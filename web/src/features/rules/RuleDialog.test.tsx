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
    enabled: false, version: 1, weight: 30, cap: 2, pattern: "N-[0-9]+", ignore_case: false,
    terms_count: null, updated_at: "2026-10-06T10:00:00Z",
  },
];

const VERSIONS = [
  { version: 2, params: { weight: 20 }, created_at: "2026-10-06T11:00:00Z" },
  { version: 1, params: { weight: 10 }, created_at: "2026-10-06T10:00:00Z" },
];

const TERMS = { items: [{ id: "t1", term: "секретно" }], total: 1 };

const route = { route: "/rules", path: "/rules" };

const setup = (extra: Record<string, Response | ((c: never) => Response)> = {}) =>
  mockApi({
    "GET /auth/me": json(200, ADMIN),
    "GET /rules": json(200, RULES),
    "GET /rules/r1/versions": json(200, VERSIONS),
    "GET /rules/r2/versions": json(200, VERSIONS),
    "GET /rules/r3/versions": json(200, VERSIONS),
    "GET /rules/r2/terms": json(200, TERMS),
    ...(extra as Record<string, Response>),
  });

async function openEdit(title: string) {
  const row = (await screen.findByText(title)).closest("tr") as HTMLElement;
  await userEvent.click(within(row).getByRole("button", { name: "Изменить" }));
  return screen.findByRole("dialog");
}

const MATCH_OK = json(200, { ok: true, error: null, count: 1, matches: [{ start: 3, end: 7 }] });

describe("RuleDialog: правка", () => {
  it("отправляет только изменённые поля, сообщает об успехе и закрывается", async () => {
    const { calls } = setup({ "PATCH /rules/r1": json(200, { ...RULES[0], weight: 40 }) });
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("ИИН/БИН (Казахстан)");
    const weight = within(dialog).getByLabelText("Вес (1–100)");
    await userEvent.clear(weight);
    await userEvent.type(weight, "40");
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(calls.find((c) => c.path === "PATCH /rules/r1")?.body).toEqual({ weight: 40 }),
    );
    expect(await screen.findByText("Правило сохранено")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("смена шаблона блокирует сохранение до проверки, затем отправляет шаблон и текст", async () => {
    const { calls } = setup({
      "POST /rules/test": MATCH_OK,
      "PATCH /rules/r3": json(200, { ...RULES[2], pattern: "N-[0-9]+x" }),
    });
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("Номер договора");
    const save = within(dialog).getByRole("button", { name: "Сохранить" });
    expect(save).toBeEnabled();

    const pattern = within(dialog).getByLabelText("Шаблон");
    await userEvent.type(pattern, "x");
    expect(save).toBeDisabled();
    expect(within(dialog).getByText(/Сначала проверьте шаблон/)).toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText("Тестовый текст"), "ab N-1234 c");
    await userEvent.click(within(dialog).getByRole("button", { name: "Проверить" }));

    expect(await within(dialog).findByText("Совпадений: 1")).toBeInTheDocument();
    expect(dialog.querySelector("mark")?.textContent).toBe("N-12");
    await waitFor(() => expect(save).toBeEnabled());

    await userEvent.click(save);
    await waitFor(() =>
      expect(calls.find((c) => c.path === "PATCH /rules/r3")?.body).toEqual({
        pattern: "N-[0-9]+x",
        ignore_case: false,
        test_text: "ab N-1234 c",
      }),
    );

    // Проверяемый запрос содержит текст; ничего другого его не получает.
    expect(calls.find((c) => c.path === "POST /rules/test")?.body).toEqual({
      kind: "regex",
      pattern: "N-[0-9]+x",
      ignore_case: false,
      text: "ab N-1234 c",
    });
  });

  it("повторная смена шаблона после проверки снова блокирует сохранение", async () => {
    setup({ "POST /rules/test": MATCH_OK });
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("Номер договора");
    const pattern = within(dialog).getByLabelText("Шаблон");
    await userEvent.type(pattern, "x");
    await userEvent.type(within(dialog).getByLabelText("Тестовый текст"), "ab N-1234 c");
    await userEvent.click(within(dialog).getByRole("button", { name: "Проверить" }));
    const save = within(dialog).getByRole("button", { name: "Сохранить" });
    await waitFor(() => expect(save).toBeEnabled());

    await userEvent.type(pattern, "y");
    await waitFor(() => expect(save).toBeDisabled());
    expect(dialog.querySelector("mark")).toBeNull();
  });

  it("подсвечивает совпадение по кодовым точкам после эмодзи", async () => {
    setup({
      "POST /rules/test": json(200, {
        ok: true,
        error: null,
        count: 1,
        matches: [{ start: 2, end: 10 }],
      }),
    });
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("Номер договора");
    await userEvent.type(within(dialog).getByLabelText("Шаблон"), "x");
    await userEvent.type(within(dialog).getByLabelText("Тестовый текст"), "😀 ALFA-123");
    await userEvent.click(within(dialog).getByRole("button", { name: "Проверить" }));

    await within(dialog).findByText("Совпадений: 1");
    expect(dialog.querySelector("mark")?.textContent).toBe("ALFA-123");
  });

  it("запоздавший ответ проверки не засчитывается для изменённого шаблона", async () => {
    let release: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    setup({ "POST /rules/test": () => pending as never });
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("Номер договора");
    const pattern = within(dialog).getByLabelText("Шаблон");
    await userEvent.type(pattern, "x");
    await userEvent.type(within(dialog).getByLabelText("Тестовый текст"), "ab N-1234 c");
    await userEvent.click(within(dialog).getByRole("button", { name: "Проверить" }));

    // Шаблон меняется, пока проверка ещё идёт.
    await userEvent.type(pattern, "y");
    release(MATCH_OK);

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Проверить" })).toBeEnabled(),
    );
    expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(dialog.querySelector("mark")).toBeNull();
    expect(within(dialog).queryByText("Совпадений: 1")).not.toBeInTheDocument();
  });

  it("ошибка шаблона от проверки показывается и оставляет сохранение недоступным", async () => {
    setup({
      "POST /rules/test": json(200, {
        ok: false,
        error: "Шаблон не принят: плохая скобка",
        count: 0,
        matches: [],
      }),
    });
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("Номер договора");
    await userEvent.type(within(dialog).getByLabelText("Шаблон"), "(");
    await userEvent.type(within(dialog).getByLabelText("Тестовый текст"), "abc");
    await userEvent.click(within(dialog).getByRole("button", { name: "Проверить" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Шаблон не принят: плохая скобка",
    );
    expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("словарь: показывает термины, добавляет пачкой и удаляет по одному", async () => {
    const { calls } = setup({
      "POST /rules/r2/terms": json(200, { added: 2 }),
      "DELETE /rules/r2/terms/t1": json(204, null),
    });
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("Грифы конфиденциальности");
    expect(await within(dialog).findByText("секретно")).toBeInTheDocument();
    expect(within(dialog).getByText("Всего терминов: 1")).toBeInTheDocument();

    await userEvent.type(
      within(dialog).getByLabelText("Добавить термины (по одному в строке)"),
      "a{Enter}b",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Добавить" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "POST /rules/r2/terms")?.body).toEqual({
        terms: ["a", "b"],
      }),
    );
    expect(await screen.findByText("Добавлено терминов: 2")).toBeInTheDocument();

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Удалить термин «секретно»" }),
    );
    await waitFor(() =>
      expect(calls.some((c) => c.path === "DELETE /rules/r2/terms/t1")).toBe(true),
    );
    expect(await screen.findByText("Термин удалён")).toBeInTheDocument();
  });

  it("выводит историю версий", async () => {
    setup();
    renderPage(<RulesPage />, route);

    const dialog = await openEdit("ИИН/БИН (Казахстан)");
    expect(await within(dialog).findByText('{"weight":10}')).toBeInTheDocument();
    expect(within(dialog).getByText('{"weight":20}')).toBeInTheDocument();
  });
});

describe("CreateRuleDialog", () => {
  async function openCreate() {
    await screen.findByText("Номер договора");
    await userEvent.click(screen.getByRole("button", { name: "Создать правило" }));
    return screen.findByRole("dialog");
  }

  it("создаёт словарь: без названия кнопка неактивна, тело содержит термины", async () => {
    const { calls } = setup({ "POST /rules": json(201, { ...RULES[1], id: "r9" }) });
    renderPage(<RulesPage />, route);

    const dialog = await openCreate();
    const create = within(dialog).getByRole("button", { name: "Создать" });
    expect(create).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText("Название"), "Мои слова");
    await userEvent.type(
      within(dialog).getByLabelText("Термины (по одному в строке)"),
      "x{Enter}y",
    );
    expect(create).toBeEnabled();
    await userEvent.click(create);

    await waitFor(() =>
      expect(calls.find((c) => c.path === "POST /rules")?.body).toEqual({
        kind: "dictionary",
        title: "Мои слова",
        weight: 20,
        cap: 3,
        ignore_case: false,
        terms: ["x", "y"],
      }),
    );
    expect(await screen.findByText("Правило создано")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("создаёт шаблон только после успешной проверки", async () => {
    const { calls } = setup({
      "POST /rules/test": MATCH_OK,
      "POST /rules": json(201, { ...RULES[2], id: "r9" }),
    });
    renderPage(<RulesPage />, route);

    const dialog = await openCreate();
    await userEvent.selectOptions(within(dialog).getByLabelText("Тип правила"), "regex");
    await userEvent.type(within(dialog).getByLabelText("Название"), "Договор");
    await userEvent.type(within(dialog).getByLabelText("Шаблон"), "N-[[0-9]+");

    const create = within(dialog).getByRole("button", { name: "Создать" });
    expect(create).toBeDisabled();
    expect(within(dialog).getByText(/Сначала проверьте шаблон/)).toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText("Тестовый текст"), "ab N-1234 c");
    await userEvent.click(within(dialog).getByRole("button", { name: "Проверить" }));
    await waitFor(() => expect(create).toBeEnabled());
    await userEvent.click(create);

    await waitFor(() =>
      expect(calls.find((c) => c.path === "POST /rules")?.body).toEqual({
        kind: "regex",
        title: "Договор",
        weight: 20,
        cap: 3,
        pattern: "N-[0-9]+",
        ignore_case: false,
        test_text: "ab N-1234 c",
      }),
    );
  });

  it("ошибка 422 показывается сообщением, окно остаётся открытым", async () => {
    setup({ "POST /rules": json(422, { detail: "Название уже занято" }) });
    renderPage(<RulesPage />, route);

    const dialog = await openCreate();
    await userEvent.type(within(dialog).getByLabelText("Название"), "Мои слова");
    await userEvent.type(within(dialog).getByLabelText("Термины (по одному в строке)"), "x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Название уже занято")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
