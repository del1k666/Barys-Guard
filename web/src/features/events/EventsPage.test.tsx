import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { EventsPage } from "./EventsPage";

const COPY = {
  event_id: "e1",
  agent_id: "a1",
  hostname: "ws-01",
  occurred_at: "2026-10-05T12:00:00Z",
  received_at: "2026-10-05T12:00:02Z",
  channel: "file",
  action: "copy",
  severity: "high",
  actor: { user_name: "PC\\ivanov", user_sid: "S-1-5-21-1-2-3-1001" },
  process: {},
  subject: {
    src_path: "C:\\Docs\\отчёт.xlsx",
    dst_path: "E:\\отчёт.xlsx",
    size_bytes: 2048,
    volume: { type: "removable", label: "KINGSTON" },
  },
  labels: { process: "unknown" },
  artifact_sha256: "ab".repeat(32),
  artifact_uploaded: true,
};

const USB = {
  ...COPY,
  event_id: "e2",
  channel: "usb",
  action: "mount",
  severity: "info",
  subject: { drive_letter: "E:", volume: { label: "KINGSTON" } },
  artifact_sha256: null,
  artifact_uploaded: false,
};

function eventsPage(items: unknown[], nextCursor: string | null = null) {
  return json(200, { items, next_cursor: nextCursor });
}

function setup(events: Response | ((call: { search: string }) => Response)) {
  return mockApi({
    "GET /auth/me": json(200, OPERATOR),
    "GET /events": events as Response,
  });
}

const route = (search = "") => ({ route: `/events${search}`, path: "/events" });

describe("EventsPage", () => {
  it("рисует строку события с агентом, критичностью и описанием копирования", async () => {
    setup(eventsPage([COPY]));
    renderPage(<EventsPage />, route());

    const link = await screen.findByRole("link", { name: "ws-01" });
    expect(link).toHaveAttribute("href", "/agents/a1");

    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Высокая")).toBeInTheDocument();
    expect(within(row).getByText("copy")).toBeInTheDocument();
    expect(within(row).getByText("C:\\Docs\\отчёт.xlsx → E:\\отчёт.xlsx")).toBeInTheDocument();
  });

  it("передаёт фильтры из адреса серверу, период превращает в since", async () => {
    const { calls } = setup(eventsPage([COPY]));
    renderPage(<EventsPage />, route("?channel=file&severity=high&agent_id=a1&period=24h"));

    await screen.findByRole("link", { name: "ws-01" });

    const search = new URLSearchParams(calls.find((call) => call.path === "GET /events")?.search);
    expect(search.get("channel")).toBe("file");
    expect(search.get("severity")).toBe("high");
    expect(search.get("agent_id")).toBe("a1");
    expect(search.get("limit")).toBe("50");
    expect(Number.isNaN(Date.parse(search.get("since") ?? ""))).toBe(false);
  });

  it("выбор канала пишется в адрес", async () => {
    setup(eventsPage([COPY]));
    renderPage(<EventsPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.selectOptions(screen.getByLabelText("Канал"), "usb");

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("channel=usb"));
  });

  it("выключение живого обновления пишется в адрес", async () => {
    setup(eventsPage([COPY]));
    renderPage(<EventsPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.click(screen.getByLabelText("Живое обновление"));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("live=0"));
  });

  it("«Загрузить ещё» просит следующую страницу по курсору и скрывается в конце", async () => {
    const { calls } = mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /events": (call) =>
        new URLSearchParams(call.search).get("cursor") === "c1"
          ? eventsPage([USB])
          : eventsPage([COPY], "c1"),
    });
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Загрузить ещё" }));

    await screen.findByText("E: · KINGSTON");
    expect(screen.getByText("C:\\Docs\\отчёт.xlsx → E:\\отчёт.xlsx")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Загрузить ещё" })).not.toBeInTheDocument();

    const cursors = calls
      .filter((call) => call.path === "GET /events")
      .map((call) => new URLSearchParams(call.search).get("cursor"));
    expect(cursors).toContain("c1");
  });

  it("«Подробнее» открывает панель с актором, SHA-256 и исходным JSON", async () => {
    setup(eventsPage([COPY]));
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("PC\\ivanov")).toBeInTheDocument();
    expect(within(dialog).getByText("ab".repeat(32))).toBeInTheDocument();
    expect(within(dialog).getByText(/"src_path"/)).toBeInTheDocument();
  });

  it("в панели видно, что содержимое файла загружено на сервер", async () => {
    setup(eventsPage([COPY]));
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Содержимое на сервере")).toBeInTheDocument();
    expect(within(dialog).getByText("Загружено")).toBeInTheDocument();
  });

  it("если хеш есть, а файла нет на сервере, панель говорит «Не загружено»", async () => {
    setup(eventsPage([{ ...COPY, artifact_uploaded: false }]));
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));

    expect(within(await screen.findByRole("dialog")).getByText("Не загружено")).toBeInTheDocument();
  });

  it("у события без файла содержимое не упоминается как загруженное или нет", async () => {
    setup(eventsPage([USB]));
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText("Загружено")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Не загружено")).not.toBeInTheDocument();
  });

  it("пустой журнал объясняет, что событий нет", async () => {
    setup(eventsPage([]));
    renderPage(<EventsPage />, route());

    expect(await screen.findByText("Событий пока нет")).toBeInTheDocument();
  });

  it("пустой результат при фильтре предлагает сбросить фильтры", async () => {
    setup(eventsPage([]));
    renderPage(<EventsPage />, route("?severity=critical"));

    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сбросить фильтры" })).toBeInTheDocument();
  });

  it("ошибка сервера показывает повтор", async () => {
    setup(json(500, { detail: "boom" }));
    renderPage(<EventsPage />, route());

    expect(await screen.findByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });
});
