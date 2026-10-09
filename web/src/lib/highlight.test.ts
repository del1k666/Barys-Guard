import { describe, expect, it } from "vitest";

import { highlight } from "./highlight";

describe("highlight", () => {
  it("режет текст по совпадениям", () => {
    expect(highlight("a 123 b 456", [{ start: 2, end: 5 }, { start: 8, end: 11 }])).toEqual([
      { text: "a ", hit: false },
      { text: "123", hit: true },
      { text: " b ", hit: false },
      { text: "456", hit: true },
    ]);
  });

  it("без совпадений возвращает весь текст, пустой текст — пустой список", () => {
    expect(highlight("abc", [])).toEqual([{ text: "abc", hit: false }]);
    expect(highlight("", [])).toEqual([]);
  });

  it("игнорирует перекрытия, пустые и вышедшие за границы совпадения", () => {
    const parts = highlight("abcdef", [
      { start: 1, end: 4 },
      { start: 2, end: 5 },
      { start: 3, end: 3 },
      { start: 5, end: 99 },
      { start: 70, end: 80 },
    ]);
    expect(parts.map((p) => p.text).join("")).toBe("abcdef");
    expect(parts.filter((p) => p.hit).map((p) => p.text)).toEqual(["bcd", "f"]);
  });
});
