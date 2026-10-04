import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ToastProvider, useToast } from "./Toast";

function Trigger() {
  const toast = useToast();
  return (
    <button type="button" onClick={() => toast.notify("Готово", "ok")}>
      показать
    </button>
  );
}

describe("Toast", () => {
  it("показывает сообщение", async () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "показать" }));

    expect(screen.getByText("Готово")).toBeInTheDocument();
  });

  it("useToast вне провайдера сообщает об ошибке разработчика", () => {
    expect(() => render(<Trigger />)).toThrow("useToast");
  });
});
