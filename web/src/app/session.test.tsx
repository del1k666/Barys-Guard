import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequireAuth } from "./RequireAuth";
import { SessionProvider } from "./session";

function json(status: number, body: unknown): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

const OPERATOR = {
  id: "11111111-1111-1111-1111-111111111111",
  username: "ivanov",
  role: "operator",
  scope_group_id: null,
  must_change_password: false,
};

function wrap(children: ReactNode, initial = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <SessionProvider>
          <Routes>
            <Route path="/login" element={<div>экран входа</div>} />
            <Route path="/password" element={<div>смена пароля</div>} />
            <Route path="/" element={<RequireAuth>{children}</RequireAuth>} />
          </Routes>
        </SessionProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("сессия консоли", () => {
  it("пускает вошедшего оператора к содержимому", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, OPERATOR)));

    wrap(<div>список агентов</div>);

    expect(await screen.findByText("список агентов")).toBeInTheDocument();
  });

  it("уводит на вход, когда сессии нет", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(401, { detail: "authentication required" })));

    wrap(<div>список агентов</div>);

    expect(await screen.findByText("экран входа")).toBeInTheDocument();
    expect(screen.queryByText("список агентов")).not.toBeInTheDocument();
  });

  it("требует сменить временный пароль прежде всего", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json(200, { ...OPERATOR, must_change_password: true })),
    );

    wrap(<div>список агентов</div>);

    // Оператор с временным паролем не должен работать в консоли:
    // такой пароль знает ещё и тот, кто его выдал.
    expect(await screen.findByText("смена пароля")).toBeInTheDocument();
  });

  it("не показывает содержимое, пока личность не подтверждена", async () => {
    // Запрос, который никогда не отвечает: проверяем именно промежуточное состояние.
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    wrap(<div>список агентов</div>);

    expect(screen.queryByText("список агентов")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("выбрасывает на вход, когда сессия истекла посреди работы", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, OPERATOR))
      .mockResolvedValue(json(401, { detail: "session expired" }));
    vi.stubGlobal("fetch", fetchMock);

    function Breaker() {
      return (
        <button
          onClick={() => {
            void import("../api/client").then(({ api }) => api.get("/agents").catch(() => {}));
          }}
        >
          запросить
        </button>
      );
    }

    wrap(<Breaker />);

    await userEvent.click(await screen.findByRole("button", { name: "запросить" }));

    await waitFor(() => expect(screen.getByText("экран входа")).toBeInTheDocument());
  });
});
