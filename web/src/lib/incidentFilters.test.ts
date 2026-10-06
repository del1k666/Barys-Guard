import { describe, expect, it } from "vitest";

import { parseIncidentFilters, toIncidentSearchParams } from "./incidentFilters";

describe("parseIncidentFilters", () => {
  it("пустой адрес даёт пустые фильтры и живое обновление", () => {
    expect(parseIncidentFilters(new URLSearchParams())).toEqual({
      status: "",
      severity: "",
      agentId: "",
      live: true,
    });
  });

  it("читает все поля", () => {
    const params = new URLSearchParams("status=open&severity=high&agent_id=a1&live=0");
    expect(parseIncidentFilters(params)).toEqual({
      status: "open",
      severity: "high",
      agentId: "a1",
      live: false,
    });
  });

  it("неизвестные статус и критичность из адресной строки отбрасываются", () => {
    const filters = parseIncidentFilters(new URLSearchParams("status=weird&severity=huge"));
    expect(filters.status).toBe("");
    expect(filters.severity).toBe("");
  });
});

describe("toIncidentSearchParams", () => {
  it("не пишет пустые значения и живое обновление по умолчанию", () => {
    const filters = parseIncidentFilters(new URLSearchParams());
    expect(toIncidentSearchParams(filters).toString()).toBe("");
  });

  it("выключенное живое обновление попадает в адрес", () => {
    const filters = { ...parseIncidentFilters(new URLSearchParams()), live: false };
    expect(toIncidentSearchParams(filters).get("live")).toBe("0");
  });

  it("возвращает то, что разобрал", () => {
    const again = toIncidentSearchParams(
      parseIncidentFilters(new URLSearchParams("status=closed&severity=low&agent_id=a2")),
    );
    expect(again.get("status")).toBe("closed");
    expect(again.get("severity")).toBe("low");
    expect(again.get("agent_id")).toBe("a2");
  });
});
