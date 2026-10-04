/**
 * Единственное место, знающее про HTTP.
 *
 * Всё остальное приложение работает с обещаниями и типами; коды ответов,
 * разбор ошибок валидации и потеря сессии разбираются здесь. Разведи это
 * по компонентам — и обработка 401 разойдётся между экранами.
 */

const BASE = "/api/v1";

export type QueryValue = string | number | boolean | undefined | null;

export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: Record<string, string>;

  constructor(status: number, message: string, fieldErrors: Record<string, string> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

type Listener = () => void;

let sessionLost: Listener = () => {};

/** Кто узнаёт о потерянной сессии. Обычно — корень приложения. */
export function onUnauthorized(listener: Listener): void {
  sessionLost = listener;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = `${BASE}${path}`;
  if (!query) return url;

  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    // Пустые параметры не попадают в строку запроса: иначе сервер получит
    // status= и будет искать агентов с пустым статусом.
    if (value === undefined || value === null || value === "") continue;
    search.append(key, String(value));
  }

  const rendered = search.toString();
  return rendered ? `${url}?${rendered}` : url;
}

interface ValidationItem {
  loc?: unknown[];
  msg?: string;
}

function parseDetail(status: number, body: unknown): ApiError {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;

    if (typeof detail === "string") {
      return new ApiError(status, detail);
    }

    if (Array.isArray(detail)) {
      const fieldErrors: Record<string, string> = {};
      for (const item of detail as ValidationItem[]) {
        // loc — это путь вида ["body", "new_password"]; интересен хвост.
        const field = Array.isArray(item.loc) ? String(item.loc[item.loc.length - 1]) : "";
        if (field && item.msg) fieldErrors[field] = item.msg;
      }
      return new ApiError(status, "проверьте заполненные поля", fieldErrors);
    }
  }

  return new ApiError(status, `ошибка сервера (${status})`);
}

async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; query?: Record<string, QueryValue> } = {},
): Promise<T> {
  const response = await fetch(buildUrl(path, options.query), {
    method,
    // Cookie сессии ходит только с этим флагом.
    credentials: "include",
    headers: options.body === undefined ? {} : { "content-type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }

    // Неудачный вход — не потеря сессии: уводить с экрана входа некуда.
    // 401 на /auth/me — обычный ответ неавторизованному браузеру; сброс
    // кеша посреди этого запроса оставил бы сессию в вечной загрузке.
    if (response.status === 401 && !path.startsWith("/auth/login") && path !== "/auth/me") {
      sessionLost();
    }

    throw parseDetail(response.status, body);
  }

  if (response.status === 204) return null as T;

  try {
    return (await response.json()) as T;
  } catch {
    return null as T;
  }
}

export const api = {
  get: <T>(path: string, query?: Record<string, QueryValue>) =>
    request<T>("GET", path, { query }),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, { body }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, { body }),
  delete: <T>(path: string) => request<T>("DELETE", path),
};
