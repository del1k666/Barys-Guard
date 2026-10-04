# Веб-консоль, цикл 1: оболочка, вход, обзор, агенты — план реализации

> **Для агентов-исполнителей:** ОБЯЗАТЕЛЬНЫЙ поднавык: используйте superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans, чтобы выполнять план задача за задачей. Шаги отмечены чекбоксами (`- [ ]`).

**Цель:** оператор входит в консоль, видит состояние парка и управляет отдельным агентом (команды, отзыв) целиком через веб.

**Архитектура:** React-приложение в `web/` поверх готового `/api/v1`. Данные — TanStack Query через единственный HTTP-клиент `api`, типы порождаются из `api/gateway-v1.yaml`. Свой UI-кит на CSS Modules и CSS-переменных (светлая и тёмная тема), все пользовательские строки лежат в `src/i18n/ru.ts`. Состояние списка агентов хранится в URL.

**Стек:** React 19, react-router-dom 7, @tanstack/react-query 5, Vite 6, Vitest 2 + Testing Library, TypeScript 5.7 (strict).

**Спека:** `docs/superpowers/specs/2026-10-04-web-console-cycle-1-design.md`. Контракт: `api/gateway-v1.yaml`.

## Глобальные ограничения

Каждая задача неявно включает этот раздел.

- Новых зависимостей (runtime и dev) не добавлять; `npm run licenses` остаётся зелёным.
- Все пользовательские строки — только в `src/i18n/ru.ts`; в компонентах литералов с текстом интерфейса нет (исключение — тестовые данные).
- Типы API берутся из `src/api/types.ts` (порождаются из контракта); руками типы ответов не пишутся.
- HTTP — только через `api` из `src/api/client.ts`. `fetch` в компонентах не вызывается.
- `tsconfig`: `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax` → типы импортируются через `import type`.
- Стили — CSS Modules (`*.module.css`) и переменные из `src/styles/global.css`; цвета в компонентах литералами не пишутся.
- Размер страницы списка агентов — 25. Минимальная длина нового пароля — 12 символов (как на сервере).
- Пункты меню цикла 2 (группы, пользователи, токены, аудит) не добавляются: мёртвых ссылок нет.
- Коммиты оканчиваются строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Команды выполняются из каталога `web/`.

## Review Focus

Входы и условия, которые спека подразумевает, а тесты задач по умолчанию не покрыли бы. Каждый пункт закреплён тестом в задаче-владельце.

1. `?page=abc`, `?page=0`, `?page=-3` в URL списка агентов → страница 1, а не пустой экран или запрос с отрицательным смещением (Задача 8, `agentFilters.test.ts`).
2. `?page=999` при непустом парке: сервер вернёт пустой список с `total > 0` → понятное сообщение и кнопка «На первую страницу», а не «Агентов пока нет» (Задача 8).
3. Агент, ни разу не выходивший на связь (`last_heartbeat_at: null`, `group_name: null`) → «—» и «Без группы», а не `Invalid Date` или пустая ячейка (Задача 8).
4. Имя хоста с разметкой (`<b>x</b>`) выводится буквально как текст (Задача 8).
5. Подтверждение отзыва или команды нажато, ответ ещё не пришёл → кнопка заблокирована, повторного запроса нет (Задача 9). Неизвестный статус или тип от сервера → нейтральный бейдж с сырым значением, а не падение (Задача 2).

---

## Карта файлов

```
web/
├── tsconfig.json                         (изм.) + vite/client
└── src/
    ├── main.tsx                          вход, провайдеры
    ├── App.tsx                           маршруты
    ├── styles/
    │   ├── global.css                    переменные, сброс, Spinner
    │   └── page.module.css               общие классы страниц
    ├── i18n/ru.ts                        все строки
    ├── test/
    │   ├── setup.ts                      (изм.) unstubAllGlobals
    │   └── utils.tsx                     json, mockApi, renderPage
    ├── lib/
    │   ├── errors.ts                     describeError
    │   ├── format.ts                     (изм.) + shortJson
    │   └── agentFilters.ts               URL ⇄ фильтры
    ├── api/types.ts                      (изм.) + CommandType
    ├── components/
    │   ├── Button.tsx / .module.css
    │   ├── Input.tsx, Select.tsx / Field.module.css
    │   ├── Badge.tsx, StatusBadge.tsx / Badge.module.css
    │   ├── Table.tsx / Table.module.css
    │   ├── Pagination.tsx / Pagination.module.css
    │   ├── States.tsx / States.module.css       EmptyState, ErrorState
    │   ├── Modal.tsx / Modal.module.css
    │   └── Toast.tsx / Toast.module.css
    ├── app/
    │   └── Shell.tsx / Shell.module.css  боковая панель и Outlet
    └── features/
        ├── auth/        LoginPage.tsx, ChangePasswordPage.tsx, auth.module.css
        ├── overview/    useOverview.ts, OverviewPage.tsx
        └── agents/      queries.ts, AgentListPage.tsx, AgentDetailPage.tsx,
                         CommandHistory.tsx, SendCommandDialog.tsx,
                         RevokeDialog.tsx, Fact.tsx
```

---

### Task 1: Фундамент — тема, строки, тестовая инфраструктура, ошибки

**Файлы:**
- Создать: `web/src/styles/global.css`, `web/src/styles/page.module.css`, `web/src/i18n/ru.ts`, `web/src/lib/errors.ts`, `web/src/lib/errors.test.ts`, `web/src/test/utils.tsx`
- Изменить: `web/tsconfig.json`, `web/src/test/setup.ts`, `web/src/lib/format.ts`, `web/src/api/types.ts`
- Тест: `web/src/lib/errors.test.ts`, `web/src/lib/shortJson.test.ts`

**Интерфейсы:**
- Потребляет: `ApiError` из `src/api/client.ts`.
- Производит: `ru` (объект строк); `describeError(error: unknown): string`; `shortJson(value: unknown, max?: number): string`; тип `CommandType`; тестовые помощники `json(status, body): Response`, `OPERATOR`, `ADMIN`, `FORCED`, `mockApi(routes): { calls: Call[] }`, `renderPage(ui, { route?, path? })`.

- [ ] **Шаг 1: Написать падающие тесты**

`web/src/lib/errors.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";
import { describeError } from "./errors";

describe("describeError", () => {
  it("объясняет 403 и 404 по-человечески", () => {
    expect(describeError(new ApiError(403, "forbidden"))).toBe(ru.errors.forbidden);
    expect(describeError(new ApiError(404, "not found"))).toBe(ru.errors.notFound);
  });

  it("оставляет сообщение сервера для прочих статусов", () => {
    expect(describeError(new ApiError(502, "ошибка сервера (502)"))).toBe("ошибка сервера (502)");
  });

  it("узнаёт обрыв сети", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toBe(ru.errors.network);
  });

  it("не падает на неизвестном", () => {
    expect(describeError("что-то")).toBe(ru.errors.unknown);
  });
});
```

`web/src/lib/shortJson.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { shortJson } from "./format";

describe("shortJson", () => {
  it("показывает прочерк для пустого результата", () => {
    expect(shortJson(null)).toBe("—");
    expect(shortJson(undefined)).toBe("—");
  });

  it("не трогает короткое", () => {
    expect(shortJson({ ok: true })).toBe('{"ok":true}');
  });

  it("обрезает длинное с многоточием", () => {
    const cut = shortJson({ text: "x".repeat(200) }, 20);
    expect(cut).toHaveLength(20);
    expect(cut.endsWith("…")).toBe(true);
  });
});
```

- [ ] **Шаг 2: Запустить и убедиться, что падают**

Выполнить: `npm test -- src/lib/errors.test.ts src/lib/shortJson.test.ts`
Ожидаем: FAIL — модули `./errors`, `../i18n/ru` не найдены; `shortJson` не экспортируется.

- [ ] **Шаг 3: Строки интерфейса `web/src/i18n/ru.ts`**

```ts
/**
 * Все строки интерфейса.
 *
 * Компоненты текста не содержат: добавить язык — значит добавить
 * соседний файл с тем же устройством, а не переписывать экраны.
 */
export const ru = {
  app: { name: "BarysGuard DLP" },

  nav: {
    main: "Основная навигация",
    overview: "Обзор",
    agents: "Агенты",
    menu: "Меню",
    logout: "Выйти",
    roles: { admin: "Администратор", operator: "Оператор" } as Record<string, string>,
  },

  common: {
    loading: "Загрузка",
    retry: "Повторить",
    cancel: "Отмена",
    none: "—",
  },

  errors: {
    forbidden: "Недостаточно прав для этого раздела.",
    notFound: "Запрошенный объект не найден или недоступен.",
    network: "Нет связи с сервером. Проверьте подключение.",
    unknown: "Что-то пошло не так.",
  },

  login: {
    title: "Вход в консоль",
    checking: "Проверяем сессию",
    username: "Логин",
    password: "Пароль",
    submit: "Войти",
    invalid: "Неверный логин или пароль",
  },

  password: {
    title: "Смена пароля",
    forced: "Вам выдан временный пароль. Задайте собственный, чтобы продолжить работу.",
    current: "Текущий пароль",
    next: "Новый пароль",
    repeat: "Повторите новый пароль",
    hint: "Не короче 12 символов.",
    submit: "Сменить пароль",
    mismatch: "Пароли не совпадают",
    wrongCurrent: "Текущий пароль неверен",
    done: "Пароль изменён",
  },

  status: {
    agent: {
      pending: "Ожидает",
      active: "Активен",
      offline: "Не в сети",
      quarantined: "Карантин",
      revoked: "Отозван",
    } as Record<string, string>,
    command: {
      queued: "В очереди",
      sent: "Отправлена",
      running: "Выполняется",
      done: "Выполнена",
      failed: "Ошибка",
      expired: "Просрочена",
    } as Record<string, string>,
  },

  commandType: {
    ping: "Проверка связи",
    refresh_config: "Обновить конфигурацию",
    collect_diagnostics: "Собрать диагностику",
  } as Record<string, string>,

  pagination: {
    label: "Постраничная навигация",
    prev: "Назад",
    next: "Вперёд",
    range: (from: string, to: string, total: string) => `${from}–${to} из ${total}`,
  },

  overview: {
    title: "Обзор",
    agents: "Агенты",
    total: "Всего",
    certificates: "Сертификаты, скоро истекающие",
    tokens: "Активные токены регистрации",
    commands: "Команды",
    queued: "В очереди",
    failed24h: "Неудачные за 24 часа",
    versions: "Версии агента",
    systems: "Операционные системы",
    noData: "Данных пока нет",
  },

  agents: {
    title: "Агенты",
    caption: "Список агентов",
    search: "Поиск по имени хоста",
    searchSubmit: "Найти",
    status: "Статус",
    group: "Группа",
    anyStatus: "Любой статус",
    anyGroup: "Любая группа",
    noGroup: "Без группы",
    reset: "Сбросить фильтры",
    columns: {
      host: "Хост",
      status: "Статус",
      os: "Система",
      group: "Группа",
      heartbeat: "Последняя связь",
      version: "Версия агента",
    },
    emptyFleet: "Агентов пока нет",
    emptyFleetHint: "Выпустите токен регистрации и установите агент на хост.",
    emptyFilter: "Ничего не найдено",
    emptyFilterHint: "Измените запрос или сбросьте фильтры.",
    emptyPage: "На этой странице агентов нет",
    emptyPageHint: "Список стал короче, чем номер страницы.",
    firstPage: "На первую страницу",
  },

  agent: {
    back: "← К списку агентов",
    facts: "Сведения",
    hostname: "Имя хоста",
    os: "Система",
    arch: "Архитектура",
    version: "Версия агента",
    group: "Группа",
    ip: "Последний IP",
    skew: "Расхождение часов",
    configVersion: "Версия конфигурации",
    enrolledAt: "Зарегистрирован",
    machineId: "Идентификатор машины",
    tags: "Теги",
    certificate: "Сертификат",
    noCertificate: "Сертификат не выдан",
    serial: "Серийный номер",
    fingerprint: "Отпечаток SHA-256",
    validFrom: "Действует с",
    validTo: "Действует до",
    revokedAt: "Отозван",
    history: "История команд",
    noCommands: "Команд ещё не было",
    historyColumns: {
      type: "Команда",
      status: "Статус",
      created: "Создана",
      completed: "Завершена",
      result: "Результат",
    },
    actions: "Действия",
    sendCommand: "Отправить команду",
    commandTitle: "Отправить команду",
    commandType: "Тип команды",
    commandSend: "Отправить",
    commandSent: "Команда поставлена в очередь",
    revoke: "Отозвать агента",
    revokeTitle: "Отозвать агента?",
    revokeWarning: (host: string) =>
      `Агент ${host} будет выведен из-под наблюдения: все его сертификаты отзываются. Отменить это нельзя.`,
    revokeReason: "Причина отзыва",
    revokeConfirm: "Отозвать",
    revoked: "Агент отозван",
  },
};
```

Строки со склонениями и подстановкой (`range`, `revokeWarning`) — функции, остальное — значения. Словари статусов и типов команд типизированы как `Record<string, string>`: сервер может прислать значение, которого консоль не знает, и поиск по словарю должен это переживать.

- [ ] **Шаг 4: `web/src/lib/errors.ts`**

```ts
import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";

/** Одна фраза для любого сбоя, которую можно показать оператору. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return ru.errors.forbidden;
    if (error.status === 404) return ru.errors.notFound;
    return error.message;
  }

  // fetch при обрыве сети бросает именно TypeError.
  if (error instanceof TypeError) return ru.errors.network;

  return ru.errors.unknown;
}
```

- [ ] **Шаг 5: Добавить `shortJson` в конец `web/src/lib/format.ts`**

```ts
/** Результат команды в одну строку таблицы. */
export function shortJson(value: unknown, max = 80): string {
  if (value === null || value === undefined) return "—";

  const text = JSON.stringify(value);
  if (text === undefined) return "—";

  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
```

- [ ] **Шаг 6: Экспортировать `CommandType` в `web/src/api/types.ts`**

Добавить после строки `export type FleetCommand = Schemas["FleetCommand"];`:

```ts
export type CommandType = Schemas["CommandType"];
```

- [ ] **Шаг 7: Подключить типы Vite для CSS Modules, `web/tsconfig.json`**

Заменить строку `types`:

```json
    "types": ["vitest/globals", "@testing-library/jest-dom", "vite/client"],
```

- [ ] **Шаг 8: Тема и сброс, `web/src/styles/global.css`**

```css
:root {
  color-scheme: light dark;
  --bg: #f4f5f7;
  --surface: #ffffff;
  --surface-2: #eceff3;
  --border: #d5dae1;
  --text: #171b22;
  --muted: #586172;
  --accent: #1d5bd1;
  --accent-hover: #174aa8;
  --on-accent: #ffffff;
  --danger: #b42318;
  --danger-hover: #912018;
  --on-danger: #ffffff;
  --danger-bg: #fdeceb;
  --ok: #176b34;
  --ok-bg: #e4f4e9;
  --warn: #8a5300;
  --warn-bg: #fff2d1;
  --info: #1d4ea8;
  --info-bg: #e4edfc;
  --neutral-bg: #e9ecf0;
  --shadow: 0 8px 28px rgba(15, 23, 42, 0.18);
  --radius: 8px;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151a;
    --surface: #1b1f27;
    --surface-2: #252a34;
    --border: #333a47;
    --text: #e8ebf0;
    --muted: #98a2b3;
    --accent: #6c9bff;
    --accent-hover: #8fb2ff;
    --on-accent: #0b1220;
    --danger: #ff8a80;
    --danger-hover: #ffa8a0;
    --on-danger: #2b0b08;
    --danger-bg: #3a1f1d;
    --ok: #7bd99a;
    --ok-bg: #16301f;
    --warn: #f2c26b;
    --warn-bg: #3a2b0f;
    --info: #8fb2ff;
    --info-bg: #182646;
    --neutral-bg: #2a303b;
    --shadow: 0 8px 28px rgba(0, 0, 0, 0.5);
  }
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

html,
body,
#root {
  height: 100%;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 var(--font);
}

a {
  color: var(--accent);
}

:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.spinner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 16px;
  color: var(--muted);
}

.spinner__dot {
  width: 16px;
  height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinner__dot {
    animation: none;
  }
}
```

- [ ] **Шаг 9: Общие классы страниц, `web/src/styles/page.module.css`**

```css
.title {
  margin: 0 0 20px;
  font-size: 24px;
  font-weight: 650;
}

.titleRow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.titleRow .title {
  margin: 0;
}

.section {
  margin-bottom: 28px;
  padding: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.sectionTitle {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 650;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 12px;
}

.card {
  display: block;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: inherit;
  text-decoration: none;
}

a.card:hover {
  border-color: var(--accent);
}

.cardLabel {
  color: var(--muted);
  font-size: 13px;
}

.cardValue {
  font-size: 28px;
  font-weight: 650;
  line-height: 1.2;
}

.list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 16px;
}

.dl {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px 24px;
  margin: 0;
}

.fact dt {
  color: var(--muted);
  font-size: 13px;
}

.fact dd {
  margin: 2px 0 0;
  overflow-wrap: anywhere;
}

.mono {
  font-family: var(--mono);
  font-size: 13px;
}

.muted {
  color: var(--muted);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.back {
  display: inline-block;
  margin-bottom: 12px;
}
```

- [ ] **Шаг 10: Тестовые помощники**

`web/src/test/setup.ts` — заменить содержимое:

```ts
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

// Подмену fetch каждый тест ставит сам; не переносим её в следующий.
afterEach(() => {
  vi.unstubAllGlobals();
});
```

`web/src/test/utils.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation, type InitialEntry } from "react-router-dom";
import { vi } from "vitest";

import { SessionProvider } from "../app/session";
import { ToastProvider } from "../components/Toast";

export function json(status: number, body: unknown): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

export const OPERATOR = {
  id: "11111111-1111-1111-1111-111111111111",
  username: "ivanov",
  role: "operator",
  scope_group_id: null,
  must_change_password: false,
};

export const ADMIN = { ...OPERATOR, username: "admin", role: "admin" };

export const FORCED = { ...OPERATOR, must_change_password: true };

export interface Call {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

export type Handler = Response | ((call: Call) => Response | Promise<Response>);

/**
 * Подмена сервера. Ключ — «МЕТОД /путь» без префикса /api/v1.
 * Необъявленный запрос отвечает 404, чтобы забытый маршрут не молчал.
 */
export function mockApi(routes: Record<string, Handler>): { calls: Call[] } {
  const calls: Call[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      const path = url.pathname.replace(/^\/api\/v1/, "");
      const key = `${method} ${path}`;

      const call: Call = {
        method,
        path: key,
        search: url.search,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };

      const handler = routes[key];
      if (!handler) return json(404, { detail: `unmocked ${key}` });

      calls.push(call);
      return typeof handler === "function" ? handler(call) : handler;
    }),
  );

  return { calls };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">
      {location.pathname}
      {location.search}
    </div>
  );
}

/** Рисует страницу в окружении приложения: запросы, маршруты, сессия, тосты. */
export function renderPage(
  ui: ReactElement,
  options: { route?: InitialEntry; path?: string } = {},
) {
  const { route = "/", path = "/*" } = options;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <ToastProvider>
          <SessionProvider>
            <Routes>
              <Route path={path} element={ui} />
              <Route path="*" element={null} />
            </Routes>
            <LocationProbe />
          </SessionProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
```

`utils.tsx` импортирует `Toast`, который появится в Задаче 4. До неё `tsc` пожалуется на этот импорт, а тесты, использующие `renderPage`, не запустятся; задачи 1–3 их не используют.

- [ ] **Шаг 11: Запустить тесты**

Выполнить: `npm test -- src/lib/errors.test.ts src/lib/shortJson.test.ts`
Ожидаем: PASS, 7 тестов.

- [ ] **Шаг 12: Зафиксировать**

```bash
git add web/tsconfig.json web/src
git commit -m "feat(web): theme tokens, i18n strings, error helper and test utilities

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: UI-кит, часть 1 — кнопка, поля, бейджи

**Файлы:**
- Создать: `web/src/components/Button.tsx`, `Button.module.css`, `Input.tsx`, `Select.tsx`, `Field.module.css`, `Badge.tsx`, `StatusBadge.tsx`, `Badge.module.css`
- Тест: `web/src/components/Button.test.tsx`, `web/src/components/StatusBadge.test.tsx`

**Интерфейсы:**
- Производит:
  - `Button(props: ButtonHTMLAttributes & { variant?: "primary" | "secondary" | "danger"; loading?: boolean })`
  - `Input(props: InputHTMLAttributes & { label: string; error?: string; hint?: string })`
  - `Select(props: SelectHTMLAttributes & { label: string; options: { value: string; label: string }[]; error?: string })`
  - `Badge({ tone?: Tone; children })`, `type Tone = "neutral" | "ok" | "warn" | "danger" | "info"`
  - `AgentStatusBadge({ status: string })`, `CommandStatusBadge({ status: string })`

- [ ] **Шаг 1: Написать падающие тесты**

`web/src/components/Button.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./Button";

describe("Button", () => {
  it("по умолчанию не отправляет форму", async () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Button>Нажать</Button>
      </form>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Нажать" }));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("в состоянии loading заблокирована и не реагирует", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Сохранить
      </Button>,
    );

    const button = screen.getByRole("button", { name: "Сохранить" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");

    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});
```

`web/src/components/StatusBadge.test.tsx`:

```tsx
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
```

- [ ] **Шаг 2: Запустить и убедиться, что падают**

Выполнить: `npm test -- src/components/Button.test.tsx src/components/StatusBadge.test.tsx`
Ожидаем: FAIL — модули не найдены.

- [ ] **Шаг 3: Кнопка**

`web/src/components/Button.tsx`:

```tsx
import type { ButtonHTMLAttributes } from "react";

import styles from "./Button.module.css";

type Variant = "primary" | "secondary" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
}

export function Button({
  variant = "secondary",
  loading = false,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={[styles.button, styles[variant], className].filter(Boolean).join(" ")}
    >
      {children}
    </button>
  );
}
```

`web/src/components/Button.module.css`:

```css
.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 36px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
}

.button:hover:not(:disabled) {
  background: var(--surface-2);
}

.button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--on-accent);
}

.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.danger {
  background: var(--danger);
  border-color: var(--danger);
  color: var(--on-danger);
}

.danger:hover:not(:disabled) {
  background: var(--danger-hover);
}
```

- [ ] **Шаг 4: Поля**

`web/src/components/Input.tsx`:

```tsx
import { useId, type InputHTMLAttributes } from "react";

import styles from "./Field.module.css";

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  error?: string;
  hint?: string;
}

export function Input({ label, error, hint, ...rest }: InputProps) {
  const id = useId();
  const noteId = `${id}-note`;
  const hasNote = Boolean(error || hint);

  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <input
        {...rest}
        id={id}
        className={styles.control}
        aria-invalid={error ? true : undefined}
        aria-describedby={hasNote ? noteId : undefined}
      />
      {error ? (
        <p id={noteId} className={styles.error} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={noteId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}
```

`web/src/components/Select.tsx`:

```tsx
import { useId, type SelectHTMLAttributes } from "react";

import styles from "./Field.module.css";

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> {
  label: string;
  options: { value: string; label: string }[];
  error?: string;
}

export function Select({ label, options, error, ...rest }: SelectProps) {
  const id = useId();
  const errorId = `${id}-error`;

  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <select
        {...rest}
        id={id}
        className={styles.control}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error ? (
        <p id={errorId} className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
```

`web/src/components/Field.module.css`:

```css
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 180px;
}

.label {
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.control {
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font: inherit;
}

.control[aria-invalid="true"] {
  border-color: var(--danger);
}

.error {
  margin: 0;
  color: var(--danger);
  font-size: 13px;
}

.hint {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
```

- [ ] **Шаг 5: Бейджи**

`web/src/components/Badge.tsx`:

```tsx
import type { ReactNode } from "react";

import styles from "./Badge.module.css";

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`${styles.badge} ${styles[tone]}`}>{children}</span>;
}
```

`web/src/components/StatusBadge.tsx`:

```tsx
import { ru } from "../i18n/ru";
import { Badge, type Tone } from "./Badge";

const AGENT_TONES: Record<string, Tone> = {
  active: "ok",
  offline: "warn",
  pending: "info",
  quarantined: "danger",
  revoked: "neutral",
};

const COMMAND_TONES: Record<string, Tone> = {
  queued: "info",
  sent: "info",
  running: "info",
  done: "ok",
  failed: "danger",
  expired: "neutral",
};

// Сервер может прислать статус новее консоли: показываем его как есть
// и нейтральным цветом, а не падаем.
export function AgentStatusBadge({ status }: { status: string }) {
  return <Badge tone={AGENT_TONES[status] ?? "neutral"}>{ru.status.agent[status] ?? status}</Badge>;
}

export function CommandStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={COMMAND_TONES[status] ?? "neutral"}>{ru.status.command[status] ?? status}</Badge>
  );
}
```

`web/src/components/Badge.module.css`:

```css
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.neutral {
  background: var(--neutral-bg);
  color: var(--muted);
}

.ok {
  background: var(--ok-bg);
  color: var(--ok);
}

.warn {
  background: var(--warn-bg);
  color: var(--warn);
}

.danger {
  background: var(--danger-bg);
  color: var(--danger);
}

.info {
  background: var(--info-bg);
  color: var(--info);
}
```

- [ ] **Шаг 6: Запустить тесты**

Выполнить: `npm test -- src/components/Button.test.tsx src/components/StatusBadge.test.tsx`
Ожидаем: PASS, 4 теста.

- [ ] **Шаг 7: Зафиксировать**

```bash
git add web/src/components
git commit -m "feat(web): button, form fields and status badges

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: UI-кит, часть 2 — таблица, пагинация, состояния

**Файлы:**
- Создать: `web/src/components/Table.tsx`, `Table.module.css`, `Pagination.tsx`, `Pagination.module.css`, `States.tsx`, `States.module.css`
- Тест: `web/src/components/Pagination.test.tsx`, `web/src/components/States.test.tsx`

**Интерфейсы:**
- Потребляет: `Button`, `describeError`, `ApiError`, `ru`, `formatNumber`.
- Производит:
  - `Table({ caption: string; children })` — обёртка над `<table>` с прокруткой; строки и заголовки пишет потребитель
  - `Pagination({ total: number; limit: number; offset: number; onChange: (offset: number) => void })`
  - `EmptyState({ title: string; hint?: string; action?: ReactNode })`
  - `ErrorState({ error: unknown; onRetry?: () => void })`

- [ ] **Шаг 1: Написать падающие тесты**

`web/src/components/Pagination.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("на первой странице нельзя идти назад и можно вперёд", async () => {
    const onChange = vi.fn();
    render(<Pagination total={120} limit={25} offset={0} onChange={onChange} />);

    expect(screen.getByText("1–25 из 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    expect(onChange).toHaveBeenCalledWith(25);
  });

  it("на последней странице нельзя идти вперёд", async () => {
    const onChange = vi.fn();
    render(<Pagination total={120} limit={25} offset={100} onChange={onChange} />);

    expect(screen.getByText("101–120 из 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Вперёд" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Назад" }));
    expect(onChange).toHaveBeenCalledWith(75);
  });

  it("при пустом списке ничего не рисует", () => {
    const { container } = render(<Pagination total={0} limit={25} offset={0} onChange={() => {}} />);

    expect(container).toBeEmptyDOMElement();
  });
});
```

`web/src/components/States.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";
import { EmptyState, ErrorState } from "./States";

describe("состояния", () => {
  it("EmptyState показывает заголовок и подсказку", () => {
    render(<EmptyState title="Пусто" hint="Добавьте что-нибудь" />);

    expect(screen.getByText("Пусто")).toBeInTheDocument();
    expect(screen.getByText("Добавьте что-нибудь")).toBeInTheDocument();
  });

  it("ErrorState предлагает повтор при сбое сервера", async () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new ApiError(502, "ошибка сервера (502)")} onRetry={onRetry} />);

    expect(screen.getByText("ошибка сервера (502)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: ru.common.retry }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("ErrorState не предлагает повтор там, где он бесполезен", () => {
    render(<ErrorState error={new ApiError(403, "forbidden")} onRetry={() => {}} />);

    expect(screen.getByText(ru.errors.forbidden)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
```

- [ ] **Шаг 2: Запустить и убедиться, что падают**

Выполнить: `npm test -- src/components/Pagination.test.tsx src/components/States.test.tsx`
Ожидаем: FAIL — модули не найдены.

- [ ] **Шаг 3: Таблица**

`web/src/components/Table.tsx`:

```tsx
import type { ReactNode } from "react";

import styles from "./Table.module.css";

export function Table({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}
```

`web/src/components/Table.module.css`:

```css
.wrap {
  overflow-x: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.table {
  width: 100%;
  border-collapse: collapse;
}

.table th,
.table td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}

.table th {
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.table tbody tr:last-child td {
  border-bottom: 0;
}
```

- [ ] **Шаг 4: Пагинация**

`web/src/components/Pagination.tsx`:

```tsx
import { ru } from "../i18n/ru";
import { formatNumber } from "../lib/format";
import { Button } from "./Button";
import styles from "./Pagination.module.css";

interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}

export function Pagination({ total, limit, offset, onChange }: PaginationProps) {
  if (total === 0) return null;

  const from = offset + 1;
  const to = Math.min(offset + limit, total);

  return (
    <nav className={styles.pagination} aria-label={ru.pagination.label}>
      <span>{ru.pagination.range(formatNumber(from), formatNumber(to), formatNumber(total))}</span>
      <div className={styles.buttons}>
        <Button disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          {ru.pagination.prev}
        </Button>
        <Button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          {ru.pagination.next}
        </Button>
      </div>
    </nav>
  );
}
```

`web/src/components/Pagination.module.css`:

```css
.pagination {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  color: var(--muted);
}

.buttons {
  display: flex;
  gap: 8px;
}
```

- [ ] **Шаг 5: Состояния**

`web/src/components/States.tsx`:

```tsx
import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";
import { describeError } from "../lib/errors";
import { Button } from "./Button";
import styles from "./States.module.css";

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.state}>
      <p className={styles.title}>{title}</p>
      {hint ? <p className={styles.hint}>{hint}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  // Повтор не вернёт права и не создаст объект, которого нет.
  const retryable = !(error instanceof ApiError && (error.status === 403 || error.status === 404));

  return (
    <div className={styles.state} role="alert">
      <p className={styles.title}>{describeError(error)}</p>
      {onRetry && retryable ? <Button onClick={onRetry}>{ru.common.retry}</Button> : null}
    </div>
  );
}
```

`web/src/components/States.module.css`:

```css
.state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 48px 16px;
  text-align: center;
}

.title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.hint {
  margin: 0;
  color: var(--muted);
}
```

- [ ] **Шаг 6: Запустить тесты**

Выполнить: `npm test -- src/components/Pagination.test.tsx src/components/States.test.tsx`
Ожидаем: PASS, 6 тестов.

- [ ] **Шаг 7: Зафиксировать**

```bash
git add web/src/components
git commit -m "feat(web): table, pagination and empty/error states

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: UI-кит, часть 3 — модальное окно и тосты

**Файлы:**
- Создать: `web/src/components/Modal.tsx`, `Modal.module.css`, `Toast.tsx`, `Toast.module.css`
- Тест: `web/src/components/Modal.test.tsx`, `web/src/components/Toast.test.tsx`

**Интерфейсы:**
- Производит:
  - `Modal({ open: boolean; title: string; onClose: () => void; children; footer?: ReactNode })` — рисуется в `document.body`, Esc закрывает, Tab зациклен внутри, фокус возвращается на прежний элемент
  - `ToastProvider({ children })`, `useToast(): { notify: (message: string, tone?: "ok" | "danger" | "info") => void }`

- [ ] **Шаг 1: Написать падающие тесты**

`web/src/components/Modal.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Modal } from "./Modal";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        открыть
      </button>
      <Modal
        open={open}
        title="Подтверждение"
        onClose={() => setOpen(false)}
        footer={
          <>
            <button type="button" onClick={() => setOpen(false)}>
              отмена
            </button>
            <button type="button">ок</button>
          </>
        }
      >
        <p>текст</p>
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("закрытое окно ничего не рисует", () => {
    render(<Harness />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("открывается с именем по заголовку и переносит фокус внутрь", async () => {
    render(<Harness />);

    await userEvent.click(screen.getByRole("button", { name: "открыть" }));

    expect(screen.getByRole("dialog", { name: "Подтверждение" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "отмена" })).toHaveFocus();
  });

  it("Esc закрывает окно и возвращает фокус на кнопку открытия", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "открыть" });

    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("Tab не выводит фокус за пределы окна", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "открыть" }));

    await userEvent.tab(); // отмена -> ок
    expect(screen.getByRole("button", { name: "ок" })).toHaveFocus();

    await userEvent.tab(); // ок -> снова отмена
    expect(screen.getByRole("button", { name: "отмена" })).toHaveFocus();
  });
});
```

`web/src/components/Toast.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ToastProvider, useToast } from "./Toast";

function Trigger() {
  const toast = useToast();
  return (
    <button type="button" onClick={() => toast.notify("Готово", "ok")}>
      показать
    </button>
  );
}

describe("Toast", () => {
  it("показывает сообщение", async () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "показать" }));

    expect(screen.getByText("Готово")).toBeInTheDocument();
  });

  it("useToast вне провайдера сообщает об ошибке разработчика", () => {
    expect(() => render(<Trigger />)).toThrow("useToast");
  });
});
```

Второй тест намеренно роняет рендер, React напишет в консоль об ошибке — это ожидаемый шум.

- [ ] **Шаг 2: Запустить и убедиться, что падают**

Выполнить: `npm test -- src/components/Modal.test.tsx src/components/Toast.test.tsx`
Ожидаем: FAIL — модули не найдены.

- [ ] **Шаг 3: Модальное окно**

`web/src/components/Modal.tsx`:

```tsx
import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import styles from "./Modal.module.css";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export function Modal({ open, title, onClose, children, footer }: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  // Родитель обычно передаёт onClose стрелкой; если бы эффект зависел от
  // неё, фокус сбрасывался бы на каждый рендер родителя.
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const dialog = dialogRef.current;
    const previous = document.activeElement as HTMLElement | null;
    const items = () => Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);

    const initial = items()[0];
    if (initial) initial.focus();
    else dialog?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const list = items();
      const first = list[0];
      const last = list[list.length - 1];
      if (!first || !last) {
        event.preventDefault();
        return;
      }

      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className={styles.overlay}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeRef.current();
      }}
    >
      <div
        ref={dialogRef}
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        <div className={styles.body}>{children}</div>
        {footer ? <div className={styles.footer}>{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
```

`web/src/components/Modal.module.css`:

```css
.overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(10, 14, 20, 0.55);
}

.dialog {
  width: min(480px, 100%);
  max-height: 100%;
  overflow: auto;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.title {
  margin: 0;
  padding: 18px 20px 0;
  font-size: 18px;
  font-weight: 650;
}

.body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 20px;
}

.footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 0 20px 18px;
}
```

- [ ] **Шаг 4: Тосты**

`web/src/components/Toast.tsx`:

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import styles from "./Toast.module.css";

type ToastTone = "ok" | "danger" | "info";

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastApi {
  notify: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const LIFETIME_MS = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Set<number>());

  const notify = useCallback((message: string, tone: ToastTone = "info") => {
    const id = nextId.current++;
    setItems((current) => [...current, { id, message, tone }]);

    const timer = window.setTimeout(() => {
      timers.current.delete(timer);
      setItems((current) => current.filter((item) => item.id !== id));
    }, LIFETIME_MS);
    timers.current.add(timer);
  }, []);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((timer) => window.clearTimeout(timer));
      pending.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={styles.region} aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`${styles.toast} ${styles[item.tone]}`}>
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (value === null) {
    throw new Error("useToast вызван вне ToastProvider");
  }
  return value;
}
```

`web/src/components/Toast.module.css`:

```css
.region {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 60;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: min(360px, calc(100vw - 32px));
}

.toast {
  padding: 10px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.ok {
  border-left-color: var(--ok);
}

.danger {
  border-left-color: var(--danger);
}

.info {
  border-left-color: var(--info);
}
```

- [ ] **Шаг 5: Запустить тесты и проверку типов**

Выполнить: `npm test -- src/components; npm run typecheck`
Ожидаем: тесты каталога PASS (Button, StatusBadge, Pagination, States, Modal, Toast); `tsc` без ошибок (теперь `Toast` существует, и `test/utils.tsx` собирается).

- [ ] **Шаг 6: Зафиксировать**

```bash
git add web/src/components
git commit -m "feat(web): modal dialog with focus trap and toast notifications

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Оболочка — боковая панель и навигация

**Файлы:**
- Создать: `web/src/app/Shell.tsx`, `web/src/app/Shell.module.css`
- Тест: `web/src/app/Shell.test.tsx`

**Интерфейсы:**
- Потребляет: `useSession()` (`user`, `logout`) из `src/app/session.tsx`; `Button`; `ru`; `renderPage`, `mockApi`, `json`, `ADMIN`, `OPERATOR`, `FORCED` из `src/test/utils.tsx`.
- Производит: `Shell()` — layout-маршрут: боковая панель и `<Outlet />`. Для оператора с `must_change_password` навигация не показывается.

- [ ] **Шаг 1: Написать падающий тест**

`web/src/app/Shell.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ADMIN, FORCED, json, mockApi, OPERATOR, renderPage } from "../test/utils";
import { Shell } from "./Shell";

function shellPage() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<div>содержимое</div>} />
      </Route>
    </Routes>
  );
}

describe("Shell", () => {
  it("показывает навигацию, имя и роль оператора", async () => {
    mockApi({ "GET /auth/me": json(200, ADMIN) });

    renderPage(shellPage());

    expect(await screen.findByText("содержимое")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Обзор" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Агенты" })).toHaveAttribute("href", "/agents");
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("Администратор")).toBeInTheDocument();
  });

  it("не предлагает разделов, пока не сменён временный пароль", async () => {
    mockApi({ "GET /auth/me": json(200, FORCED) });

    renderPage(shellPage());

    expect(await screen.findByText("содержимое")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Агенты" })).not.toBeInTheDocument();
  });

  it("выход завершает сессию и ведёт на вход", async () => {
    const { calls } = mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "POST /auth/logout": json(204, null),
    });

    renderPage(shellPage());
    await screen.findByText("содержимое");

    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/login"));
    expect(calls.some((call) => call.path === "POST /auth/logout")).toBe(true);
  });
});
```

- [ ] **Шаг 2: Запустить и убедиться, что падает**

Выполнить: `npm test -- src/app/Shell.test.tsx`
Ожидаем: FAIL — `./Shell` не найден.

- [ ] **Шаг 3: Реализация `web/src/app/Shell.tsx`**

```tsx
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { Button } from "../components/Button";
import { ru } from "../i18n/ru";
import styles from "./Shell.module.css";
import { useSession } from "./session";

function linkClass({ isActive }: { isActive: boolean }): string {
  return isActive ? `${styles.link} ${styles.active}` : styles.link;
}

export function Shell() {
  const { user, logout } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);

  // После перехода выдвижное меню на узком экране закрывается.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  // Временный пароль знает и тот, кто его выдал: до смены доступен
  // один экран, и ссылкам на остальные здесь делать нечего.
  const locked = user?.must_change_password === true;

  async function leave() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <span className={styles.brand}>{ru.app.name}</span>
        <button
          type="button"
          className={styles.menuButton}
          aria-expanded={open}
          aria-controls="sidebar"
          onClick={() => setOpen((value) => !value)}
        >
          {ru.nav.menu}
        </button>
      </header>

      <aside id="sidebar" className={open ? `${styles.sidebar} ${styles.open}` : styles.sidebar}>
        <div className={styles.brandLarge}>{ru.app.name}</div>

        {locked ? null : (
          <nav aria-label={ru.nav.main} className={styles.nav}>
            <NavLink to="/" end className={linkClass}>
              {ru.nav.overview}
            </NavLink>
            <NavLink to="/agents" className={linkClass}>
              {ru.nav.agents}
            </NavLink>
          </nav>
        )}

        <div className={styles.account}>
          <div className={styles.username}>{user?.username}</div>
          <div className={styles.role}>{user ? (ru.nav.roles[user.role] ?? user.role) : ""}</div>
          <Button onClick={leave}>{ru.nav.logout}</Button>
        </div>
      </aside>

      <main className={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}
```

`web/src/app/Shell.module.css`:

```css
.shell {
  display: grid;
  grid-template-columns: 240px 1fr;
  grid-template-areas: "sidebar content";
  min-height: 100%;
}

.topbar {
  display: none;
}

.sidebar {
  grid-area: sidebar;
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 20px 16px;
  background: var(--surface);
  border-right: 1px solid var(--border);
}

.brandLarge,
.brand {
  font-weight: 700;
  letter-spacing: 0.2px;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.link {
  padding: 8px 12px;
  border-radius: var(--radius);
  color: var(--text);
  text-decoration: none;
}

.link:hover {
  background: var(--surface-2);
}

.active {
  background: var(--info-bg);
  color: var(--info);
  font-weight: 600;
}

.account {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: auto;
}

.username {
  font-weight: 600;
}

.role {
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 13px;
}

.content {
  grid-area: content;
  min-width: 0;
  padding: 28px 32px;
}

.menuButton {
  min-height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font: inherit;
}

@media (min-width: 801px) {
  .menuButton {
    display: none;
  }
}

@media (max-width: 800px) {
  .shell {
    grid-template-columns: 1fr;
    grid-template-areas:
      "topbar"
      "sidebar"
      "content";
    grid-template-rows: auto auto 1fr;
  }

  .topbar {
    grid-area: topbar;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }

  .brandLarge {
    display: none;
  }

  .sidebar {
    display: none;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .open {
    display: flex;
  }

  .content {
    padding: 20px 16px;
  }
}
```

- [ ] **Шаг 4: Запустить тесты**

Выполнить: `npm test -- src/app/Shell.test.tsx`
Ожидаем: PASS, 3 теста.

- [ ] **Шаг 5: Зафиксировать**

```bash
git add web/src/app/Shell.tsx web/src/app/Shell.module.css web/src/app/Shell.test.tsx
git commit -m "feat(web): application shell with sidebar navigation

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Вход и смена пароля

**Файлы:**
- Создать: `web/src/features/auth/LoginPage.tsx`, `ChangePasswordPage.tsx`, `auth.module.css`
- Тест: `web/src/features/auth/LoginPage.test.tsx`, `web/src/features/auth/ChangePasswordPage.test.tsx`

**Интерфейсы:**
- Потребляет: `useSession()` (`user`, `loading`, `login`, `refresh`); `api`; `ApiError`; `Input`, `Button`, `Spinner`, `useToast`, `describeError`, `ru`.
- Производит: `LoginPage()`, `ChangePasswordPage()`. После успешного входа сессия попадает в кеш, и страница сама рисует `<Navigate>` на `location.state.from` или `/`.

- [ ] **Шаг 1: Написать падающие тесты**

`web/src/features/auth/LoginPage.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { LoginPage } from "./LoginPage";

const unauthenticated = json(401, { detail: "authentication required" });
const where = { route: "/login", path: "/login" };

describe("LoginPage", () => {
  it("успешный вход ведёт на главную", async () => {
    const { calls } = mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": json(200, OPERATOR),
    });
    renderPage(<LoginPage />, where);

    await userEvent.type(await screen.findByLabelText("Логин"), " ivanov ");
    await userEvent.type(screen.getByLabelText("Пароль"), "секрет-пароль-12");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/));
    const login = calls.find((call) => call.path === "POST /auth/login");
    expect(login?.body).toEqual({ username: "ivanov", password: "секрет-пароль-12" });
  });

  it("возвращает на страницу, с которой оператора увели на вход", async () => {
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": json(200, OPERATOR),
    });
    renderPage(<LoginPage />, {
      route: { pathname: "/login", state: { from: "/agents" } },
      path: "/login",
    });

    await userEvent.type(await screen.findByLabelText("Логин"), "ivanov");
    await userEvent.type(screen.getByLabelText("Пароль"), "секрет-пароль-12");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/agents"));
  });

  it("неверные данные показывают одно общее сообщение и остаются на форме", async () => {
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": json(401, { detail: "invalid credentials" }),
    });
    renderPage(<LoginPage />, where);

    await userEvent.type(await screen.findByLabelText("Логин"), "ivanov");
    await userEvent.type(screen.getByLabelText("Пароль"), "не-тот");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByText("Неверный логин или пароль")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/login");
    expect(screen.getByRole("button", { name: "Войти" })).toBeEnabled();
  });

  it("уже вошедшего сразу уводит с экрана входа", async () => {
    mockApi({ "GET /auth/me": json(200, OPERATOR) });
    renderPage(<LoginPage />, where);

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/));
  });
});
```

`web/src/features/auth/ChangePasswordPage.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FORCED, json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { ChangePasswordPage } from "./ChangePasswordPage";

const where = { route: "/password", path: "/password" };

async function fill(current: string, next: string, repeat: string) {
  await userEvent.type(await screen.findByLabelText("Текущий пароль"), current);
  await userEvent.type(screen.getByLabelText("Новый пароль"), next);
  await userEvent.type(screen.getByLabelText("Повторите новый пароль"), repeat);
  await userEvent.click(screen.getByRole("button", { name: "Сменить пароль" }));
}

describe("ChangePasswordPage", () => {
  it("объясняет принудительную смену", async () => {
    mockApi({ "GET /auth/me": json(200, FORCED) });
    renderPage(<ChangePasswordPage />, where);

    expect(await screen.findByText(/временный пароль/i)).toBeInTheDocument();
  });

  it("не отправляет запрос, если повтор не совпал", async () => {
    const { calls } = mockApi({ "GET /auth/me": json(200, FORCED) });
    renderPage(<ChangePasswordPage />, where);

    await fill("временный", "новый-пароль-123", "другой-пароль-123");

    expect(await screen.findByText("Пароли не совпадают")).toBeInTheDocument();
    expect(calls.some((call) => call.path === "POST /auth/password")).toBe(false);
  });

  it("неверный текущий пароль подсвечивается у своего поля", async () => {
    mockApi({
      "GET /auth/me": json(200, FORCED),
      "POST /auth/password": json(400, { detail: "current password does not match" }),
    });
    renderPage(<ChangePasswordPage />, where);

    await fill("не-тот", "новый-пароль-123", "новый-пароль-123");

    expect(await screen.findByText("Текущий пароль неверен")).toBeInTheDocument();
  });

  it("слишком короткий пароль показывает ответ сервера у поля нового пароля", async () => {
    mockApi({
      "GET /auth/me": json(200, FORCED),
      "POST /auth/password": json(422, {
        detail: [{ loc: ["body", "new_password"], msg: "String should have at least 12 characters" }],
      }),
    });
    renderPage(<ChangePasswordPage />, where);

    await fill("временный", "короткий", "короткий");

    expect(await screen.findByText(/at least 12 characters/)).toBeInTheDocument();
  });

  it("после смены перечитывает сессию и уходит на главную", async () => {
    let changed = false;
    const { calls } = mockApi({
      "GET /auth/me": () => json(200, changed ? OPERATOR : FORCED),
      "POST /auth/password": () => {
        changed = true;
        return json(204, null);
      },
    });
    renderPage(<ChangePasswordPage />, where);

    await fill("временный", "новый-пароль-123", "новый-пароль-123");

    expect(await screen.findByText("Пароль изменён")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/));
    const change = calls.find((call) => call.path === "POST /auth/password");
    expect(change?.body).toEqual({
      current_password: "временный",
      new_password: "новый-пароль-123",
    });
  });
});
```

- [ ] **Шаг 2: Запустить и убедиться, что падают**

Выполнить: `npm test -- src/features/auth`
Ожидаем: FAIL — страницы не найдены.

- [ ] **Шаг 3: Реализация**

`web/src/features/auth/LoginPage.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useSession } from "../../app/session";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Spinner } from "../../components/Spinner";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import styles from "./auth.module.css";

export function LoginPage() {
  const { user, loading, login } = useSession();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (loading) return <Spinner label={ru.login.checking} />;
  if (user) return <Navigate to={from} replace />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      // После успеха сессия попадает в кеш, и страница сама уходит
      // на <Navigate> выше. Пароль не обрезается: пробелы в нём значимы.
      await login(username.trim(), password);
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 401
          ? ru.login.invalid
          : describeError(failure),
      );
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <form className={styles.card} onSubmit={submit}>
        <h1 className={styles.title}>{ru.login.title}</h1>
        <Input
          label={ru.login.username}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          autoFocus
          required
        />
        <Input
          label={ru.login.password}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
        />
        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={busy}>
          {ru.login.submit}
        </Button>
      </form>
    </main>
  );
}
```

`web/src/features/auth/ChangePasswordPage.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import { useSession } from "../../app/session";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import styles from "./auth.module.css";

type Errors = Partial<Record<"current" | "next" | "repeat" | "form", string>>;

export function ChangePasswordPage() {
  const { user, refresh } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (next !== repeat) {
      setErrors({ repeat: ru.password.mismatch });
      return;
    }

    setBusy(true);
    setErrors({});

    try {
      await api.post<null>("/auth/password", { current_password: current, new_password: next });
      // До перечитывания в кеше всё ещё стоит must_change_password, и
      // охрана маршрутов вернула бы оператора на этот же экран.
      await refresh();
      toast.notify(ru.password.done, "ok");
      navigate("/", { replace: true });
    } catch (failure) {
      setBusy(false);

      if (failure instanceof ApiError) {
        if (failure.status === 400) {
          setErrors({ current: ru.password.wrongCurrent });
          return;
        }
        const fields = failure.fieldErrors;
        if (Object.keys(fields).length > 0) {
          setErrors({ current: fields.current_password, next: fields.new_password });
          return;
        }
      }
      setErrors({ form: describeError(failure) });
    }
  }

  return (
    <main className={styles.pageInline}>
      <form className={styles.card} onSubmit={submit}>
        <h1 className={styles.title}>{ru.password.title}</h1>
        {user?.must_change_password ? <p className={styles.notice}>{ru.password.forced}</p> : null}
        <Input
          label={ru.password.current}
          type="password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          autoComplete="current-password"
          error={errors.current}
          required
        />
        <Input
          label={ru.password.next}
          type="password"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          autoComplete="new-password"
          hint={ru.password.hint}
          error={errors.next}
          required
        />
        <Input
          label={ru.password.repeat}
          type="password"
          value={repeat}
          onChange={(event) => setRepeat(event.target.value)}
          autoComplete="new-password"
          error={errors.repeat}
          required
        />
        {errors.form ? (
          <p className={styles.error} role="alert">
            {errors.form}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={busy}>
          {ru.password.submit}
        </Button>
      </form>
    </main>
  );
}
```

`web/src/features/auth/auth.module.css`:

```css
.page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 24px 16px;
}

.pageInline {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: min(400px, 100%);
  padding: 28px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
}

.notice {
  margin: 0;
  padding: 10px 12px;
  background: var(--warn-bg);
  color: var(--warn);
  border-radius: var(--radius);
}

.error {
  margin: 0;
  color: var(--danger);
}
```

- [ ] **Шаг 4: Запустить тесты**

Выполнить: `npm test -- src/features/auth`
Ожидаем: PASS, 9 тестов.

- [ ] **Шаг 5: Зафиксировать**

```bash
git add web/src/features/auth
git commit -m "feat(web): login and forced password change screens

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Обзор

**Файлы:**
- Создать: `web/src/features/overview/useOverview.ts`, `web/src/features/overview/OverviewPage.tsx`
- Тест: `web/src/features/overview/OverviewPage.test.tsx`

**Интерфейсы:**
- Потребляет: `api`, тип `Overview`, `Spinner`, `ErrorState`, `page.module.css`, `formatNumber`, `ru`.
- Производит: `OVERVIEW_KEY`, `useOverview()`, `OverviewPage()`. Карточки по статусам ведут на `/agents?status=<статус>`, «Всего» — на `/agents`.

- [ ] **Шаг 1: Написать падающий тест**

`web/src/features/overview/OverviewPage.test.tsx`:

```tsx
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
```

- [ ] **Шаг 2: Запустить и убедиться, что падает**

Выполнить: `npm test -- src/features/overview`
Ожидаем: FAIL — `./OverviewPage` не найден.

- [ ] **Шаг 3: Реализация**

`web/src/features/overview/useOverview.ts`:

```ts
import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { Overview } from "../../api/types";

export const OVERVIEW_KEY = ["overview"] as const;

export function useOverview() {
  return useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => api.get<Overview>("/overview"),
  });
}
```

`web/src/features/overview/OverviewPage.tsx`:

```tsx
import { Link } from "react-router-dom";

import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { ru } from "../../i18n/ru";
import { formatNumber } from "../../lib/format";
import page from "../../styles/page.module.css";
import { useOverview } from "./useOverview";

function Stat({ label, value, to }: { label: string; value: number; to?: string }) {
  const body = (
    <>
      <div className={page.cardLabel}>{label}</div>
      <div className={page.cardValue}>{formatNumber(value)}</div>
    </>
  );

  return to ? (
    <Link to={to} className={page.card}>
      {body}
    </Link>
  ) : (
    <div className={page.card}>{body}</div>
  );
}

function Distribution({
  title,
  rows,
}: {
  title: string;
  rows: { value: string; count: number }[];
}) {
  return (
    <section className={page.section}>
      <h2 className={page.sectionTitle}>{title}</h2>
      {rows.length === 0 ? (
        <p className={page.muted}>{ru.overview.noData}</p>
      ) : (
        <ul className={page.list}>
          {rows.map((row) => (
            <li key={row.value} className={page.row}>
              <span>{row.value}</span>
              <span>{formatNumber(row.count)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const STATUSES = ["active", "offline", "pending", "quarantined", "revoked"] as const;

export function OverviewPage() {
  const overview = useOverview();

  if (overview.isPending) return <Spinner label={ru.common.loading} />;
  if (overview.isError) {
    return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  }

  const data = overview.data;

  return (
    <>
      <h1 className={page.title}>{ru.overview.title}</h1>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.overview.agents}</h2>
        <div className={page.grid}>
          <Stat label={ru.overview.total} value={data.agents.total} to="/agents" />
          {STATUSES.map((status) => (
            <Stat
              key={status}
              label={ru.status.agent[status] ?? status}
              value={data.agents[status]}
              to={`/agents?status=${status}`}
            />
          ))}
        </div>
      </section>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.overview.commands}</h2>
        <div className={page.grid}>
          <Stat label={ru.overview.queued} value={data.commands.queued} />
          <Stat label={ru.overview.failed24h} value={data.commands.failed_24h} />
          <Stat label={ru.overview.certificates} value={data.certificates_expiring} />
          <Stat label={ru.overview.tokens} value={data.tokens_active} />
        </div>
      </section>

      <div className={page.grid}>
        <Distribution title={ru.overview.versions} rows={data.agent_versions} />
        <Distribution title={ru.overview.systems} rows={data.operating_systems} />
      </div>
    </>
  );
}
```

- [ ] **Шаг 4: Запустить тесты**

Выполнить: `npm test -- src/features/overview`
Ожидаем: PASS, 2 теста.

- [ ] **Шаг 5: Зафиксировать**

```bash
git add web/src/features/overview
git commit -m "feat(web): fleet overview screen

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Список агентов

**Файлы:**
- Создать: `web/src/lib/agentFilters.ts`, `web/src/lib/agentFilters.test.ts`, `web/src/features/agents/queries.ts`, `web/src/features/agents/AgentListPage.tsx`
- Тест: `web/src/features/agents/AgentListPage.test.tsx`

**Интерфейсы:**
- Потребляет: `api`, типы `AgentPage`, `GroupSummary`; `Table`, `Pagination`, `Input`, `Select`, `Button`, `AgentStatusBadge`, `EmptyState`, `ErrorState`, `Spinner`; `relativeTime`; `ru`.
- Производит:
  - `AgentFilters { q: string; status: string; groupId: string; page: number }`, `parseFilters(params: URLSearchParams): AgentFilters`, `toSearchParams(filters: AgentFilters): URLSearchParams`
  - `PAGE_SIZE = 25`, `useAgents(filters)`, `useGroups()`
  - `AgentListPage()`

- [ ] **Шаг 1: Написать падающие тесты**

`web/src/lib/agentFilters.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { parseFilters, toSearchParams } from "./agentFilters";

describe("фильтры списка агентов", () => {
  it("читает все параметры", () => {
    const filters = parseFilters(new URLSearchParams("q=ws&status=offline&group_id=g1&page=3"));

    expect(filters).toEqual({ q: "ws", status: "offline", groupId: "g1", page: 3 });
  });

  it("пустой URL даёт значения по умолчанию", () => {
    expect(parseFilters(new URLSearchParams())).toEqual({
      q: "",
      status: "",
      groupId: "",
      page: 1,
    });
  });

  it.each(["abc", "0", "-3", "1.5", "2x", ""])("мусорная страница %j сводится к первой", (value) => {
    expect(parseFilters(new URLSearchParams({ page: value })).page).toBe(1);
  });

  it("не пишет в URL значения по умолчанию", () => {
    const params = toSearchParams({ q: "", status: "", groupId: "", page: 1 });

    expect(params.toString()).toBe("");
  });

  it("пишет заданные фильтры и страницу после первой", () => {
    const params = toSearchParams({ q: "ws", status: "offline", groupId: "g1", page: 2 });

    expect(params.get("q")).toBe("ws");
    expect(params.get("status")).toBe("offline");
    expect(params.get("group_id")).toBe("g1");
    expect(params.get("page")).toBe("2");
  });
});
```

`web/src/features/agents/AgentListPage.test.tsx`:

```tsx
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

    expect(screen.getByTestId("location")).toHaveTextContent("/agents?status=offline");
  });

  it("поиск по Enter пишется в URL", async () => {
    setup(agentsPage([AGENT]));
    renderPage(<AgentListPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.type(screen.getByLabelText("Поиск по имени хоста"), "srv{Enter}");

    expect(screen.getByTestId("location")).toHaveTextContent("/agents?q=srv");
  });

  it("переход на следующую страницу пишется в URL", async () => {
    setup(agentsPage([AGENT], 60));
    renderPage(<AgentListPage />, route());
    await screen.findByRole("link", { name: "ws-01" });

    await userEvent.click(screen.getByRole("button", { name: "Вперёд" }));

    expect(screen.getByTestId("location")).toHaveTextContent("/agents?page=2");
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
});
```

- [ ] **Шаг 2: Запустить и убедиться, что падают**

Выполнить: `npm test -- src/lib/agentFilters.test.ts src/features/agents/AgentListPage.test.tsx`
Ожидаем: FAIL — модули не найдены.

- [ ] **Шаг 3: Фильтры в URL, `web/src/lib/agentFilters.ts`**

```ts
export interface AgentFilters {
  q: string;
  status: string;
  groupId: string;
  page: number;
}

export function parseFilters(params: URLSearchParams): AgentFilters {
  // Номер страницы — целое с единицы; всё остальное в адресной строке
  // чужая рука, и запрос с отрицательным смещением сервер отвергнет.
  const raw = params.get("page") ?? "";
  const page = /^\d+$/.test(raw) ? Number.parseInt(raw, 10) : 1;

  return {
    q: params.get("q") ?? "",
    status: params.get("status") ?? "",
    groupId: params.get("group_id") ?? "",
    page: page >= 1 ? page : 1,
  };
}

export function toSearchParams(filters: AgentFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.status) params.set("status", filters.status);
  if (filters.groupId) params.set("group_id", filters.groupId);
  if (filters.page > 1) params.set("page", String(filters.page));
  return params;
}
```

- [ ] **Шаг 4: Запросы, `web/src/features/agents/queries.ts`**

```ts
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { AgentPage, GroupSummary } from "../../api/types";
import type { AgentFilters } from "../../lib/agentFilters";

export const PAGE_SIZE = 25;

export function useAgents(filters: AgentFilters) {
  return useQuery({
    queryKey: ["agents", filters],
    queryFn: () =>
      api.get<AgentPage>("/agents", {
        q: filters.q,
        status: filters.status,
        group_id: filters.groupId,
        limit: PAGE_SIZE,
        offset: (filters.page - 1) * PAGE_SIZE,
      }),
    // Таблица не должна мигать пустотой, пока грузится следующая страница.
    placeholderData: keepPreviousData,
  });
}

export function useGroups() {
  return useQuery({
    queryKey: ["groups"],
    queryFn: () => api.get<GroupSummary[]>("/groups"),
    staleTime: 60_000,
  });
}
```

- [ ] **Шаг 5: Страница, `web/src/features/agents/AgentListPage.tsx`**

```tsx
import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Pagination } from "../../components/Pagination";
import { Select } from "../../components/Select";
import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { AgentStatusBadge } from "../../components/StatusBadge";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import { parseFilters, toSearchParams, type AgentFilters } from "../../lib/agentFilters";
import { relativeTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import { PAGE_SIZE, useAgents, useGroups } from "./queries";

export function AgentListPage() {
  const [params, setParams] = useSearchParams();
  const filters = parseFilters(params);
  const agents = useAgents(filters);
  const groups = useGroups();
  const [draft, setDraft] = useState(filters.q);

  // Кнопка «назад» меняет q в адресе; поле должно за ним последовать.
  useEffect(() => {
    setDraft(filters.q);
  }, [filters.q]);

  function update(patch: Partial<AgentFilters>) {
    setParams(toSearchParams({ ...filters, page: 1, ...patch }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update({ q: draft.trim() });
  }

  const filtered = Boolean(filters.q || filters.status || filters.groupId);

  const statusOptions = [
    { value: "", label: ru.agents.anyStatus },
    ...Object.entries(ru.status.agent).map(([value, label]) => ({ value, label })),
  ];
  const groupOptions = [
    { value: "", label: ru.agents.anyGroup },
    ...(groups.data ?? []).map((group) => ({ value: group.id, label: group.name })),
  ];

  function body() {
    if (agents.isPending) return <Spinner label={ru.common.loading} />;
    if (agents.isError) {
      return <ErrorState error={agents.error} onRetry={() => void agents.refetch()} />;
    }

    const { items, total } = agents.data;

    if (items.length === 0) {
      // Список пуст, но агенты есть: оператор ушёл за последнюю страницу.
      if (total > 0) {
        return (
          <EmptyState
            title={ru.agents.emptyPage}
            hint={ru.agents.emptyPageHint}
            action={<Button onClick={() => update({ page: 1 })}>{ru.agents.firstPage}</Button>}
          />
        );
      }
      if (filtered) {
        return (
          <EmptyState
            title={ru.agents.emptyFilter}
            hint={ru.agents.emptyFilterHint}
            action={
              <Button onClick={() => setParams(new URLSearchParams())}>{ru.agents.reset}</Button>
            }
          />
        );
      }
      return <EmptyState title={ru.agents.emptyFleet} hint={ru.agents.emptyFleetHint} />;
    }

    return (
      <>
        <Table caption={ru.agents.caption}>
          <thead>
            <tr>
              <th>{ru.agents.columns.host}</th>
              <th>{ru.agents.columns.status}</th>
              <th>{ru.agents.columns.os}</th>
              <th>{ru.agents.columns.group}</th>
              <th>{ru.agents.columns.heartbeat}</th>
              <th>{ru.agents.columns.version}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((agent) => (
              <tr key={agent.id}>
                <td>
                  <Link to={`/agents/${agent.id}`}>{agent.hostname}</Link>
                </td>
                <td>
                  <AgentStatusBadge status={agent.status} />
                </td>
                <td>
                  {agent.os} {agent.os_version}
                </td>
                <td>{agent.group_name ?? ru.agents.noGroup}</td>
                <td>{relativeTime(agent.last_heartbeat_at)}</td>
                <td>{agent.agent_version}</td>
              </tr>
            ))}
          </tbody>
        </Table>
        <Pagination
          total={total}
          limit={PAGE_SIZE}
          offset={(filters.page - 1) * PAGE_SIZE}
          onChange={(offset) => update({ page: offset / PAGE_SIZE + 1 })}
        />
      </>
    );
  }

  return (
    <>
      <h1 className={page.title}>{ru.agents.title}</h1>

      <form className={page.toolbar} onSubmit={submit}>
        <Input
          label={ru.agents.search}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Select
          label={ru.agents.status}
          value={filters.status}
          options={statusOptions}
          onChange={(event) => update({ status: event.target.value })}
        />
        <Select
          label={ru.agents.group}
          value={filters.groupId}
          options={groupOptions}
          onChange={(event) => update({ groupId: event.target.value })}
        />
        <Button type="submit">{ru.agents.searchSubmit}</Button>
      </form>

      {body()}
    </>
  );
}
```

Порядок статусов в выпадающем списке повторяет порядок ключей `ru.status.agent`.

- [ ] **Шаг 6: Запустить тесты**

Выполнить: `npm test -- src/lib/agentFilters.test.ts src/features/agents/AgentListPage.test.tsx`
Ожидаем: PASS (10 тестов `agentFilters` вместе с `each`, 9 тестов страницы).

- [ ] **Шаг 7: Зафиксировать**

```bash
git add web/src/lib web/src/features/agents
git commit -m "feat(web): agent list with URL-driven filters and pagination

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Карточка агента — сведения, история, команда, отзыв

**Файлы:**
- Создать: `web/src/features/agents/Fact.tsx`, `CommandHistory.tsx`, `SendCommandDialog.tsx`, `RevokeDialog.tsx`, `AgentDetailPage.tsx`
- Изменить: `web/src/features/agents/queries.ts` (импорты и новые хуки)
- Тест: `web/src/features/agents/AgentDetailPage.test.tsx`

**Интерфейсы:**
- Потребляет: `api`, типы `AgentDetail`, `CommandPage`, `CommandResponse`, `CommandType`; `useSession`, `Modal`, `Select`, `Input`, `Button`, `useToast`, `describeError`, `formatDateTime`, `formatSkew`, `relativeTime`, `shortJson`, `OVERVIEW_KEY`.
- Производит: `useAgent(id)`, `useAgentCommands(id)`, `useSendCommand(id)`, `useRevokeAgent(id)`; `Fact({ label, children })`, `CommandHistory({ agentId })`, `SendCommandDialog({ agentId, hostname, open, onClose })`, `RevokeDialog({ agentId, hostname, open, onClose })`, `AgentDetailPage()`. Отзыв видит только `admin`; у отозванного агента действий нет.

- [ ] **Шаг 1: Написать падающий тест**

`web/src/features/agents/AgentDetailPage.test.tsx`:

```tsx
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
```



- [ ] **Шаг 2: Запустить и убедиться, что падает**

Выполнить: `npm test -- src/features/agents/AgentDetailPage.test.tsx`
Ожидаем: FAIL — `./AgentDetailPage` не найден.

- [ ] **Шаг 3: Хуки, `web/src/features/agents/queries.ts`**

Заменить блок импортов в начале файла:

```ts
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type {
  AgentDetail,
  AgentPage,
  CommandPage,
  CommandResponse,
  CommandType,
  GroupSummary,
} from "../../api/types";
import type { AgentFilters } from "../../lib/agentFilters";
import { OVERVIEW_KEY } from "../overview/useOverview";
```

Добавить в конец файла:

```ts
export function useAgent(id: string) {
  return useQuery({
    queryKey: ["agent", id],
    queryFn: () => api.get<AgentDetail>(`/agents/${id}`),
  });
}

export function useAgentCommands(id: string) {
  return useQuery({
    queryKey: ["agent-commands", id],
    queryFn: () => api.get<CommandPage>("/commands", { agent_id: id, limit: 20 }),
  });
}

export function useSendCommand(id: string) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (type: CommandType) => api.post<CommandResponse>(`/agents/${id}/commands`, { type }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["agent-commands", id] });
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY });
    },
  });
}

export function useRevokeAgent(id: string) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (reason: string) => api.post<null>(`/agents/${id}/revoke`, { reason }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["agent", id] });
      void client.invalidateQueries({ queryKey: ["agents"] });
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY });
    },
  });
}
```

- [ ] **Шаг 4: Компоненты карточки**

`web/src/features/agents/Fact.tsx`:

```tsx
import type { ReactNode } from "react";

import page from "../../styles/page.module.css";

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={page.fact}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
```

`web/src/features/agents/CommandHistory.tsx`:

```tsx
import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { CommandStatusBadge } from "../../components/StatusBadge";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import { formatDateTime, shortJson } from "../../lib/format";
import page from "../../styles/page.module.css";
import { useAgentCommands } from "./queries";

export function CommandHistory({ agentId }: { agentId: string }) {
  const commands = useAgentCommands(agentId);

  if (commands.isPending) return <Spinner label={ru.common.loading} />;
  if (commands.isError) {
    return <ErrorState error={commands.error} onRetry={() => void commands.refetch()} />;
  }
  if (commands.data.items.length === 0) return <EmptyState title={ru.agent.noCommands} />;

  const columns = ru.agent.historyColumns;

  return (
    <Table caption={ru.agent.history}>
      <thead>
        <tr>
          <th>{columns.type}</th>
          <th>{columns.status}</th>
          <th>{columns.created}</th>
          <th>{columns.completed}</th>
          <th>{columns.result}</th>
        </tr>
      </thead>
      <tbody>
        {commands.data.items.map((command) => (
          <tr key={command.id}>
            <td>{ru.commandType[command.type] ?? command.type}</td>
            <td>
              <CommandStatusBadge status={command.status} />
            </td>
            <td>{formatDateTime(command.created_at)}</td>
            <td>{formatDateTime(command.completed_at)}</td>
            <td className={page.mono}>{shortJson(command.result)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
```

`web/src/features/agents/SendCommandDialog.tsx`:

```tsx
import { useState } from "react";

import type { CommandType } from "../../api/types";
import { Button } from "../../components/Button";
import { Modal } from "../../components/Modal";
import { Select } from "../../components/Select";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { useSendCommand } from "./queries";

const TYPES: CommandType[] = ["ping", "refresh_config", "collect_diagnostics"];

interface Props {
  agentId: string;
  hostname: string;
  open: boolean;
  onClose: () => void;
}

export function SendCommandDialog({ agentId, hostname, open, onClose }: Props) {
  const [type, setType] = useState<CommandType>("ping");
  const send = useSendCommand(agentId);
  const toast = useToast();

  async function confirm() {
    try {
      await send.mutateAsync(type);
      toast.notify(ru.agent.commandSent, "ok");
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <Modal
      open={open}
      title={ru.agent.commandTitle}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{ru.common.cancel}</Button>
          <Button variant="primary" loading={send.isPending} onClick={confirm}>
            {ru.agent.commandSend}
          </Button>
        </>
      }
    >
      <p>{hostname}</p>
      <Select
        label={ru.agent.commandType}
        value={type}
        options={TYPES.map((value) => ({ value, label: ru.commandType[value] ?? value }))}
        onChange={(event) => setType(event.target.value as CommandType)}
      />
    </Modal>
  );
}
```

`web/src/features/agents/RevokeDialog.tsx`:

```tsx
import { useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { useRevokeAgent } from "./queries";

interface Props {
  agentId: string;
  hostname: string;
  open: boolean;
  onClose: () => void;
}

export function RevokeDialog({ agentId, hostname, open, onClose }: Props) {
  const [reason, setReason] = useState("");
  const revoke = useRevokeAgent(agentId);
  const toast = useToast();

  async function confirm() {
    try {
      await revoke.mutateAsync(reason.trim());
      toast.notify(ru.agent.revoked, "ok");
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <Modal
      open={open}
      title={ru.agent.revokeTitle}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{ru.common.cancel}</Button>
          <Button
            variant="danger"
            loading={revoke.isPending}
            disabled={reason.trim() === ""}
            onClick={confirm}
          >
            {ru.agent.revokeConfirm}
          </Button>
        </>
      }
    >
      <p>{ru.agent.revokeWarning(hostname)}</p>
      <Input
        label={ru.agent.revokeReason}
        value={reason}
        maxLength={255}
        onChange={(event) => setReason(event.target.value)}
      />
    </Modal>
  );
}
```

`web/src/features/agents/AgentDetailPage.tsx`:

```tsx
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useSession } from "../../app/session";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { AgentStatusBadge } from "../../components/StatusBadge";
import { ru } from "../../i18n/ru";
import { formatDateTime, formatSkew, relativeTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import { CommandHistory } from "./CommandHistory";
import { Fact } from "./Fact";
import { RevokeDialog } from "./RevokeDialog";
import { SendCommandDialog } from "./SendCommandDialog";
import { useAgent } from "./queries";

export function AgentDetailPage() {
  const { id = "" } = useParams();
  const { user } = useSession();
  const agent = useAgent(id);
  const [sending, setSending] = useState(false);
  const [revoking, setRevoking] = useState(false);

  if (agent.isPending) return <Spinner label={ru.common.loading} />;
  if (agent.isError) {
    return <ErrorState error={agent.error} onRetry={() => void agent.refetch()} />;
  }

  const data = agent.data;
  const active = data.status !== "revoked";
  // Роль скрывает кнопку, но не защищает: отзыв сервер проверяет сам.
  const canRevoke = user?.role === "admin" && active;
  const certificate = data.certificate;
  const tags = Object.entries(data.tags);

  return (
    <>
      <Link to="/agents" className={page.back}>
        {ru.agent.back}
      </Link>
      <div className={page.titleRow}>
        <h1 className={page.title}>{data.hostname}</h1>
        <AgentStatusBadge status={data.status} />
      </div>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.agent.facts}</h2>
        <dl className={page.dl}>
          <Fact label={ru.agent.os}>
            {data.os} {data.os_version}
          </Fact>
          <Fact label={ru.agent.arch}>{data.arch}</Fact>
          <Fact label={ru.agent.version}>{data.agent_version}</Fact>
          <Fact label={ru.agent.group}>{data.group_name ?? ru.agents.noGroup}</Fact>
          <Fact label={ru.agent.ip}>{data.last_ip ?? ru.common.none}</Fact>
          <Fact label={ru.agents.columns.heartbeat}>{relativeTime(data.last_heartbeat_at)}</Fact>
          <Fact label={ru.agent.skew}>{formatSkew(data.clock_skew_ms)}</Fact>
          <Fact label={ru.agent.configVersion}>{data.config_version}</Fact>
          <Fact label={ru.agent.enrolledAt}>{formatDateTime(data.enrolled_at)}</Fact>
          <Fact label={ru.agent.machineId}>
            <span className={page.mono}>{data.machine_id}</span>
          </Fact>
          <Fact label={ru.agent.tags}>
            {tags.length === 0
              ? ru.common.none
              : tags.map(([key, value]) => `${key}: ${String(value)}`).join(", ")}
          </Fact>
        </dl>
      </section>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.agent.certificate}</h2>
        {certificate === null ? (
          <p className={page.muted}>{ru.agent.noCertificate}</p>
        ) : (
          <dl className={page.dl}>
            <Fact label={ru.agent.serial}>
              <span className={page.mono}>{certificate.serial}</span>
            </Fact>
            <Fact label={ru.agent.fingerprint}>
              <span className={page.mono}>{certificate.fingerprint_sha256}</span>
            </Fact>
            <Fact label={ru.agent.validFrom}>{formatDateTime(certificate.not_before)}</Fact>
            <Fact label={ru.agent.validTo}>{formatDateTime(certificate.not_after)}</Fact>
            {certificate.revoked_at ? (
              <Fact label={ru.agent.revokedAt}>{formatDateTime(certificate.revoked_at)}</Fact>
            ) : null}
          </dl>
        )}
      </section>

      {active ? (
        <section className={page.section}>
          <h2 className={page.sectionTitle}>{ru.agent.actions}</h2>
          <div className={page.actions}>
            <Button variant="primary" onClick={() => setSending(true)}>
              {ru.agent.sendCommand}
            </Button>
            {canRevoke ? (
              <Button variant="danger" onClick={() => setRevoking(true)}>
                {ru.agent.revoke}
              </Button>
            ) : null}
          </div>
        </section>
      ) : null}

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.agent.history}</h2>
        <CommandHistory agentId={id} />
      </section>

      <SendCommandDialog
        agentId={id}
        hostname={data.hostname}
        open={sending}
        onClose={() => setSending(false)}
      />
      <RevokeDialog
        agentId={id}
        hostname={data.hostname}
        open={revoking}
        onClose={() => setRevoking(false)}
      />
    </>
  );
}
```

После успешного отзыва карточка перечитывается, статус становится `revoked`, секция действий исчезает вместе с диалогом.

- [ ] **Шаг 5: Запустить тесты**

Выполнить: `npm test -- src/features/agents`
Ожидаем: PASS — тесты списка (9) и карточки (9).

- [ ] **Шаг 6: Зафиксировать**

```bash
git add web/src/features/agents
git commit -m "feat(web): agent detail with command history, commands and revoke

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Сборка приложения, проверка и документы

**Файлы:**
- Создать: `web/src/App.tsx`, `web/src/main.tsx`
- Изменить: `docs/superpowers/specs/2026-10-04-web-console-cycle-1-design.md` (статус)
- Тест: `web/src/App.test.tsx`

**Интерфейсы:**
- Потребляет: все страницы, `Shell`, `RequireAuth`, `SessionProvider`, `ToastProvider`.
- Производит: `App()` — таблица маршрутов; `main.tsx` — вход приложения.

- [ ] **Шаг 1: Написать падающий тест маршрутов**

`web/src/App.test.tsx`:

```tsx
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
```

`renderPage` оборачивает `ui` в свой `<Route path="/*">`; `App` содержит собственный `<Routes>`, и вложенные `Routes` внутри `/*` разбирают путь от корня, поэтому абсолютные маршруты `App` работают.

- [ ] **Шаг 2: Запустить и убедиться, что падает**

Выполнить: `npm test -- src/App.test.tsx`
Ожидаем: FAIL — `./App` не найден.

- [ ] **Шаг 3: Маршруты, `web/src/App.tsx`**

```tsx
import { Navigate, Route, Routes } from "react-router-dom";

import { RequireAuth } from "./app/RequireAuth";
import { Shell } from "./app/Shell";
import { AgentDetailPage } from "./features/agents/AgentDetailPage";
import { AgentListPage } from "./features/agents/AgentListPage";
import { ChangePasswordPage } from "./features/auth/ChangePasswordPage";
import { LoginPage } from "./features/auth/LoginPage";
import { OverviewPage } from "./features/overview/OverviewPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="agents" element={<AgentListPage />} />
        <Route path="agents/:id" element={<AgentDetailPage />} />
        <Route path="password" element={<ChangePasswordPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
```

`RequireAuth` пропускает на `/password` при `must_change_password` по `location.pathname`; вложенность под `Shell` это не меняет.

- [ ] **Шаг 4: Вход приложения, `web/src/main.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { SessionProvider } from "./app/session";
import { ToastProvider } from "./components/Toast";
import "./styles/global.css";

// Повторы по умолчанию скрыли бы сбой на три попытки; оператор нажмёт «Повторить».
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

const root = document.getElementById("root");
if (!root) throw new Error("в index.html нет #root");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ToastProvider>
          <SessionProvider>
            <App />
          </SessionProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
```

- [ ] **Шаг 5: Полная проверка**

Выполнить по порядку:

```bash
npm run typecheck
npm test
npm run build
npm run licenses
```

Ожидаем: `tsc` без ошибок; все тесты PASS (прежние 20 и новые); `vite build` создаёт `dist/`; `licenses` печатает summary без запрещённых лицензий. Любое падение разобрать до коммита, не ослабляя тест.

- [ ] **Шаг 6: Проверка против живого сервера**

Поднять сервер и консоль (две консоли):

```bash
cd server && .venv/Scripts/python -m uvicorn barysguard.main:app --port 8000
cd web && npm run dev
```

Создать оператора командой CLI сервера (`barysguard --help` показывает доступные команды) и пройти: вход → обзор → список агентов → карточка → команда `ping`. Если сервер или база недоступны — сообщить об этом явно, а не считать шаг выполненным.

- [ ] **Шаг 7: Обновить статус спеки**

В `docs/superpowers/specs/2026-10-04-web-console-cycle-1-design.md` заменить строку статуса на `**Статус:** реализовано (цикл 1)`.

- [ ] **Шаг 8: Зафиксировать**

```bash
git add web/src docs/superpowers
git commit -m "feat(web): wire routes and entry point; mark console cycle 1 complete

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Самопроверка плана

**Покрытие спеки.** Вход и смена пароля — Задача 6. Обзор — 7. Список (поиск, статус, группа, пагинация, состояние в URL) — 8. Карточка (сведения, сертификат, история, команда, отзыв только admin) — 9. Оболочка, адаптивность, отсутствие мёртвых ссылок — 5. UI-кит — 2–4. Ошибки 401/403/404/5xx — клиент (готов), `describeError`, `ErrorState` — 1, 3. Строки в одном файле — 1. Светлая и тёмная темы — 1. Критерии завершения (`typecheck`, `test`, `build`, `licenses`) — Задача 10.

**Review Focus.** Пункты 1–4 закреплены тестами Задачи 8 (`agentFilters.test.ts`, «за пределами списка», «без связи и без группы», «с разметкой»). Пункт 5 — тест «пока ответ на отзыв не пришёл» (Задача 9) и тест неизвестного статуса (Задача 2).

**Допущения, которые исполнитель проверяет по ходу.**
- Задачи 1–3 не используют `renderPage`, поэтому `test/utils.tsx` не компилируется до появления `Toast` в Задаче 4; это ожидаемо.
- Тесты написаны под поведение `jsdom` и `user-event` 14; если конкретный тест нестабилен из-за тайминга, чинится ожиданием (`waitFor`), а не ослаблением проверки.
- Точные имена команд CLI сервера для создания оператора берутся из `barysguard --help`.
