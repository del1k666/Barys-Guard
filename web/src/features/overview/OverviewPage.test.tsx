import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { OverviewPage } from "./OverviewPage";

const OVERVIEW = {
  agents: { total: 12, active: 8, offline: 3, pending: 1, quarantined: 0, revoked: 0 },
  certificates_expiring: 2,
  tokens_active: 4,
  commands: { queued: 5, failed_24h: 1 },
  agent_versions: [
    { value: "0.3.1", count: 9 },
    { value: "0.2.0", count: 3 },
  ],
  operating_systems: [{ value: "windows", count: 10 }],
};

const where = { route: "/", path: "/" };

describe("OverviewPage", () => {
  it("показывает числа парка, команд и распределения", async () => {
    mockApi({ "GET /auth/me": json(200, OPERATOR), "GET /overview": json(200, OVERVIEW) });

    renderPage(<OverviewPage />, where);

    expect(await screen.findByText("12")).toBeInTheDocument();
    expect(screen.getByText("Не в сети").closest("a")).toHaveAttribute("href", "/agents?status=offline");
    expect(screen.getByText("Всего").closest("a")).toHaveAttribute("href", "/agents");
    expect(screen.getByText("Неудачные за 24 часа").parentElement).toHaveTextContent("1");
    expect(screen.getByText("0.3.1").parentElement).toHaveTextContent("9");
    expect(screen.getByText("windows")).toBeInTheDocument();
  });

  it("при сбое предлагает повторить", async () => {
    mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /overview": json(500, { detail: "boom" }),
    });

    renderPage(<OverviewPage />, where);

    expect(await screen.findByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });
});
