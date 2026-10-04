import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("на первой странице нельзя идти назад и можно вперёд", async () => {
    const onChange = vi.fn();
    render(<Pagination total={120} limit={25} offset={0} onChange={onChange} />);

    expect(screen.getByText("1–25 из 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    expect(onChange).toHaveBeenCalledWith(25);
  });

  it("на последней странице нельзя идти вперёд", async () => {
    const onChange = vi.fn();
    render(<Pagination total={120} limit={25} offset={100} onChange={onChange} />);

    expect(screen.getByText("101–120 из 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Вперёд" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Назад" }));
    expect(onChange).toHaveBeenCalledWith(75);
  });

  it("при пустом списке ничего не рисует", () => {
    const { container } = render(<Pagination total={0} limit={25} offset={0} onChange={() => {}} />);

    expect(container).toBeEmptyDOMElement();
  });
});
