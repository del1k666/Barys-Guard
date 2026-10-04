import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { AgentListPage } from "./AgentListPage";

const AGENT = {
  id: "a1",
  hostname: "ws-01",
  os: "windows",
  os_version: "11",
  arch: "amd64",
  agent_version: "0.3.1",
  status: "active",
  group_id: "g1",
  group_name: "Бухгалтерия",
  last_heartbeat_at: new Date().toISOString(),
  last_ip: "10.0.0.5",
  clock_skew_ms: 0,
  config_version: 3,
  enrolled_at: "2026-09-01T10:00:00Z",
};

const GROUP = { id: "g1", name: "Бухгалтерия", parent_id: null, agent_count: 1 };

function agentsPage(items: unknown[], total = items.length) {
  return json(200, { items, total, limit: 25, offset: 0 });
}

function setup(agents: Response) {
  return mockApi({
    "GET /auth/me": json(200, OPERATOR),
    "GET /groups": json(200, [GROUP]),
    "GET /agents": agents,
  });
}

const route = (search = "") => ({ route: `/agents${search}`, path: "/agents" });

async function rowOf(hostname: string): Promise<HTMLElement> {
  const link = await screen.findByRole("link", { name: hostname });
  return link.closest("tr") as HTMLElement;
}

describe("AgentListPage", () => {
  it("рисует строки и ведёт на карточку", async () => {
    setup(agentsPage([AGENT]));
    renderPage(<AgentListPage />, route());

    const link = await screen.findByRole("link", { name: "ws-01" });
    expect(link).toHaveAttribute("href", "/agents/a1");

    const row = await rowOf("ws-01");
    expect(within(row).getByText("Активен")).toBeInTheDocument();
    expect(within(row).getByText("Бухгалтерия")).toBeInTheDocument();
    expect(within(row).getByText("0.3.1")).toBeInTheDocument();
  });

  it("берёт фильтры и страницу из URL и передаёт их серверу", async () => {
    const { calls } = setup(agentsPage([AGENT], 60));
    renderPage(<AgentListPage />, route("?status=offline&q=ws&page=2"));

    await screen.findByRole("link", { name: "ws-01" });

    const search = new URLSearchParams(calls.find((call) => call.path === "GET /agents")?.search);
    expect(search.get("status")).toBe("offline");
    expect(search.get("q")).toBe("ws");
    expect(search.get("limit")).toBe("25");
    expect(search.get("offset")).toBe("25");
  });

  it("выбор статуса пишется в URL и сбрасывает страницу", async () => {
    setup(agentsPage([AGENT]));
    renderPage(<AgentListPage />, route("?page=2"));
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.selectOptions(screen.getByLabelText("Статус"), "offline");

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/agents\?status=offline$/),
    );
  });

  it("поиск по Enter пишется в URL", async () => {
    setup(agentsPage([AGENT]));
    renderPage(<AgentListPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.type(screen.getByLabelText("Поиск по имени хоста"), "srv{Enter}");

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/agents\?q=srv$/),
    );
  });

  it("переход на следующую страницу пишется в URL", async () => {
    setup(agentsPage([AGENT], 60));
    renderPage(<AgentListPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.click(screen.getByRole("button", { name: "Вперёд" }));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/agents\?page=2$/),
    );
  });

  it("пустой парк объясняется иначе, чем пустой результат фильтра", async () => {
    setup(agentsPage([]));
    const first = renderPage(<AgentListPage />, route());
    expect(await screen.findByText("Агентов пока нет")).toBeInTheDocument();
    first.unmount();

    setup(agentsPage([]));
    renderPage(<AgentListPage />, route("?status=revoked"));
    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сбросить фильтры" })).toBeInTheDocument();
  });

  it("номер страницы за пределами списка предлагает вернуться к первой", async () => {
    setup(agentsPage([], 5));
    renderPage(<AgentListPage />, route("?page=999"));

    expect(await screen.findByText("На этой странице агентов нет")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "На первую страницу" }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/agents$/));
  });

  it("агент без связи и без группы показывается без Invalid Date", async () => {
    setup(
      agentsPage([
        { ...AGENT, status: "pending", last_heartbeat_at: null, group_id: null, group_name: null },
      ]),
    );
    renderPage(<AgentListPage />, route());

    const row = await rowOf("ws-01");
    expect(within(row).getByText("Без группы")).toBeInTheDocument();
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("Invalid");
  });

  it("имя хоста с разметкой выводится буквально", async () => {
    setup(agentsPage([{ ...AGENT, hostname: "<b>evil</b>" }]));
    renderPage(<AgentListPage />, route());

    expect(await screen.findByText("<b>evil</b>")).toBeInTheDocument();
    expect(document.querySelector("b")).toBeNull();
  });

  it("группа из URL, которой нет среди загруженных, остаётся выбранной в фильтре", async () => {
    setup(agentsPage([AGENT]));
    renderPage(<AgentListPage />, route("?group_id=ghost"));

    await screen.findByRole("link", { name: "ws-01" });
    const select = screen.getByLabelText("Группа") as HTMLSelectElement;
    expect(select).toHaveValue("ghost");
    expect(select.selectedOptions[0]).toHaveTextContent("ghost");
  });
});
