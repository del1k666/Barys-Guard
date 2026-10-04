import { describe, expect, it } from "vitest";

import { formatDateTime, formatSkew, relativeTime } from "./format";

const NOW = new Date("2026-09-15T12:00:00Z");

describe("относительное время", () => {
  it("описывает свежие отметки секундами", () => {
    expect(relativeTime("2026-09-15T11:59:45Z", NOW)).toBe("15 секунд назад");
  });

  it("описывает минуты и часы", () => {
    expect(relativeTime("2026-09-15T11:40:00Z", NOW)).toBe("20 минут назад");
    expect(relativeTime("2026-09-15T09:00:00Z", NOW)).toBe("3 часа назад");
  });

  it("склоняет числительные по-русски", () => {
    expect(relativeTime("2026-09-15T11:59:59Z", NOW)).toBe("1 секунду назад");
    expect(relativeTime("2026-09-15T11:58:00Z", NOW)).toBe("2 минуты назад");
    expect(relativeTime("2026-09-15T11:55:00Z", NOW)).toBe("5 минут назад");
  });

  it("не показывает будущее как прошлое", () => {
    // Часы агента могут спешить: heartbeat из будущего — обычное дело.
    expect(relativeTime("2026-09-15T12:00:30Z", NOW)).toBe("только что");
  });

  it("отвечает прочерком на отсутствие отметки", () => {
    expect(relativeTime(null, NOW)).toBe("—");
  });
});

describe("дрейф часов", () => {
  it("показывает знак и единицы", () => {
    expect(formatSkew(0)).toBe("0 мс");
    expect(formatSkew(-1500)).toBe("−1.5 с");
    expect(formatSkew(2500)).toBe("+2.5 с");
    expect(formatSkew(450)).toBe("+450 мс");
  });
});

describe("дата и время", () => {
  it("отвечает прочерком на пустое значение", () => {
    expect(formatDateTime(null)).toBe("—");
  });

  it("печатает дату полностью", () => {
    expect(formatDateTime("2026-09-15T12:00:00Z")).toMatch(/2026/);
  });
});
