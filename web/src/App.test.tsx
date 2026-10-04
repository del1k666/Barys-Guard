import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { FORCED, json, mockApi, OPERATOR, renderPage } from "./test/utils";

const OVERVIEW = {
  agents: { total: 1, active: 1, offline: 0, pending: 0, quarantined: 0, revoked: 0 },
  certificates_expiring: 0,
  tokens_active: 0,
  commands: { queued: 0, failed_24h: 0 },
  agent_versions: [],
  operating_systems: [],
};

describe("App", () => {
  it("неавторизованного уводит на вход", async () => {
    mockApi({ "GET /auth/me": json(401, { detail: "authentication required" }) });
    renderPage(<App />, { route: "/agents" });

    expect(await screen.findByRole("heading", { name: "Вход в консоль" })).toBeInTheDocument();
  });

  it("вошедшему показывает обзор внутри оболочки", async () => {
    mockApi({ "GET /auth/me": json(200, OPERATOR), "GET /overview": json(200, OVERVIEW) });
    renderPage(<App />, { route: "/" });

    expect(await screen.findByRole("heading", { name: "Обзор" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Агенты" })).toBeInTheDocument();
  });

  it("временный пароль запирает на экране смены", async () => {
    mockApi({ "GET /auth/me": json(200, FORCED) });
    renderPage(<App />, { route: "/agents" });

    expect(await screen.findByRole("heading", { name: "Смена пароля" })).toBeInTheDocument();
  });

  it("неизвестный адрес ведёт на главную", async () => {
    mockApi({ "GET /auth/me": json(200, OPERATOR), "GET /overview": json(200, OVERVIEW) });
    renderPage(<App />, { route: "/nowhere" });

    expect(await screen.findByRole("heading", { name: "Обзор" })).toBeInTheDocument();
  });
});
