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

  it("смещения — в кодовых точках: эмодзи до совпадения не сдвигают подсветку", () => {
    expect(highlight("😀 ALFA-123", [{ start: 2, end: 10 }])).toEqual([
      { text: "😀 ", hit: false },
      { text: "ALFA-123", hit: true },
    ]);
  });

  it("эмодзи внутри совпадения и несколько совпадений", () => {
    expect(
      highlight("😀😀 a😀b c 😀 d", [
        { start: 3, end: 6 },
        { start: 11, end: 12 },
      ]),
    ).toEqual([
      { text: "😀😀 ", hit: false },
      { text: "a😀b", hit: true },
      { text: " c 😀 ", hit: false },
      { text: "d", hit: true },
    ]);
  });

  it("выход за границы в кодовых точках обрезается без падения", () => {
    expect(highlight("😀ab", [{ start: 1, end: 99 }, { start: 50, end: 60 }])).toEqual([
      { text: "😀", hit: false },
      { text: "ab", hit: true },
    ]);
  });
});
