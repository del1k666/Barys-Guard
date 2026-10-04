import { describe, expect, it } from "vitest";

import { shortJson } from "./format";

describe("shortJson", () => {
  it("показывает прочерк для пустого результата", () => {
    expect(shortJson(null)).toBe("—");
    expect(shortJson(undefined)).toBe("—");
  });

  it("не трогает короткое", () => {
    expect(shortJson({ ok: true })).toBe('{"ok":true}');
  });

  it("обрезает длинное с многоточием", () => {
    const cut = shortJson({ text: "x".repeat(200) }, 20);
    expect(cut).toHaveLength(20);
    expect(cut.endsWith("…")).toBe(true);
  });
});
