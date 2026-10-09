import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, json, mockApi, renderPage } from "../../test/utils";
import { RulesPage } from "./RulesPage";

const RULES = [
  {
    id: "r1", key: "iin_bin", kind: "detector", title: "ИИН/БИН (Казахстан)", builtin: true,
    enabled: true, version: 1, weight: 20, cap: 5, pattern: null, ignore_case: false,
    terms_count: null, updated_at: "2026-10-06T10:00:00Z",
  },
  {
    id: "r2", key: "markings", kind: "dictionary", title: "Грифы конфиденциальности", builtin: true,
    enabled: true, version: 2, weight: 15, cap: 2, pattern: null, ignore_case: false,
    terms_count: 7, updated_at: "2026-10-06T10:00:00Z",
  },
  {
    id: "r3", key: "custom_aa11bb22", kind: "regex", title: "Номер договора", builtin: false,
    enabled: false, version: 1, weight: 30, cap: 2, pattern: "№\d{4}", ignore_case: false,
    terms_count: null, updated_at: "2026-10-06T10:00:00Z",
  },
];

const setup = (extra: Record<string, Response | ((c: never) => Response)> = {}) =>
  mockApi({
    "GET /auth/me": json(200, ADMIN),
    "GET /rules": json(200, RULES),
    ...(extra as Record<string, Response>),
  });

const route = { route: "/rules", path: "/rules" };

describe("RulesPage", () => {
  it("показывает правила с типом, весом, потолком, версией и переключателем", async () => {
    setup();
    renderPage(<RulesPage />, route);

    const row = (await screen.findByText("ИИН/БИН (Казахстан)")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Встроенное")).toBeInTheDocument();
    expect(within(row).getByText("20")).toBeInTheDocument();
    expect(within(row).getByText("5")).toBeInTheDocument();
    expect(
      within(row).getByRole("checkbox", { name: "Включить правило «ИИН/БИН (Казахстан)»" }),
    ).toBeChecked();

    const custom = screen.getByText("Номер договора").closest("tr") as HTMLElement;
    expect(within(custom).getByText("Шаблон")).toBeInTheDocument();
    expect(
      within(custom).getByRole("checkbox", { name: "Включить правило «Номер договора»" }),
    ).not.toBeChecked();
    expect(screen.getByText("Словарь")).toBeInTheDocument();
  });

  it("переключатель отправляет PATCH enabled и сообщает об успехе", async () => {
    const { calls } = setup({ "PATCH /rules/r3": json(200, { ...RULES[2], enabled: true }) });
    renderPage(<RulesPage />, route);

    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Включить правило «Номер договора»" }),
    );

    await waitFor(() =>
      expect(calls.find((c) => c.path === "PATCH /rules/r3")?.body).toEqual({ enabled: true }),
    );
    expect(await screen.findByText("Правило включено")).toBeInTheDocument();
  });

  it("ошибка 409 возвращает переключатель и показывает сообщение", async () => {
    setup({
      "PATCH /rules/r1": json(409, { detail: "Нельзя отключить последнее включённое правило" }),
    });
    renderPage(<RulesPage />, route);

    const toggle = await screen.findByRole("checkbox", {
      name: "Включить правило «ИИН/БИН (Казахстан)»",
    });
    await userEvent.click(toggle);

    expect(
      await screen.findByText("Нельзя отключить последнее включённое правило"),
    ).toBeInTheDocument();
    await waitFor(() => expect(toggle).toBeChecked());
  });

  it("пустой список и ошибка загрузки", async () => {
    setup({ "GET /rules": json(200, []) });
    const first = renderPage(<RulesPage />, route);
    expect(await screen.findByText("Правил пока нет")).toBeInTheDocument();
    first.unmount();

    setup({ "GET /rules": json(500, { detail: "сбой" }) });
    renderPage(<RulesPage />, route);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("оператору страница недоступна", async () => {
    mockApi({ "GET /auth/me": json(200, { ...ADMIN, role: "operator" }), "GET /rules": json(200, RULES) });
    renderPage(<RulesPage />, route);

    expect(await screen.findByText("Правила доступны только администратору")).toBeInTheDocument();
    expect(screen.queryByText("ИИН/БИН (Казахстан)")).not.toBeInTheDocument();
  });

  it("вкладка «Как создавать правила» открывается", async () => {
    setup();
    renderPage(<RulesPage />, route);

    await userEvent.click(await screen.findByRole("tab", { name: "Как создавать правила" }));

    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Вес, потолок и оценка" })).toBeInTheDocument();
  });
});
