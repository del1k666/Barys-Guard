import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";
import { EmptyState, ErrorState } from "./States";

describe("состояния", () => {
  it("EmptyState показывает заголовок и подсказку", () => {
    render(<EmptyState title="Пусто" hint="Добавьте что-нибудь" />);

    expect(screen.getByText("Пусто")).toBeInTheDocument();
    expect(screen.getByText("Добавьте что-нибудь")).toBeInTheDocument();
  });

  it("ErrorState предлагает повтор при сбое сервера", async () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new ApiError(502, "ошибка сервера (502)")} onRetry={onRetry} />);

    expect(screen.getByText("ошибка сервера (502)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: ru.common.retry }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("ErrorState не предлагает повтор там, где он бесполезен", () => {
    render(<ErrorState error={new ApiError(403, "forbidden")} onRetry={() => {}} />);

    expect(screen.getByText(ru.errors.forbidden)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
