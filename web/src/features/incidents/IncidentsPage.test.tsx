import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { IncidentsPage } from "./IncidentsPage";

const ID = "99999999-9999-9999-9999-999999999999";

const INCIDENT = {
  id: ID,
  agent_id: "a1",
  hostname: "ws-01",
  artifact_sha256: "cd".repeat(32),
  title: "Персональные данные в файле",
  severity: "high",
  score: 80,
  status: "open",
  events_count: 3,
  first_event_at: "2026-10-05T11:00:00Z",
  last_event_at: "2026-10-05T12:00:00Z",
  assignee: null,
};

const SECOND = {
  ...INCIDENT,
  id: "88888888-8888-8888-8888-888888888888",
  hostname: "ws-02",
  title: "Гриф в документе",
};

const DETAIL = {
  ...INCIDENT,
  verdict: { status: "flagged", score: 80, severity: "high" },
  matches: [
    { rule_key: "iin_bin", count: 2, points: 60, samples: ["**********17", "**********42"] },
    { rule_key: "card", count: 1, points: 20, samples: ["************1111"] },
    { rule_key: "custom_rule", count: 1, points: 5, samples: [] },
  ],
  events: [
    {
      event_id: "e1",
      occurred_at: "2026-10-05T12:00:00Z",
      action: "copy",
      severity: "high",
      dst_path: "E:\\отчёт.xlsx",
    },
    {
      event_id: "e2",
      occurred_at: "2026-10-05T11:00:00Z",
      action: "create",
      severity: "high",
      dst_path: null,
    },
  ],
};

function incidentPage(items: unknown[], nextCursor: string | null = null) {
  return json(200, { items, next_cursor: nextCursor });
}

const route = (search = "") => ({ route: `/incidents${search}`, path: "/incidents" });

function setup(list: Response | ((call: { search: string }) => Response)) {
  return mockApi({
    "GET /auth/me": json(200, OPERATOR),
    "GET /incidents": list as Response,
    [`GET /incidents/${ID}`]: json(200, DETAIL),
  });
}

async function openPanel() {
  await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));
  return screen.findByRole("dialog");
}

describe("IncidentsPage", () => {
  it("рисует строку инцидента: агент, критичность, оценка, статус, число событий", async () => {
    setup(incidentPage([INCIDENT]));
    renderPage(<IncidentsPage />, route());

    const link = await screen.findByRole("link", { name: "ws-01" });
    expect(link).toHaveAttribute("href", "/agents/a1");

    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Высокая")).toBeInTheDocument();
    expect(within(row).getByText("80")).toBeInTheDocument();
    expect(within(row).getByText("Персональные данные в файле")).toBeInTheDocument();
    expect(within(row).getByText("Открыт")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
  });

  it("передаёт фильтры из адреса серверу", async () => {
    const { calls } = setup(incidentPage([INCIDENT]));
    renderPage(<IncidentsPage />, route("?status=open&severity=high&agent_id=a1"));

    await screen.findByRole("link", { name: "ws-01" });

    const search = new URLSearchParams(calls.find((call) => call.path === "GET /incidents")?.search);
    expect(search.get("status")).toBe("open");
    expect(search.get("severity")).toBe("high");
    expect(search.get("agent_id")).toBe("a1");
    expect(search.get("limit")).toBe("50");
    expect(screen.getByText("Показаны инциденты одного агента")).toBeInTheDocument();
  });

  it("выбор статуса уходит на сервер и пишется в адрес", async () => {
    const { calls } = setup(incidentPage([INCIDENT]));
    renderPage(<IncidentsPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.selectOptions(screen.getByLabelText("Статус"), "closed");

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("status=closed"));
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.path === "GET /incidents" &&
            new URLSearchParams(call.search).get("status") === "closed",
        ),
      ).toBe(true),
    );
  });

  it("выключение живого обновления пишется в адрес", async () => {
    setup(incidentPage([INCIDENT]));
    renderPage(<IncidentsPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.click(screen.getByLabelText("Живое обновление"));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("live=0"));
  });

  it("«Показать ещё» просит следующую страницу по курсору и скрывается в конце", async () => {
    const { calls } = mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /incidents": (call) =>
        new URLSearchParams(call.search).get("cursor") === "c1"
          ? incidentPage([SECOND])
          : incidentPage([INCIDENT], "c1"),
    });
    renderPage(<IncidentsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Показать ещё" }));

    await screen.findByRole("link", { name: "ws-02" });
    expect(screen.getByRole("link", { name: "ws-01" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Показать ещё" })).not.toBeInTheDocument();
    expect(
      calls
        .filter((call) => call.path === "GET /incidents")
        .map((call) => new URLSearchParams(call.search).get("cursor")),
    ).toContain("c1");
  });

  it("пустой список объясняет, что инцидентов нет", async () => {
    setup(incidentPage([]));
    renderPage(<IncidentsPage />, route());

    expect(await screen.findByText("Инцидентов пока нет")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сбросить фильтры" })).not.toBeInTheDocument();
  });

  it("пустой результат при фильтре предлагает сбросить фильтры", async () => {
    setup(incidentPage([]));
    renderPage(<IncidentsPage />, route("?status=closed"));

    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Сбросить фильтры" }));
    await waitFor(() => expect(screen.getByTestId("location")).not.toHaveTextContent("status="));
  });

  it("ошибка сервера показывает повтор", async () => {
    setup(json(500, { detail: "boom" }));
    renderPage(<IncidentsPage />, route());

    expect(await screen.findByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });
});

describe("IncidentDetailPanel", () => {
  it("показывает вердикт, совпадения с маскированными образцами и события", async () => {
    setup(incidentPage([INCIDENT]));
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    expect(await within(dialog).findByText("Опасно")).toBeInTheDocument();
    expect(within(dialog).getByText("ИИН/БИН")).toBeInTheDocument();
    expect(within(dialog).getByText("Банковская карта")).toBeInTheDocument();
    expect(within(dialog).getByText("custom_rule")).toBeInTheDocument();
    expect(within(dialog).getByText("**********17, **********42")).toBeInTheDocument();
    expect(within(dialog).getByText("************1111")).toBeInTheDocument();
    expect(within(dialog).getByText("copy")).toBeInTheDocument();
    expect(within(dialog).getByText("E:\\отчёт.xlsx")).toBeInTheDocument();
    expect(within(dialog).getByText("cd".repeat(32))).toBeInTheDocument();
  });

  it("«Принять» шлёт PATCH, показывает новый статус и прячет «Принять»", async () => {
    let status = "open";
    const { calls } = mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /incidents": () => incidentPage([{ ...INCIDENT, status }]),
      [`GET /incidents/${ID}`]: () => json(200, { ...DETAIL, status }),
      [`PATCH /incidents/${ID}`]: (call) => {
        status = (call.body as { status: string }).status;
        return json(200, { ...DETAIL, status });
      },
    });
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    await userEvent.click(await within(dialog).findByRole("button", { name: "Принять" }));

    expect(await screen.findByText("Инцидент принят")).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByText("Принят")).toBeInTheDocument());
    expect(within(dialog).queryByRole("button", { name: "Принять" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Закрыть" })).toBeInTheDocument();

    expect(calls.find((call) => call.path === `PATCH /incidents/${ID}`)?.body).toEqual({
      status: "acknowledged",
    });
    // Список перечитан: в таблице тоже новый статус.
    await waitFor(() => {
      const row = within(screen.getByRole("table", { name: "Список инцидентов" })).getByRole("link", { name: "ws-01" }).closest("tr") as HTMLElement;
      expect(within(row).getByText("Принят")).toBeInTheDocument();
    });
  });

  it("«Закрыть» закрывает инцидент и убирает обе кнопки", async () => {
    let status = "open";
    const { calls } = mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /incidents": () => incidentPage([{ ...INCIDENT, status }]),
      [`GET /incidents/${ID}`]: () => json(200, { ...DETAIL, status }),
      [`PATCH /incidents/${ID}`]: () => {
        status = "closed";
        return json(200, { ...DETAIL, status });
      },
    });
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    await userEvent.click(await within(dialog).findByRole("button", { name: "Закрыть" }));

    expect(await screen.findByText("Инцидент закрыт")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(dialog).queryByRole("button", { name: "Закрыть" })).not.toBeInTheDocument(),
    );
    expect(within(dialog).queryByRole("button", { name: "Принять" })).not.toBeInTheDocument();
    expect(calls.find((call) => call.path === `PATCH /incidents/${ID}`)?.body).toEqual({
      status: "closed",
    });
  });

  it("409 показывает ошибку и оставляет панель открытой", async () => {
    mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /incidents": incidentPage([INCIDENT]),
      [`GET /incidents/${ID}`]: json(200, DETAIL),
      [`PATCH /incidents/${ID}`]: json(409, { detail: "инцидент закрыт" }),
    });
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    await userEvent.click(await within(dialog).findByRole("button", { name: "Принять" }));

    expect(await screen.findByText("инцидент закрыт")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Принять" })).toBeEnabled(),
    );
  });

  it("у закрытого инцидента кнопок действий нет", async () => {
    mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /incidents": incidentPage([{ ...INCIDENT, status: "closed" }]),
      [`GET /incidents/${ID}`]: json(200, { ...DETAIL, status: "closed" }),
    });
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    await within(dialog).findByText("Опасно");
    expect(within(dialog).queryByRole("button", { name: "Принять" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Закрыть" })).not.toBeInTheDocument();
  });

  it("ошибка загрузки деталей показывается внутри панели", async () => {
    mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /incidents": incidentPage([INCIDENT]),
      [`GET /incidents/${ID}`]: json(500, { detail: "boom" }),
    });
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    expect(await within(dialog).findByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });
});
