import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Modal } from "./Modal";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        открыть
      </button>
      <Modal
        open={open}
        title="Подтверждение"
        onClose={() => setOpen(false)}
        footer={
          <>
            <button type="button" onClick={() => setOpen(false)}>
              отмена
            </button>
            <button type="button">ок</button>
          </>
        }
      >
        <p>текст</p>
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("закрытое окно ничего не рисует", () => {
    render(<Harness />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("открывается с именем по заголовку и переносит фокус внутрь", async () => {
    render(<Harness />);

    await userEvent.click(screen.getByRole("button", { name: "открыть" }));

    expect(screen.getByRole("dialog", { name: "Подтверждение" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "отмена" })).toHaveFocus();
  });

  it("Esc закрывает окно и возвращает фокус на кнопку открытия", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "открыть" });

    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("Tab не выводит фокус за пределы окна", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "открыть" }));

    await userEvent.tab(); // отмена -> ок
    expect(screen.getByRole("button", { name: "ок" })).toHaveFocus();

    await userEvent.tab(); // ок -> снова отмена
    expect(screen.getByRole("button", { name: "отмена" })).toHaveFocus();
  });
});
