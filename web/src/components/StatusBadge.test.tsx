import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentStatusBadge, CommandStatusBadge } from "./StatusBadge";

describe("бейджи статусов", () => {
  it("переводит известные статусы", () => {
    render(
      <>
        <AgentStatusBadge status="offline" />
        <CommandStatusBadge status="failed" />
      </>,
    );

    expect(screen.getByText("Не в сети")).toBeInTheDocument();
    expect(screen.getByText("Ошибка")).toBeInTheDocument();
  });

  it("неизвестный статус от сервера показывает как есть", () => {
    render(<AgentStatusBadge status="weird" />);

    expect(screen.getByText("weird")).toBeInTheDocument();
  });
});
