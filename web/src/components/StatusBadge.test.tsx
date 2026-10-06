import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AgentStatusBadge,
  CommandStatusBadge,
  IncidentStatusBadge,
  VerdictBadge,
} from "./StatusBadge";

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

  it("переводит статусы инцидентов и неизвестный оставляет как есть", () => {
    render(
      <>
        <IncidentStatusBadge status="open" />
        <IncidentStatusBadge status="acknowledged" />
        <IncidentStatusBadge status="closed" />
        <IncidentStatusBadge status="reopened" />
      </>,
    );

    expect(screen.getByText("Открыт")).toBeInTheDocument();
    expect(screen.getByText("Принят")).toBeInTheDocument();
    expect(screen.getByText("Закрыт")).toBeInTheDocument();
    expect(screen.getByText("reopened")).toBeInTheDocument();
  });

  it("переводит вердикты и неизвестный оставляет как есть", () => {
    render(
      <>
        <VerdictBadge status="flagged" />
        <VerdictBadge status="clean" />
        <VerdictBadge status="not_inspected" />
        <VerdictBadge status="mystery" />
      </>,
    );

    expect(screen.getByText("Опасно")).toBeInTheDocument();
    expect(screen.getByText("Чисто")).toBeInTheDocument();
    expect(screen.getByText("Не проверено")).toBeInTheDocument();
    expect(screen.getByText("mystery")).toBeInTheDocument();
  });
});
