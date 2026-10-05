import { describe, expect, it } from "vitest";

import { parseEventFilters, periodStart, toEventSearchParams } from "./eventFilters";

describe("parseEventFilters", () => {
  it("пустой адрес даёт пустые фильтры и живое обновление", () => {
    expect(parseEventFilters(new URLSearchParams())).toEqual({
      channel: "",
      severity: "",
      agentId: "",
      period: "",
      live: true,
    });
  });

  it("читает все поля", () => {
    const params = new URLSearchParams("channel=file&severity=high&agent_id=a1&period=24h&live=0");
    expect(parseEventFilters(params)).toEqual({
      channel: "file",
      severity: "high",
      agentId: "a1",
      period: "24h",
      live: false,
    });
  });

  it("неизвестный период из адресной строки отбрасывается", () => {
    expect(parseEventFilters(new URLSearchParams("period=вечность")).period).toBe("");
  });
});

describe("toEventSearchParams", () => {
  it("не пишет пустые значения и живое обновление по умолчанию", () => {
    const filters = parseEventFilters(new URLSearchParams());
    expect(toEventSearchParams(filters).toString()).toBe("");
  });

  it("выключенное живое обновление попадает в адрес", () => {
    const filters = { ...parseEventFilters(new URLSearchParams()), live: false };
    expect(toEventSearchParams(filters).get("live")).toBe("0");
  });

  it("возвращает то, что разобрал", () => {
    const params = new URLSearchParams("channel=usb&severity=medium&period=1h");
    const again = toEventSearchParams(parseEventFilters(params));
    expect(again.get("channel")).toBe("usb");
    expect(again.get("severity")).toBe("medium");
    expect(again.get("period")).toBe("1h");
  });
});

describe("periodStart", () => {
  const now = new Date("2026-10-05T12:00:00Z");

  it("без периода начала нет", () => {
    expect(periodStart("", now)).toBeUndefined();
  });

  it("считает назад от текущего момента", () => {
    expect(periodStart("1h", now)).toBe("2026-10-05T11:00:00.000Z");
    expect(periodStart("24h", now)).toBe("2026-10-04T12:00:00.000Z");
    expect(periodStart("7d", now)).toBe("2026-09-28T12:00:00.000Z");
  });
});
