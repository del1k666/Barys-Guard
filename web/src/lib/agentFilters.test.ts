import { describe, expect, it } from "vitest";

import { parseFilters, toSearchParams } from "./agentFilters";

describe("фильтры списка агентов", () => {
  it("читает все параметры", () => {
    const filters = parseFilters(new URLSearchParams("q=ws&status=offline&group_id=g1&page=3"));

    expect(filters).toEqual({ q: "ws", status: "offline", groupId: "g1", page: 3 });
  });

  it("пустой URL даёт значения по умолчанию", () => {
    expect(parseFilters(new URLSearchParams())).toEqual({
      q: "",
      status: "",
      groupId: "",
      page: 1,
    });
  });

  it.each(["abc", "0", "-3", "1.5", "2x", ""])("мусорная страница %j сводится к первой", (value) => {
    expect(parseFilters(new URLSearchParams({ page: value })).page).toBe(1);
  });

  it("не пишет в URL значения по умолчанию", () => {
    const params = toSearchParams({ q: "", status: "", groupId: "", page: 1 });

    expect(params.toString()).toBe("");
  });

  it("пишет заданные фильтры и страницу после первой", () => {
    const params = toSearchParams({ q: "ws", status: "offline", groupId: "g1", page: 2 });

    expect(params.get("q")).toBe("ws");
    expect(params.get("status")).toBe("offline");
    expect(params.get("group_id")).toBe("g1");
    expect(params.get("page")).toBe("2");
  });
});
