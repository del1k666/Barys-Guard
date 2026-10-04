import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { AgentDetailPage } from "./AgentDetailPage";

const DETAIL = {
  id: "a1",
  hostname: "ws-01",
  os: "windows",
  os_version: "11",
  arch: "amd64",
  agent_version: "0.3.1",
  status: "active",
  group_id: "g1",
  group_name: "Бухгалтерия",
  last_heartbeat_at: "2026-10-04T10:00:00Z",
  last_ip: "10.0.0.5",
  clock_skew_ms: -1500,
  config_version: 3,
  enrolled_at: "2026-09-01T10:00:00Z",
  machine_id: "m-123",
  tags: { floor: "2" },
  certificate: {
    id: "c1",
    serial: "0A1B",
    fingerprint_sha256: "ab:cd",
    not_before: "2026-09-01T10:00:00Z",
    not_after: "2027-09-01T10:00:00Z",
    issued_at: "2026-09-01T10:00:00Z",
    revoked_at: null,
    revocation_reason: null,
  },
};

const COMMAND = {
  id: "k1",
  type: "ping",
  status: "done",
  payload: {},
  result: { pong: true },
  created_at: "2026-10-04T09:00:00Z",
  sent_at: "2026-10-04T09:00:05Z",
  completed_at: "2026-10-04T09:00:10Z",
  expires_at: "2026-10-04T10:00:00Z",
  agent_id: "a1",
  hostname: "ws-01",
};

type Routes = Parameters<typeof mockApi>[0];

function setup(user: typeof OPERATOR, extra: Routes = {}) {
  return mockApi({
    "GET /auth/me": json(200, user),
    "GET /agents/a1": json(200, DETAIL),
    "GET /commands": json(200, { items: [COMMAND], total: 1, limit: 20, offset: 0 }),
    ...extra,
  });
}

const where = { route: "/agents/a1", path: "/agents/:id" };

describe("AgentDetailPage", () => {
  it("показывает сведения, сертификат и историю команд", async () => {
    const { calls } = setup(OPERATOR);
    renderPage(<AgentDetailPage />, where);

    expect(await screen.findByRole("heading", { name: "ws-01" })).toBeInTheDocument();
    expect(screen.getByText("10.0.0.5")).toBeInTheDocument();
    expect(screen.getByText("−1.5 с")).toBeInTheDocument();
    expect(screen.getByText("0A1B")).toBeInTheDocument();
    expect(await screen.findByText("Проверка связи")).toBeInTheDocument();
    expect(screen.getByText('{"pong":true}')).toBeInTheDocument();

    const history = calls.find((call) => call.path === "GET /commands");
    expect(new URLSearchParams(history?.search).get("agent_id")).toBe("a1");
  });

  it("агент без сертификата не ломает карточку", async () => {
    setup(OPERATOR, { "GET /agents/a1": json(200, { ...DETAIL, certificate: null }) });
    renderPage(<AgentDetailPage />, where);

    expect(await screen.findByText("Сертификат не выдан")).toBeInTheDocument();
  });

  it("несуществующий агент показывает сообщение, а не пустой экран", async () => {
    setup(OPERATOR, { "GET /agents/a1": json(404, { detail: "agent not found" }) });
    renderPage(<AgentDetailPage />, where);

    expect(await screen.findByText("Запрошенный объект не найден или недоступен.")).toBeInTheDocument();
  });

  it("отправляет выбранную команду", async () => {
    const { calls } = setup(OPERATOR, {
      "POST /agents/a1/commands": json(201, { ...COMMAND, id: "k2", status: "queued" }),
    });
    renderPage(<AgentDetailPage />, where);

    await userEvent.click(await screen.findByRole("button", { name: "Отправить команду" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Тип команды"), "refresh_config");
    await userEvent.click(within(dialog).getByRole("button", { name: "Отправить" }));

    expect(await screen.findByText("Команда поставлена в очередь")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const sent = calls.find((call) => call.path === "POST /agents/a1/commands");
    expect(sent?.body).toEqual({ type: "refresh_config" });
  });

  it("оператору отзыв не предлагается", async () => {
    setup(OPERATOR);
    renderPage(<AgentDetailPage />, where);

    await screen.findByRole("heading", { name: "ws-01" });
    expect(screen.queryByRole("button", { name: "Отозвать агента" })).not.toBeInTheDocument();
  });

  it("у отозванного агента нет действий", async () => {
    setup(ADMIN, { "GET /agents/a1": json(200, { ...DETAIL, status: "revoked" }) });
    renderPage(<AgentDetailPage />, where);

    await screen.findByRole("heading", { name: "ws-01" });
    expect(screen.queryByRole("button", { name: "Отозвать агента" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отправить команду" })).not.toBeInTheDocument();
  });

  it("отзыв требует причину и подтверждения", async () => {
    const { calls } = setup(ADMIN, { "POST /agents/a1/revoke": json(204, null) });
    renderPage(<AgentDetailPage />, where);

    await userEvent.click(await screen.findByRole("button", { name: "Отозвать агента" }));
    const dialog = screen.getByRole("dialog", { name: "Отозвать агента?" });
    expect(within(dialog).getByText(/ws-01/)).toBeInTheDocument();

    const confirm = within(dialog).getByRole("button", { name: "Отозвать" });
    expect(confirm).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText("Причина отзыва"), "хост утерян");
    await userEvent.click(confirm);

    expect(await screen.findByText("Агент отозван")).toBeInTheDocument();
    const revoke = calls.find((call) => call.path === "POST /agents/a1/revoke");
    expect(revoke?.body).toEqual({ reason: "хост утерян" });
  });

  it("отмена отзыва не шлёт запрос", async () => {
    const { calls } = setup(ADMIN, { "POST /agents/a1/revoke": json(204, null) });
    renderPage(<AgentDetailPage />, where);

    await userEvent.click(await screen.findByRole("button", { name: "Отозвать агента" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Отмена" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((call) => call.path === "POST /agents/a1/revoke")).toBe(false);
  });

  it("пока ответ на отзыв не пришёл, подтверждение заблокировано", async () => {
    let release: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const { calls } = setup(ADMIN, { "POST /agents/a1/revoke": () => pending });
    renderPage(<AgentDetailPage />, where);

    await userEvent.click(await screen.findByRole("button", { name: "Отозвать агента" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Причина отзыва"), "хост утерян");
    await userEvent.click(within(dialog).getByRole("button", { name: "Отозвать" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Отозвать" })).toBeDisabled(),
    );
    expect(calls.filter((call) => call.path === "POST /agents/a1/revoke")).toHaveLength(1);

    release(json(204, null));
    expect(await screen.findByText("Агент отозван")).toBeInTheDocument();
  });
});
