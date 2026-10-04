import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation, type InitialEntry } from "react-router-dom";
import { vi } from "vitest";

import { SessionProvider } from "../app/session";
import { ToastProvider } from "../components/Toast";

export function json(status: number, body: unknown): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

export const OPERATOR = {
  id: "11111111-1111-1111-1111-111111111111",
  username: "ivanov",
  role: "operator",
  scope_group_id: null,
  must_change_password: false,
};

export const ADMIN = { ...OPERATOR, username: "admin", role: "admin" };

export const FORCED = { ...OPERATOR, must_change_password: true };

export interface Call {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

export type Handler = Response | ((call: Call) => Response | Promise<Response>);

/**
 * Подмена сервера. Ключ — «МЕТОД /путь» без префикса /api/v1.
 * Необъявленный запрос отвечает 404, чтобы забытый маршрут не молчал.
 */
export function mockApi(routes: Record<string, Handler>): { calls: Call[] } {
  const calls: Call[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      const path = url.pathname.replace(/^\/api\/v1/, "");
      const key = `${method} ${path}`;

      const call: Call = {
        method,
        path: key,
        search: url.search,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };

      // Записываем до поиска обработчика: необъявленный запрос тоже виден
      // тесту, иначе проверка «запрос не уходил» не может провалиться.
      calls.push(call);

      const handler = routes[key];
      if (!handler) return json(404, { detail: `unmocked ${key}` });

      return typeof handler === "function" ? handler(call) : handler;
    }),
  );

  return { calls };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">
      {location.pathname}
      {location.search}
    </div>
  );
}

/** Рисует страницу в окружении приложения: запросы, маршруты, сессия, тосты. */
export function renderPage(
  ui: ReactElement,
  options: { route?: InitialEntry; path?: string } = {},
) {
  const { route = "/", path = "/*" } = options;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <ToastProvider>
          <SessionProvider>
            <Routes>
              <Route path={path} element={ui} />
              <Route path="*" element={null} />
            </Routes>
            <LocationProbe />
          </SessionProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
