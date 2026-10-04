import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, onUnauthorized } from "./client";

function respond(status: number, body: unknown = null, ok = status < 400): Response {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
  onUnauthorized(() => {});
});

describe("клиент API", () => {
  it("отправляет cookie сессии вместе с запросом", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/agents");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/agents");
    // Без credentials браузер не приложит cookie, и каждый запрос
    // окажется неаутентифицированным.
    expect(init.credentials).toBe("include");
  });

  it("сообщает наверх о потерянной сессии ровно один раз", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(401, { detail: "session expired" })));
    const lost = vi.fn();
    onUnauthorized(lost);

    await expect(api.get("/agents")).rejects.toBeInstanceOf(ApiError);
    expect(lost).toHaveBeenCalledTimes(1);
  });

  it("не считает вход потерянной сессией", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(401, { detail: "invalid" })));
    const lost = vi.fn();
    onUnauthorized(lost);

    // Неверный пароль на экране входа — это не истёкшая сессия, и уводить
    // с экрана входа на экран входа бессмысленно.
    await expect(api.post("/auth/login", { username: "a", password: "b" })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(lost).not.toHaveBeenCalled();
  });

  it("раскладывает ошибку валидации по полям", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        respond(422, {
          detail: [
            { loc: ["body", "new_password"], msg: "String should have at least 12 characters" },
          ],
        }),
      ),
    );

    const error = await api.post("/auth/password", {}).catch((caught: ApiError) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).fieldErrors.new_password).toContain("12");
  });

  it("переносит текст ошибки сервера в сообщение", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(respond(409, { detail: "group is not empty" })),
    );

    const error = await api.delete("/groups/1").catch((caught: ApiError) => caught);

    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe("group is not empty");
  });

  it("переживает ответ без тела", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 204,
        headers: new Headers(),
        json: async () => {
          throw new Error("нет тела");
        },
        text: async () => "",
      } as unknown as Response),
    );

    await expect(api.post("/auth/logout")).resolves.toBeNull();
  });

  it("собирает строку запроса из параметров, пропуская пустые", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/agents", { q: "buh", status: undefined, limit: 50, offset: 0 });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/agents?q=buh&limit=50&offset=0");
  });
});
