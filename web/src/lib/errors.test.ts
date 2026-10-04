import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";
import { describeError } from "./errors";

describe("describeError", () => {
  it("объясняет 403 и 404 по-человечески", () => {
    expect(describeError(new ApiError(403, "forbidden"))).toBe(ru.errors.forbidden);
    expect(describeError(new ApiError(404, "not found"))).toBe(ru.errors.notFound);
  });

  it("оставляет сообщение сервера для прочих статусов", () => {
    expect(describeError(new ApiError(502, "ошибка сервера (502)"))).toBe("ошибка сервера (502)");
  });

  it("узнаёт обрыв сети", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toBe(ru.errors.network);
  });

  it("не падает на неизвестном", () => {
    expect(describeError("что-то")).toBe(ru.errors.unknown);
  });
});
