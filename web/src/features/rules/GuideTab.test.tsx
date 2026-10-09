import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ru } from "../../i18n/ru";
import { GuideTab } from "./GuideTab";

describe("GuideTab", () => {
  it("выводит все разделы инструкции", () => {
    render(<GuideTab />);

    expect(ru.rules.guide.sections).toHaveLength(6);
    for (const section of ru.rules.guide.sections) {
      expect(screen.getByRole("heading", { name: section.title })).toBeInTheDocument();
    }
  });

  it("примеры шаблонов выведены моноширинным текстом", () => {
    render(<GuideTab />);

    const example = screen.getByText("ALFA-\\d{3,5}");
    expect(example.tagName).toBe("CODE");
    expect(example.className).toMatch(/mono/);
  });

  it("описывает класс букв для кириллицы и пороги вердикта", () => {
    render(<GuideTab />);

    expect(screen.getByText(/\\p\{L\} \(любая буква\)/)).toBeInTheDocument();
    expect(screen.getByText(/20–49 — средняя; 50–79 — высокая; 80 и больше — критическая/)).toBeInTheDocument();
  });
});
