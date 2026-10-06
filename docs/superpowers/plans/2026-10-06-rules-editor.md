# Rules and Dictionary Editor (C2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Администратор управляет правилами инспекции из консоли: включает и отключает встроенные правила, меняет вес и потолок, правит словарь грифов, создаёт собственные словари и regex-правила с проверкой на тестовом тексте; в консоли есть инструкция.

**Architecture:** Сервер: regex на RE2 (линейное время) как ещё один детектор рядом со встроенными; сервис `rule_admin` публикует неизменяемые версии правил (вердикт ссылается на версию); `/api/v1/rules` только для администратора с аудитом. Консоль: страница «Правила» (список, окна создания и правки, термины, проверка шаблона, история версий, вкладка «Как создавать правила»). Сканер перестаёт приводить текст к нижнему регистру: регистр и `ё/е` решает словарный детектор.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async, Alembic, `google-re2`; React 19 + TypeScript + TanStack Query + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-06-rules-editor-design.md` (основа: `2026-10-06-dlp-worker-design.md`)

## Global Constraints

- Серверные команды запускать из `server/`: `BG_TEST_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard" .venv/Scripts/python -m pytest <путь> -q`; перед коммитом задачи: `.venv/Scripts/python -m ruff check .`, `.venv/Scripts/python -m ruff format --check .`, `.venv/Scripts/python -m mypy barysguard`. Консоль — из `web/`: `npx vitest run`, `npm run typecheck`, `npm run build`.
- Строки интерфейса, комментарии и докстринги — на русском, как в остальном коде; идентификаторы — английские. Строки консоли только в `web/src/i18n/ru.ts`. Длина строки Python 100.
- Файлы пишутся с LF (инструменты Write/Edit); не переписывать файлы через Python в текстовом режиме на Windows.
- Права: все `/api/v1/rules*` только `require_admin` (оператор и аноним получают 403/401); пункт меню и маршрут `/rules` только для администратора.
- RE2: у `\w`, `\d`, `\b` только ASCII-семантика; для кириллицы использовать `\p{L}`, `\p{Cyrillic}`, `\p{Nd}`. Lookahead/lookbehind и обратные ссылки не поддерживаются — это ошибка валидации, а не падение. Ошибки компиляции RE2 приходят как `re2.error` с байтовым текстом (`e.args[0].decode()`); логирование RE2 отключается (`Options.log_errors = False`).
- Лимиты: шаблон ≤ 500 символов (`BG_REGEX_MAX_PATTERN`), совпадение ≤ 200 символов (`BG_REGEX_MAX_MATCH`; более длинные не засчитываются), пустое совпадение запрещено, тестовый текст ≤ 20 000 символов (`BG_RULES_TEST_MAX_TEXT`), название ≤ 120, термин ≤ 200, не более 500 терминов за вызов, вес 1…100, потолок 1…50.
- Приватность: тестовый текст никогда не сохраняется, не логируется и не попадает в аудит и исключения; шаблон — конфигурация, в аудит попадает; образцы совпадений своих regex-правил маскируются (все символы, кроме двух последних, заменяются `*`); полные значения нигде не хранятся.
- Пороги вердикта (20/50/80) и порог инцидента не меняются. Встроенные правила нельзя удалить; удаления правил нет вообще. Отключение последнего включённого правила — 409.
- Коммиты оканчиваются строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; `prompt.txt` не коммитить.

## Review Focus

1. **RE2-ловушки.** Шаблон с `\w`/`\b`, не находящий кириллицу, и инструкция, которая об этом молчит; lookaround; шаблон, совпадающий с пустой строкой; совпадение длиннее лимита (Tasks 2, 4, 8).
2. **Правка оператора не должна теряться.** `seed-rules` не перезаписывает веса, версии и термины (Task 1).
3. **Одинаковый результат при любом разбиении текста.** `ContentScanner` без глобальной нормализации и с regex-детектором (Task 2).
4. **Только админ.** Каждый маршрут (включая `/rules/test` и термины) и меню (Tasks 5, 6).
5. **Нельзя остаться без правил** (409) и нельзя «сломать» воркер повреждённым шаблоном в БД (Tasks 3, 4).
6. **Утечки.** Тестовый текст и образцы в логах, аудите, ответах (Tasks 4, 5, 9).
7. **Гонки при правке.** Две одновременные правки одного правила или словаря не теряют версию (Task 4).

---

## File Structure

Создаются:
- `server/alembic/versions/20261006_1800_rules_editor.py`
- `server/barysguard/services/inspection/regex_detector.py`
- `server/barysguard/services/inspection/rule_admin.py`
- `server/barysguard/api/rules.py`
- Тесты: `server/tests/test_regex_detector.py`, `test_rule_admin.py`, `test_rules_api.py`, `test_rules_e2e.py`
- `web/src/features/rules/` — `queries.ts`, `RulesPage.tsx`, `RuleDialog.tsx`, `CreateRuleDialog.tsx`, `RuleTester.tsx`, `TermsEditor.tsx`, `VersionsList.tsx`, `GuideTab.tsx`, `rules.module.css`, тесты `RulesPage.test.tsx`, `RuleDialog.test.tsx`
- `web/src/components/Textarea.tsx`, `web/src/lib/highlight.ts` (+ тесты)
- `docs/RULES_GUIDE.md`

Меняются: `server/pyproject.toml`, `server/barysguard/core/config.py`, `server/barysguard/db/models/inspection.py`, `server/barysguard/services/inspection/{detectors,rules,scoring,incidents}.py`, `server/barysguard/api/{schemas,incidents}.py`, `server/barysguard/main.py`, существующие тесты правил/сканера/скоринга, `api/gateway-v1.yaml`, `web/src/api/{schema.d.ts,types.ts}`, `web/src/i18n/ru.ts`, `web/src/App.tsx`, `web/src/app/Shell.tsx`, `web/src/features/incidents/IncidentDetailPanel.tsx`, `docs/DLP_WORKER.md`.

---

### Task 1: Зависимость RE2, настройки, миграция, seed не перезаписывает правки

**Files:**
- Modify: `server/pyproject.toml`, `server/barysguard/core/config.py`, `server/barysguard/db/models/inspection.py`, `server/barysguard/services/inspection/rules.py`, `server/tests/test_inspection_rules.py`
- Create: `server/alembic/versions/20261006_1800_rules_editor.py`

**Interfaces:**
- Produces: `Rule.builtin: bool`, `Rule.updated_at: datetime`; `Settings.regex_max_pattern`, `regex_max_match`, `rules_test_max_text`; `seed_rules` создаёт правило и версию 1 только если их нет и помечает три встроенных `builtin=True`.

- [ ] **Step 1: Зависимость и настройки**

В `server/pyproject.toml` в `dependencies` добавить `"google-re2>=1.1",` (после `pypdf`). Модуль `re2` без типовых заглушек: в секцию mypy добавить

```toml
[[tool.mypy.overrides]]
module = ["re2"]
ignore_missing_imports = true
```

(поставить после существующей `[tool.mypy]`; если такой блок overrides уже есть — добавить `"re2"` в его `module`). Установить: `.venv/Scripts/python -m pip install "google-re2>=1.1"`.

В `server/barysguard/core/config.py` в конец класса `Settings` добавить:

```python

    # Правила-шаблоны (RE2) и проверка шаблона в консоли.
    regex_max_pattern: int = 500
    regex_max_match: int = 200
    rules_test_max_text: int = 20000
```

- [ ] **Step 2: Падающие тесты seed**

В `server/tests/test_inspection_rules.py` заменить два теста и добавить два (найти через `grep -n "versions_created" tests/test_inspection_rules.py`):

1. Тест, добавлявший термин вручную и ждавший новую версию после `seed_rules` (`test_changed_dictionary_gets_a_new_rule_version_and_ruleset_hash`), переписать так: после `seed_rules(session)` с добавленным термином `report.versions_created == 0`, версии markings остались `[1]`, а `after.hash != before.hash` (термины входят в хеш набора) — переименовать в `test_seed_does_not_publish_versions_for_edited_dictionary`.
2. Тест про удалённый встроенный термин (после fix-wave: «re-seed не возвращает термин и создаёт ровно одну новую версию») изменить: повторный `seed_rules` не создаёт версий (`versions_created == 0`), термин остаётся удалённым.
3. Добавить:

```python
async def test_seed_marks_the_three_rules_builtin(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()

    rows = (await session.scalars(select(Rule))).all()

    assert {r.key: r.builtin for r in rows} == {"iin_bin": True, "card": True, "markings": True}


async def test_seed_never_overwrites_operator_changes(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()
    card = (await session.scalars(select(Rule).where(Rule.key == "card"))).one()
    latest = (
        await session.scalars(select(RuleVersion).where(RuleVersion.rule_id == card.id))
    ).one()
    session.add(
        RuleVersion(
            rule_id=card.id, version=2, params={**latest.params, "weight": 99, "cap": 7}
        )
    )
    await session.commit()

    report = await seed_rules(session)
    await session.commit()
    ruleset = await load_ruleset(session)

    assert (report.rules_created, report.versions_created) == (0, 0)
    weights = {w.key: (w.weight, w.cap) for w in ruleset.weights()}
    assert weights["card"] == (99, 7)
```

- [ ] **Step 3: Запустить — упасть**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_rules.py -q` → FAIL (нет колонки `builtin`, seed перезаписывает).

- [ ] **Step 4: Модель**

В `server/barysguard/db/models/inspection.py` в класс `Rule` после поля `enabled` добавить:

```python
    # Встроенные правила (ИИН/БИН, карты, грифы): их нельзя удалить и нельзя менять тип и ключ.
    builtin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 5: Миграция**

`server/alembic/versions/20261006_1800_rules_editor.py`:

```python
"""rules editor: builtin flag and updated_at

Revision ID: d4a8b27c5e91
Revises: c91f5e2a7d34
Create Date: 2026-10-06 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a8b27c5e91"
down_revision: str | None = "c91f5e2a7d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rules", sa.Column("builtin", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column(
        "rules",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.execute("UPDATE rules SET builtin = true WHERE key IN ('iin_bin', 'card', 'markings')")


def downgrade() -> None:
    op.drop_column("rules", "updated_at")
    op.drop_column("rules", "builtin")
```

Проверить единственную голову: `.venv/Scripts/python -m alembic heads` → `d4a8b27c5e91`.

- [ ] **Step 6: seed_rules**

В `server/barysguard/services/inspection/rules.py` заменить докстринг `seed_rules` на:

```python
    """Заводит встроенные правила и словарь. Повторный вызов ничего не меняет.

    Правило и его версия 1 создаются только если их ещё нет; веса, версии и термины,
    которые поменял оператор, не перезаписываются. Новые значения по умолчанию
    в будущих релизах применяются отдельной миграцией данных.
    """
```

В цикле: создание `Rule(key=..., kind=..., title=..., builtin=True)`; условие создания версии заменить на `if latest is None:` (убрать сравнение `latest.params != params` и `latest.version + 1`, версия всегда `1`):

```python
        if latest is None:
            session.add(RuleVersion(rule_id=rule.id, version=1, params=params))
            versions_created += 1
```

- [ ] **Step 7: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_rules.py tests/test_cli.py tests/test_devstand.py tests/test_migrations.py -q` → PASS. Затем весь набор, `ruff check .`, `ruff format --check .`, `mypy barysguard`.

- [ ] **Step 8: Commit**

```bash
git add server/pyproject.toml server/barysguard server/alembic server/tests
git commit -m "feat(server): RE2 dependency, builtin flag and seed that keeps operator edits

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Сканер без глобальной нормализации и RegexDetector

**Files:**
- Modify: `server/barysguard/services/inspection/detectors.py`
- Create: `server/barysguard/services/inspection/regex_detector.py`, `server/tests/test_regex_detector.py`

**Interfaces:**
- Produces: `detectors.mask_tail(value: str, keep: int = 2) -> str`; `PatternError(Exception)` с `.message: str`; `compile_pattern(pattern: str, ignore_case: bool) -> Any` (RE2-шаблон; бросает `PatternError`); `RegexDetector(key: str, pattern: str, ignore_case: bool = False, max_match: int = 200)` с `max_length` и `find(...)`, как у остальных детекторов; `DictionaryDetector` теперь регистро- и `ё/е`-независим внутри; `ContentScanner.feed` текст не нормализует.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_regex_detector.py`:

```python
"""RegexDetector (RE2) и регистр/ё в словарном детекторе без глобальной нормализации."""

import pytest

from barysguard.services.inspection.detectors import (
    CardDetector,
    ContentScanner,
    DictionaryDetector,
    IinBinDetector,
    mask_tail,
)
from barysguard.services.inspection.regex_detector import (
    PatternError,
    RegexDetector,
    compile_pattern,
)


def _scan(text: str, *detectors):
    scanner = ContentScanner(list(detectors))
    scanner.feed(text)
    return scanner.finish()


def test_mask_keeps_the_last_two_characters() -> None:
    assert mask_tail("ALFA-123") == "******23"
    assert mask_tail("ab") == "**"
    assert mask_tail("abc") == "*bc"
    assert mask_tail("") == ""


def test_regex_finds_matches_masked() -> None:
    found = _scan("Договор №1234-567 и ещё № 9999-000.", RegexDetector("c", r"№\s?\d{4}-\d{3}"))

    assert found["c"].count == 2
    assert all(set(s[:-2]) == {"*"} for s in found["c"].samples)
    assert "1234-567" not in repr(found["c"].samples)


def test_ignore_case_applies_to_cyrillic() -> None:
    sensitive = RegexDetector("c", r"договор", ignore_case=False)
    insensitive = RegexDetector("c", r"договор", ignore_case=True)

    assert _scan("ДОГОВОР", sensitive)["c"].count == 0
    assert _scan("ДОГОВОР", insensitive)["c"].count == 1


def test_unicode_classes_work_but_w_is_ascii_only() -> None:
    assert _scan("слово", RegexDetector("c", r"\p{L}+"))["c"].count == 1
    assert _scan("слово", RegexDetector("c", r"\w+"))["c"].count == 0


def test_match_longer_than_the_limit_is_ignored() -> None:
    detector = RegexDetector("c", r"x{5,}", max_match=10)

    assert _scan("x" * 30, detector)["c"].count == 0
    assert _scan("xxxxxxx", detector)["c"].count == 1


@pytest.mark.parametrize("pattern", [r"(?=a)b", r"(?<=a)b", r"(a)\1", "(", "[a-"])
def test_unsupported_or_broken_patterns_raise_pattern_error(pattern: str) -> None:
    with pytest.raises(PatternError) as caught:
        compile_pattern(pattern, False)

    assert caught.value.message


def test_dictionary_ignores_case_and_yo_without_global_normalisation() -> None:
    detector = DictionaryDetector("m", ["Тёмный список", "секретно"])

    found = _scan("ТЁМНЫЙ СПИСОК, темный   список и СЕКРЕТНО", detector)

    assert found["m"].count == 3
    assert found["m"].samples[0] == "темный список"  # образец нормализован


def _document() -> str:
    return (
        "Договор №1234-567 заключён. Код ALFA-123 и alfa-99999. "
        + "9" * 30
        + " ИИН 900101300017 карта 4111 1111 1111 1111. Строго конфиденциально. "
        + "x" * 150
        + " Договор № 9999-000, ALFA-77 конец. секретно"
    )


@pytest.mark.parametrize("size", [1, 2, 3, 7, 13, 50, 64, 1000])
def test_chunking_does_not_change_the_result_with_regex(size: int) -> None:
    def detectors():
        return [
            IinBinDetector(),
            CardDetector(),
            DictionaryDetector("markings", ["конфиденциально", "секретно"]),
            RegexDetector("contract", r"№\s?\d{4}-\d{3}"),
            RegexDetector("alfa", r"alfa-\d{2,5}", ignore_case=True),
        ]

    whole = _scan(_document(), *detectors())
    scanner = ContentScanner(detectors())
    text = _document()
    for start in range(0, len(text), size):
        scanner.feed(text[start : start + size])
    chunked = scanner.finish()

    assert {k: (f.count, f.samples) for k, f in chunked.items()} == {
        k: (f.count, f.samples) for k, f in whole.items()
    }
    assert (whole["contract"].count, whole["alfa"].count) == (2, 3)
    assert whole["markings"].count == 2
```

- [ ] **Step 2: Запустить — упасть** (`ModuleNotFoundError: regex_detector`, `mask_tail`).

- [ ] **Step 3: Изменить detectors.py**

В `server/barysguard/services/inspection/detectors.py`:

1. После `normalize` добавить:

```python
def mask_tail(value: str, keep: int = 2) -> str:
    """Маска образца: все символы, кроме последних `keep`, заменяются `*`."""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
```

2. Заменить класс `DictionaryDetector` целиком на:

```python
_YO_CLASS = "[еёЕЁ]"


def _term_pattern(term: str) -> str:
    """Шаблон одного термина: е и ё взаимозаменяемы, пробелы между словами гибкие."""
    words = []
    for word in term.split():
        words.append("".join(_YO_CLASS if ch in "еёЕЁ" else re.escape(ch) for ch in word))
    # Между словами допускается до четырёх пробельных символов подряд.
    return r"\s{1,4}".join(words)


class DictionaryDetector:
    def __init__(self, key: str, terms: Iterable[str]) -> None:
        self.key = key
        unique: dict[str, str] = {}
        for term in terms:
            cleaned = " ".join(term.split())
            if cleaned:
                unique.setdefault(normalize(cleaned), cleaned)
        ordered = sorted(unique.values(), key=len, reverse=True)
        self.max_length = max((len(t) + 3 * t.count(" ") for t in ordered), default=0) + 2
        body = "|".join(_term_pattern(term) for term in ordered) if ordered else "(?!)"
        # Регистр и ё/е решает сам детектор: текст сканер не нормализует.
        self.pattern = re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int]]:
        for match in self.pattern.finditer(data, start):
            if match.start() >= end:
                return
            yield normalize(" ".join(match.group().split())), match.end()
```

3. В `ContentScanner.feed` заменить `data = self._carry + normalize(chunk)` на `data = self._carry + chunk`. Докстринг класса дополнить фразой «Текст не нормализуется: регистр учитывают сами детекторы.».

- [ ] **Step 4: Создать regex_detector.py**

`server/barysguard/services/inspection/regex_detector.py`:

```python
"""Правила-шаблоны на RE2: линейное время, без lookaround и обратных ссылок.

У RE2 `\\w`, `\\d` и `\\b` понимают только ASCII; для кириллицы нужны `\\p{L}`,
`\\p{Cyrillic}`. Ошибки компиляции приходят байтами и переводятся в PatternError.
"""

from collections.abc import Iterator
from typing import Any

import re2

from barysguard.services.inspection.detectors import mask_tail


class PatternError(Exception):
    """Шаблон не принят; `message` можно показать оператору (без текста файлов)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def compile_pattern(pattern: str, ignore_case: bool) -> Any:
    options = re2.Options()
    options.log_errors = False
    try:
        return re2.compile(("(?i)" if ignore_case else "") + pattern, options)
    except re2.error as exc:
        raw = exc.args[0] if exc.args else b""
        reason = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        raise PatternError(f"Шаблон не принят: {reason}") from None


class RegexDetector:
    def __init__(
        self, key: str, pattern: str, ignore_case: bool = False, max_match: int = 200
    ) -> None:
        self.key = key
        self.max_length = max_match
        self._max_match = max_match
        self._pattern = compile_pattern(pattern, ignore_case)

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int]]:
        for match in self._pattern.finditer(data, start):
            if match.start() >= end:
                return
            length = match.end() - match.start()
            # Пустые и слишком длинные совпадения не засчитываются: перекрытие
            # на стыке порций рассчитано на max_match.
            if length == 0 or length > self._max_match:
                continue
            yield mask_tail(match.group()), match.end()
```

- [ ] **Step 5: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_regex_detector.py tests/test_inspection_detectors.py tests/test_inspection_extract.py tests/test_inspection_scoring.py -q` → PASS (существующие тесты сканера и словаря остаются зелёными). Затем `ruff check .`, `ruff format --check .`, `mypy barysguard`.

Если `test_chunking_does_not_change_the_result_with_regex` падает при размерах порции 1–3, разобраться по существу (перекрытие `max_length + 2`, отсчёт `resume` у ContentScanner) и починить сканер, а не тест; «хвост» из 30 девяток нужен как раз для проверки отсутствия ложных совпадений на стыке.

- [ ] **Step 6: Commit**

```bash
git add server/barysguard server/tests
git commit -m "feat(server): RE2 regex detector and case-insensitive dictionary without global normalisation

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Regex-правила в наборе, названия правил в вердиктах и инцидентах

**Files:**
- Modify: `server/barysguard/services/inspection/rules.py`, `scoring.py`, `incidents.py`, `server/barysguard/api/schemas.py`, `server/barysguard/api/incidents.py`, `server/tests/test_inspection_scoring.py`, `server/tests/test_inspection_rules.py`, `server/tests/test_inspection_incidents.py`

**Interfaces:**
- Consumes: `RegexDetector`, `PatternError` (Task 2).
- Produces: `RuleRuntime(..., title: str, pattern: str, ignore_case: bool)`; `RuleWeight(key, rule_version_id, weight, cap, title: str = "")`; в `matches` вердикта у каждого элемента `rule_title`; `build_title` использует `rule_title` для не встроенных ключей; схема `IncidentMatch.rule_title: str = ""`.

- [ ] **Step 1: Падающие тесты**

В `server/tests/test_inspection_scoring.py` в тесте `test_matches_carry_rule_version_and_masked_samples` ожидаемый словарь дополнить `"rule_title": ""` (веса в файле создаются без названия) и добавить тест:

```python
def test_matches_carry_the_rule_title() -> None:
    weights = [RuleWeight("custom_ab12cd34", "v-x", weight=30, cap=2, title="Номер договора")]

    result = evaluate({"custom_ab12cd34": {"count": 3, "samples": ["**"]}}, weights)

    assert result.matches[0]["rule_title"] == "Номер договора"
    assert result.score == 60
```

В `server/tests/test_inspection_rules.py` добавить:

```python
async def test_regex_rule_is_loaded_and_detects(app_client, session) -> None:
    await seed_rules(session)
    rule = Rule(key="custom_aa11bb22", kind="regex", title="Номер договора", builtin=False)
    session.add(rule)
    await session.flush()
    session.add(
        RuleVersion(
            rule_id=rule.id,
            version=1,
            params={"pattern": r"№\s?\d{4}-\d{3}", "ignore_case": False, "weight": 30, "cap": 2},
        )
    )
    await session.commit()

    ruleset = await load_ruleset(session)

    assert any(w.key == "custom_aa11bb22" and w.title == "Номер договора" for w in ruleset.weights())
    scanner = ContentScanner(ruleset.detectors())
    scanner.feed("Договор №1234-567 подписан")
    assert scanner.finish()["custom_aa11bb22"].count == 1


async def test_broken_pattern_in_the_database_is_skipped_not_fatal(app_client, session) -> None:
    await seed_rules(session)
    rule = Rule(key="custom_bad00000", kind="regex", title="Сломанное", builtin=False)
    session.add(rule)
    await session.flush()
    session.add(
        RuleVersion(
            rule_id=rule.id,
            version=1,
            params={"pattern": "(?=a)b", "ignore_case": False, "weight": 10, "cap": 1},
        )
    )
    await session.commit()

    ruleset = await load_ruleset(session)
    detectors = ruleset.detectors()

    assert {d.key for d in detectors} == {"iin_bin", "card", "markings"}
```

(добавить недостающие импорты: `Rule`, `RuleVersion`, `ContentScanner`.)

В `server/tests/test_inspection_incidents.py` добавить:

```python
def test_title_uses_the_rule_title_for_custom_rules() -> None:
    matches = [
        {"rule_key": "iin_bin", "rule_title": "ИИН/БИН (Казахстан)", "count": 1},
        {"rule_key": "custom_aa11bb22", "rule_title": "Номер договора", "count": 2},
    ]

    assert build_title("copy", matches) == "Копирование на USB: ИИН/БИН ×1, Номер договора ×2"


def test_title_falls_back_to_the_key_without_a_title() -> None:
    assert build_title("copy", [{"rule_key": "custom_zz", "count": 1}]) == "Копирование на USB: custom_zz ×1"
```

- [ ] **Step 2: Запустить — упасть.**

- [ ] **Step 3: scoring.py**

`RuleWeight` получает поле `title: str = ""` (последним); в `evaluate` в словарь совпадения добавить `"rule_title": rule.title` (после `"rule_key"`).

- [ ] **Step 4: rules.py**

- импорт: `import logging`, `from barysguard.services.inspection.regex_detector import PatternError, RegexDetector`, и `from barysguard.core.config import get_settings` (для `regex_max_match`) — вместо вызова settings внутри `detectors()` принять `max_match` аргументом: `def detectors(self, max_match: int = 200)`.
- `logger = logging.getLogger("barysguard.rules")`.
- `RuleRuntime` дополнить полями `title: str`, `pattern: str = ""`, `ignore_case: bool = False` (поля с умолчаниями — в конец dataclass; `title` без умолчания разместить после `key`; поправить все конструкторы в тестах, если они есть).
- В `load_ruleset` при сборке `RuleRuntime` передавать `title=rule.title`, `pattern=str(version.params.get("pattern", ""))`, `ignore_case=bool(version.params.get("ignore_case", False))`.
- `detectors()`:

```python
            elif rule.kind == "regex":
                try:
                    result.append(
                        RegexDetector(rule.key, rule.pattern, rule.ignore_case, max_match)
                    )
                except PatternError:
                    # Повреждённый шаблон не должен останавливать воркер; текст шаблона в лог
                    # не пишется — достаточно ключа правила.
                    logger.error("правило пропущено: шаблон не компилируется", extra={"rule": rule.key})
```

- `weights()` → `RuleWeight(r.key, r.rule_version_id, r.weight, r.cap, r.title)`.
- Вызов `ruleset.detectors()` в `worker.py` (`_scan_for`) заменить на `ruleset.detectors(settings.regex_max_match)`.

- [ ] **Step 5: incidents.py**

В `build_title` заменить получение подписи правила на: `_RULE_LABELS.get(m["rule_key"]) or m.get("rule_title") or m["rule_key"]`. Порядок: встроенные ключи в каноническом порядке (как сейчас), остальные после них в порядке появления.

- [ ] **Step 6: API-схема инцидентов**

В `server/barysguard/api/schemas.py` в `IncidentMatch` добавить поле `rule_title: str = ""`; в `server/barysguard/api/incidents.py` при сборке `IncidentMatch(...)` передавать `rule_title=m.get("rule_title", "")`.

- [ ] **Step 7: Запустить и проверить**

Run: весь серверный набор, `ruff check .`, `ruff format --check .`, `mypy barysguard`. Ожидается всё зелёное; контракт OpenAPI изменился (`rule_title`) — тест `test_openapi_contract` упадёт до регенерации в Task 5; чтобы не оставлять красный набор, в этом шаге выполнить регенерацию (команда в Task 5, Step 6) и закоммитить `api/gateway-v1.yaml`; типы консоли `web/src/api/schema.d.ts` регенерируются там же (`npm run types`), `npm run typecheck` должен остаться зелёным.

- [ ] **Step 8: Commit**

```bash
git add server api web
git commit -m "feat(server): regex rules in the ruleset and rule titles in verdicts and incidents

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Сервис rule_admin

**Files:**
- Create: `server/barysguard/services/inspection/rule_admin.py`, `server/tests/test_rule_admin.py`

**Interfaces:**
- Consumes: `Rule`, `RuleVersion`, `Dictionary`, `DictionaryTerm`; `compile_pattern`, `PatternError`, `RegexDetector`; `terms_hash` (rules.py); `normalize`, `DictionaryDetector`, `ContentScanner`.
- Produces (всё в `rule_admin.py`):
  - `class RuleError(Exception)`: `.status_code: int`, `.message: str`.
  - `@dataclass(frozen=True) RuleView`: `id, key, kind, title, builtin, enabled, version, weight, cap, pattern, ignore_case, terms_count, updated_at`.
  - `@dataclass(frozen=True) TestOutcome`: `ok: bool, error: str | None, count: int, matches: list[tuple[int, int]]`.
  - `run_rule_test(kind, *, pattern, ignore_case, terms, text, max_pattern, max_match, max_text) -> TestOutcome`
  - `async list_rules(session) -> list[RuleView]`, `async get_rule(session, rule_id) -> RuleView`
  - `async create_rule(session, settings, *, kind, title, weight, cap, pattern, ignore_case, test_text, terms) -> RuleView`
  - `async update_rule(session, settings, rule_id, fields: dict[str, Any]) -> tuple[RuleView, dict[str, Any]]` — второй элемент: изменения для аудита `{поле: [было, стало]}`
  - `async add_terms(session, rule_id, terms) -> int`, `async delete_term(session, rule_id, term_id) -> None`, `async list_terms(session, rule_id, q, limit, offset) -> tuple[list[DictionaryTerm], int]`
  - `async list_versions(session, rule_id) -> list[RuleVersion]`
- Ошибки: `RuleError(404,…)` нет правила; `422` валидация; `409` отключение последнего включённого правила.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_rule_admin.py`:

```python
"""Сервис управления правилами: валидация, версии, термины."""

import pytest
from sqlalchemy import select

from barysguard.core.config import Settings
from barysguard.db.models.inspection import DictionaryTerm, Rule, RuleVersion
from barysguard.services.inspection.rule_admin import (
    RuleError,
    add_terms,
    create_rule,
    delete_term,
    get_rule,
    list_rules,
    list_terms,
    list_versions,
    run_rule_test,
    update_rule,
)
from barysguard.services.inspection.rules import load_ruleset, seed_rules

SETTINGS = Settings()
SAMPLE = "Договор №1234-567 подписан"


async def _seeded(session) -> None:
    await seed_rules(session)
    await session.commit()


async def _regex(session, **extra):
    args = {
        "kind": "regex",
        "title": "Номер договора",
        "weight": 30,
        "cap": 2,
        "pattern": r"№\s?\d{4}-\d{3}",
        "ignore_case": False,
        "test_text": SAMPLE,
        "terms": None,
    }
    args.update(extra)
    return await create_rule(session, SETTINGS, **args)


async def test_list_shows_the_three_builtin_rules(app_client, session) -> None:
    await _seeded(session)

    views = await list_rules(session)

    assert {v.key for v in views} == {"iin_bin", "card", "markings"}
    markings = next(v for v in views if v.key == "markings")
    assert markings.builtin and markings.terms_count == 7 and markings.version == 1
    assert next(v for v in views if v.key == "card").terms_count is None


async def test_create_regex_rule_needs_a_matching_test_text(app_client, session) -> None:
    await _seeded(session)

    view = await _regex(session)
    assert view.key.startswith("custom_") and not view.builtin and view.version == 1
    assert (view.pattern, view.weight, view.cap) == (r"№\s?\d{4}-\d{3}", 30, 2)

    with pytest.raises(RuleError) as no_text:
        await _regex(session, test_text=None)
    with pytest.raises(RuleError) as no_match:
        await _regex(session, test_text="здесь нет номера")
    assert (no_text.value.status_code, no_match.value.status_code) == (422, 422)


@pytest.mark.parametrize(
    "pattern", ["(?=a)b", "(", "a*", "x" * 501]
)  # lookahead, ошибка скобок, пустое совпадение, длина
async def test_bad_patterns_are_rejected(app_client, session, pattern: str) -> None:
    await _seeded(session)

    with pytest.raises(RuleError) as caught:
        await _regex(session, pattern=pattern, test_text="aaa")

    assert caught.value.status_code == 422 and caught.value.message


@pytest.mark.parametrize(
    "bad", [{"weight": 0}, {"weight": 101}, {"cap": 0}, {"cap": 51}, {"title": "  "}, {"title": "x" * 121}]
)
async def test_numbers_and_title_are_validated(app_client, session, bad: dict) -> None:
    await _seeded(session)

    with pytest.raises(RuleError) as caught:
        await _regex(session, **bad)

    assert caught.value.status_code == 422


async def test_create_dictionary_with_terms(app_client, session) -> None:
    await _seeded(session)

    view = await create_rule(
        session,
        SETTINGS,
        kind="dictionary",
        title="Проект Альфа",
        weight=20,
        cap=3,
        pattern=None,
        ignore_case=False,
        test_text=None,
        terms=["проект Альфа", "  Альфа-2  ", "проект   альфа", ""],
    )

    assert view.kind == "dictionary" and view.terms_count == 2
    ruleset = await load_ruleset(session)
    assert view.key in {w.key for w in ruleset.weights()}


async def test_update_publishes_a_version_only_when_parameters_change(app_client, session) -> None:
    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")

    renamed, _ = await update_rule(session, SETTINGS, card.id, {"title": "Карты"})
    assert renamed.version == 1 and renamed.title == "Карты"

    heavier, changes = await update_rule(session, SETTINGS, card.id, {"weight": 40})
    assert heavier.version == 2 and heavier.weight == 40
    assert changes == {"weight": [25, 40]}

    same, _ = await update_rule(session, SETTINGS, card.id, {"weight": 40})
    assert same.version == 2
    versions = await list_versions(session, card.id)
    assert [v.version for v in versions] == [2, 1]


async def test_toggle_does_not_create_a_version_but_changes_the_ruleset(app_client, session) -> None:
    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")
    before = await load_ruleset(session)

    off, _ = await update_rule(session, SETTINGS, card.id, {"enabled": False})
    after = await load_ruleset(session)

    assert off.enabled is False and off.version == 1
    assert after.hash != before.hash and "card" not in {w.key for w in after.weights()}


async def test_the_last_enabled_rule_cannot_be_disabled(app_client, session) -> None:
    await _seeded(session)
    views = await list_rules(session)
    for view in views[:-1]:
        await update_rule(session, SETTINGS, view.id, {"enabled": False})

    with pytest.raises(RuleError) as caught:
        await update_rule(session, SETTINGS, views[-1].id, {"enabled": False})

    assert caught.value.status_code == 409


async def test_builtin_rules_keep_their_nature(app_client, session) -> None:
    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")

    with pytest.raises(RuleError) as caught:
        await update_rule(session, SETTINGS, card.id, {"pattern": "x", "test_text": "x"})

    assert caught.value.status_code == 422


async def test_changing_a_pattern_needs_a_matching_test_text(app_client, session) -> None:
    await _seeded(session)
    view = await _regex(session)

    with pytest.raises(RuleError):
        await update_rule(session, SETTINGS, view.id, {"pattern": "ALFA-\\d+"})
    changed, changes = await update_rule(
        session, SETTINGS, view.id, {"pattern": "ALFA-\\d+", "test_text": "код ALFA-123"}
    )

    assert changed.version == 2 and changed.pattern == "ALFA-\\d+"
    assert changes["pattern"] == [r"№\s?\d{4}-\d{3}", "ALFA-\\d+"]


async def test_terms_add_delete_and_search_publish_versions(app_client, session) -> None:
    await _seeded(session)
    markings = next(v for v in await list_rules(session) if v.key == "markings")

    added = await add_terms(session, markings.id, ["Только для своих", "секретно", "  "])
    assert added == 1  # «секретно» уже есть (без учёта регистра), пустая строка отброшена
    assert (await get_rule(session, markings.id)).version == 2

    items, total = await list_terms(session, markings.id, "своих", 50, 0)
    assert total == 1 and items[0].term == "Только для своих"

    await delete_term(session, markings.id, items[0].id)
    assert (await get_rule(session, markings.id)).version == 3
    assert (await list_terms(session, markings.id, "своих", 50, 0))[1] == 0


async def test_terms_limits_and_wrong_rule_kind(app_client, session) -> None:
    await _seeded(session)
    views = await list_rules(session)
    markings = next(v for v in views if v.key == "markings")
    card = next(v for v in views if v.key == "card")

    with pytest.raises(RuleError) as long_term:
        await add_terms(session, markings.id, ["я" * 201])
    with pytest.raises(RuleError) as too_many:
        await add_terms(session, markings.id, [f"t{i}" for i in range(501)])
    with pytest.raises(RuleError) as wrong_kind:
        await add_terms(session, card.id, ["x"])

    assert [e.value.status_code for e in (long_term, too_many, wrong_kind)] == [422, 422, 422]


async def test_unknown_rule_is_404(app_client, session) -> None:
    import uuid

    with pytest.raises(RuleError) as caught:
        await get_rule(session, uuid.uuid4())

    assert caught.value.status_code == 404


def test_test_rule_reports_positions_and_errors() -> None:
    ok = run_rule_test(
        "regex", pattern=r"\d{3}", ignore_case=False, terms=None, text="a 123 b 456",
        max_pattern=500, max_match=200, max_text=20000,
    )
    bad = run_rule_test(
        "regex", pattern="(?=a)b", ignore_case=False, terms=None, text="x",
        max_pattern=500, max_match=200, max_text=20000,
    )
    words = run_rule_test(
        "dictionary", pattern=None, ignore_case=False, terms=["секретно"], text="Это СЕКРЕТНО!",
        max_pattern=500, max_match=200, max_text=20000,
    )

    assert ok.ok and ok.count == 2 and ok.matches == [(2, 5), (8, 11)]
    assert not bad.ok and bad.error and bad.count == 0
    assert words.ok and words.count == 1 and words.matches == [(4, 12)]


async def test_concurrent_edits_do_not_lose_versions(app_client, session, migrated_database_url) -> None:
    import asyncio

    from barysguard.db.session import create_engine_from_url, session_factory

    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")
    engine = create_engine_from_url(migrated_database_url)
    maker = session_factory(engine)

    async def edit(weight: int) -> None:
        async with maker() as other:
            await update_rule(other, SETTINGS, card.id, {"weight": weight})
            await other.commit()

    try:
        await asyncio.gather(edit(31), edit(32))
    finally:
        await engine.dispose()

    versions = (
        await session.scalars(select(RuleVersion.version).where(RuleVersion.rule_id == card.id))
    ).all()
    assert sorted(versions) == [1, 2, 3]
```

(`select`, `DictionaryTerm`, `Rule` импортируются там, где реально используются; убрать лишние импорты.)

- [ ] **Step 2: Запустить — упасть** (`ModuleNotFoundError: rule_admin`).

- [ ] **Step 3: Реализовать `rule_admin.py`**

```python
"""Управление правилами инспекции из консоли: проверка, версии, термины.

Правки идут под блокировкой строки правила; каждое изменение параметров (вес, потолок,
шаблон, термины) публикует новую неизменяемую версию. Тестовый текст нигде не сохраняется.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings
from barysguard.db.models.inspection import Dictionary, DictionaryTerm, Rule, RuleVersion
from barysguard.services.inspection.detectors import DictionaryDetector, normalize
from barysguard.services.inspection.regex_detector import PatternError, compile_pattern
from barysguard.services.inspection.rules import terms_hash

MAX_TITLE = 120
MAX_TERM = 200
MAX_TERMS_PER_CALL = 500
MAX_TEST_MATCHES = 200


class RuleError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class RuleView:
    id: uuid.UUID
    key: str
    kind: str
    title: str
    builtin: bool
    enabled: bool
    version: int
    weight: int
    cap: int
    pattern: str | None
    ignore_case: bool
    terms_count: int | None
    updated_at: datetime


@dataclass(frozen=True)
class TestOutcome:
    ok: bool
    error: str | None
    count: int
    matches: list[tuple[int, int]]


# --- проверка значений ----------------------------------------------------


def _int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RuleError(422, f"{name}: целое число от {low} до {high}")
    return value


def _title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title or len(title) > MAX_TITLE:
        raise RuleError(422, f"Название: от 1 до {MAX_TITLE} символов")
    return title


def clean_terms(terms: list[str] | None) -> list[str]:
    """Обрезает пробелы, отбрасывает пустые и повторы (без учёта регистра и ё/е)."""
    if terms is not None and len(terms) > MAX_TERMS_PER_CALL:
        raise RuleError(422, f"За один раз можно добавить не больше {MAX_TERMS_PER_CALL} терминов")
    seen: dict[str, str] = {}
    for raw in terms or []:
        term = " ".join(str(raw).split())
        if not term:
            continue
        if len(term) > MAX_TERM:
            raise RuleError(422, f"Термин длиннее {MAX_TERM} символов")
        seen.setdefault(normalize(term), term)
    return list(seen.values())


def check_pattern(pattern: Any, ignore_case: bool, max_pattern: int) -> str:
    if not isinstance(pattern, str) or not pattern.strip():
        raise RuleError(422, "Укажите шаблон")
    if len(pattern) > max_pattern:
        raise RuleError(422, f"Шаблон длиннее {max_pattern} символов")
    try:
        compiled = compile_pattern(pattern, ignore_case)
    except PatternError as exc:
        raise RuleError(422, exc.message) from None
    if compiled.search("") is not None:
        raise RuleError(422, "Шаблон совпадает с пустой строкой: уточните его")
    return pattern


def _positions(pattern: str, ignore_case: bool, text: str, max_match: int) -> list[tuple[int, int]]:
    compiled = compile_pattern(pattern, ignore_case)
    result: list[tuple[int, int]] = []
    for match in compiled.finditer(text):
        length = match.end() - match.start()
        if 0 < length <= max_match:
            result.append((match.start(), match.end()))
    return result


def _term_positions(terms: list[str], text: str) -> list[tuple[int, int]]:
    detector = DictionaryDetector("test", terms)
    result: list[tuple[int, int]] = []
    for match in detector.pattern.finditer(text):
        result.append((match.start(), match.end()))
    return result


def run_rule_test(
    kind: str,
    *,
    pattern: str | None,
    ignore_case: bool,
    terms: list[str] | None,
    text: str,
    max_pattern: int,
    max_match: int,
    max_text: int,
) -> TestOutcome:
    """Проверка правила на тексте оператора; текст не хранится, возвращаются позиции."""
    if len(text) > max_text:
        raise RuleError(422, f"Тестовый текст длиннее {max_text} символов")
    try:
        if kind == "regex":
            check_pattern(pattern, ignore_case, max_pattern)
            positions = _positions(str(pattern), ignore_case, text, max_match)
        elif kind == "dictionary":
            positions = _term_positions(clean_terms(terms), text)
        else:
            raise RuleError(422, "Тип правила: словарь или шаблон")
    except RuleError as exc:
        if exc.status_code == 422 and kind == "regex":
            return TestOutcome(False, exc.message, 0, [])
        raise
    return TestOutcome(True, None, len(positions), positions[:MAX_TEST_MATCHES])
```

Дальше в том же файле (импорты `RegexDetector`, `ContentScanner` в этом модуле не нужны — не импортировать):

```python
def _requires_match(pattern: str, ignore_case: bool, test_text: Any, settings: Settings) -> None:
    if not isinstance(test_text, str) or not test_text:
        raise RuleError(422, "Проверьте шаблон на тестовом тексте перед сохранением")
    if len(test_text) > settings.rules_test_max_text:
        raise RuleError(422, f"Тестовый текст длиннее {settings.rules_test_max_text} символов")
    if not _positions(pattern, ignore_case, test_text, settings.regex_max_match):
        raise RuleError(422, "На тестовом тексте нет совпадений: проверьте шаблон")


# --- чтение ----------------------------------------------------------------


async def _latest(session: AsyncSession, rule_id: uuid.UUID) -> RuleVersion:
    version = await session.scalar(
        select(RuleVersion)
        .where(RuleVersion.rule_id == rule_id)
        .order_by(RuleVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise RuleError(404, "Правило не найдено")
    return version


async def _terms_count(session: AsyncSession, params: dict[str, Any]) -> int | None:
    if "dictionary_id" not in params:
        return None
    return int(
        await session.scalar(
            select(func.count())
            .select_from(DictionaryTerm)
            .where(DictionaryTerm.dictionary_id == uuid.UUID(params["dictionary_id"]))
        )
        or 0
    )


async def _view(session: AsyncSession, rule: Rule, version: RuleVersion) -> RuleView:
    params = version.params
    return RuleView(
        id=rule.id,
        key=rule.key,
        kind=rule.kind,
        title=rule.title,
        builtin=rule.builtin,
        enabled=rule.enabled,
        version=version.version,
        weight=int(params["weight"]),
        cap=int(params["cap"]),
        pattern=params.get("pattern"),
        ignore_case=bool(params.get("ignore_case", False)),
        terms_count=await _terms_count(session, params),
        updated_at=rule.updated_at,
    )


async def list_rules(session: AsyncSession) -> list[RuleView]:
    latest = (
        select(RuleVersion.rule_id, func.max(RuleVersion.version).label("version"))
        .group_by(RuleVersion.rule_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Rule, RuleVersion)
            .join(latest, latest.c.rule_id == Rule.id)
            .join(
                RuleVersion,
                and_(
                    RuleVersion.rule_id == latest.c.rule_id,
                    RuleVersion.version == latest.c.version,
                ),
            )
            .order_by(Rule.builtin.desc(), Rule.created_at, Rule.key)
        )
    ).all()
    return [await _view(session, rule, version) for rule, version in rows]


async def _rule(session: AsyncSession, rule_id: uuid.UUID, *, lock: bool = False) -> Rule:
    statement = select(Rule).where(Rule.id == rule_id)
    if lock:
        statement = statement.with_for_update()
    rule = await session.scalar(statement.execution_options(populate_existing=True))
    if rule is None:
        raise RuleError(404, "Правило не найдено")
    return rule


async def get_rule(session: AsyncSession, rule_id: uuid.UUID) -> RuleView:
    rule = await _rule(session, rule_id)
    return await _view(session, rule, await _latest(session, rule.id))


async def list_versions(session: AsyncSession, rule_id: uuid.UUID) -> list[RuleVersion]:
    await _rule(session, rule_id)
    return list(
        (
            await session.scalars(
                select(RuleVersion)
                .where(RuleVersion.rule_id == rule_id)
                .order_by(RuleVersion.version.desc())
            )
        ).all()
    )


# --- запись ----------------------------------------------------------------


async def _publish(session: AsyncSession, rule: Rule, params: dict[str, Any]) -> RuleVersion:
    """Новая версия, если параметры изменились; иначе возвращается последняя."""
    latest = await _latest(session, rule.id)
    if latest.params == params:
        return latest
    version = RuleVersion(rule_id=rule.id, version=latest.version + 1, params=params)
    session.add(version)
    rule.updated_at = datetime.now(UTC)
    await session.flush()
    return version


async def create_rule(
    session: AsyncSession,
    settings: Settings,
    *,
    kind: str,
    title: str,
    weight: int,
    cap: int,
    pattern: str | None,
    ignore_case: bool,
    test_text: str | None,
    terms: list[str] | None,
) -> RuleView:
    if kind not in ("dictionary", "regex"):
        raise RuleError(422, "Тип правила: словарь или шаблон")
    title = _title(title)
    weight = _int("Вес", weight, 1, 100)
    cap = _int("Потолок", cap, 1, 50)
    key = f"custom_{uuid.uuid4().hex[:8]}"

    if kind == "regex":
        checked = check_pattern(pattern, ignore_case, settings.regex_max_pattern)
        _requires_match(checked, ignore_case, test_text, settings)
        rule = Rule(key=key, kind="regex", title=title, builtin=False)
        session.add(rule)
        await session.flush()
        params: dict[str, Any] = {
            "pattern": checked,
            "ignore_case": bool(ignore_case),
            "weight": weight,
            "cap": cap,
        }
    else:
        cleaned = clean_terms(terms)
        dictionary = Dictionary(key=key, title=title)
        session.add(dictionary)
        await session.flush()
        session.add_all(DictionaryTerm(dictionary_id=dictionary.id, term=t) for t in cleaned)
        rule = Rule(key=key, kind="dictionary", title=title, builtin=False)
        session.add(rule)
        await session.flush()
        params = {
            "weight": weight,
            "cap": cap,
            "dictionary_id": str(dictionary.id),
            "terms_hash": terms_hash(cleaned),
        }

    session.add(RuleVersion(rule_id=rule.id, version=1, params=params))
    await session.flush()
    return await get_rule(session, rule.id)


async def update_rule(
    session: AsyncSession, settings: Settings, rule_id: uuid.UUID, fields: dict[str, Any]
) -> tuple[RuleView, dict[str, Any]]:
    rule = await _rule(session, rule_id, lock=True)
    latest = await _latest(session, rule.id)
    params = dict(latest.params)
    changes: dict[str, Any] = {}

    if "title" in fields:
        new_title = _title(fields["title"])
        if new_title != rule.title:
            changes["title"] = [rule.title, new_title]
            rule.title = new_title

    if "enabled" in fields:
        enabled = bool(fields["enabled"])
        if enabled != rule.enabled:
            if not enabled:
                others = await session.scalar(
                    select(func.count())
                    .select_from(Rule)
                    .where(Rule.enabled.is_(True), Rule.id != rule.id)
                )
                if not others:
                    raise RuleError(409, "Нельзя отключить последнее включённое правило")
            changes["enabled"] = [rule.enabled, enabled]
            rule.enabled = enabled

    for name, label, low, high in (("weight", "Вес", 1, 100), ("cap", "Потолок", 1, 50)):
        if name in fields:
            value = _int(label, fields[name], low, high)
            if value != params[name]:
                changes[name] = [params[name], value]
                params[name] = value

    if "pattern" in fields or "ignore_case" in fields:
        if rule.kind != "regex" or rule.builtin:
            raise RuleError(422, "Шаблон есть только у собственных правил-шаблонов")
        pattern = fields.get("pattern", params["pattern"])
        ignore_case = bool(fields.get("ignore_case", params.get("ignore_case", False)))
        if pattern != params["pattern"] or ignore_case != params.get("ignore_case", False):
            checked = check_pattern(pattern, ignore_case, settings.regex_max_pattern)
            _requires_match(checked, ignore_case, fields.get("test_text"), settings)
            if checked != params["pattern"]:
                changes["pattern"] = [params["pattern"], checked]
            if ignore_case != params.get("ignore_case", False):
                changes["ignore_case"] = [params.get("ignore_case", False), ignore_case]
            params["pattern"] = checked
            params["ignore_case"] = ignore_case

    if params != latest.params:
        await _publish(session, rule, params)
    elif changes:
        rule.updated_at = datetime.now(UTC)
    await session.flush()
    return await get_rule(session, rule.id), changes


# --- термины ----------------------------------------------------------------


async def _dictionary_rule(session: AsyncSession, rule_id: uuid.UUID, *, lock: bool) -> tuple[Rule, uuid.UUID]:
    rule = await _rule(session, rule_id, lock=lock)
    if rule.kind != "dictionary":
        raise RuleError(422, "Термины есть только у словарных правил")
    latest = await _latest(session, rule.id)
    return rule, uuid.UUID(latest.params["dictionary_id"])


async def _republish_terms(session: AsyncSession, rule: Rule, dictionary_id: uuid.UUID) -> None:
    terms = (
        await session.scalars(
            select(DictionaryTerm.term).where(DictionaryTerm.dictionary_id == dictionary_id)
        )
    ).all()
    latest = await _latest(session, rule.id)
    await _publish(session, rule, {**latest.params, "terms_hash": terms_hash(terms)})


async def add_terms(session: AsyncSession, rule_id: uuid.UUID, terms: list[str]) -> int:
    rule, dictionary_id = await _dictionary_rule(session, rule_id, lock=True)
    cleaned = clean_terms(terms)
    existing = {
        normalize(t)
        for t in (
            await session.scalars(
                select(DictionaryTerm.term).where(DictionaryTerm.dictionary_id == dictionary_id)
            )
        ).all()
    }
    fresh = [t for t in cleaned if normalize(t) not in existing]
    session.add_all(DictionaryTerm(dictionary_id=dictionary_id, term=t) for t in fresh)
    await session.flush()
    if fresh:
        await _republish_terms(session, rule, dictionary_id)
    return len(fresh)


async def delete_term(session: AsyncSession, rule_id: uuid.UUID, term_id: uuid.UUID) -> None:
    rule, dictionary_id = await _dictionary_rule(session, rule_id, lock=True)
    result = await session.execute(
        delete(DictionaryTerm).where(
            DictionaryTerm.id == term_id, DictionaryTerm.dictionary_id == dictionary_id
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise RuleError(404, "Термин не найден")
    await session.flush()
    await _republish_terms(session, rule, dictionary_id)


async def list_terms(
    session: AsyncSession, rule_id: uuid.UUID, q: str | None, limit: int, offset: int
) -> tuple[list[DictionaryTerm], int]:
    _, dictionary_id = await _dictionary_rule(session, rule_id, lock=False)
    conditions = [DictionaryTerm.dictionary_id == dictionary_id]
    if q:
        conditions.append(DictionaryTerm.term.ilike(f"%{q}%"))
    total = int(
        await session.scalar(select(func.count()).select_from(DictionaryTerm).where(*conditions))
        or 0
    )
    items = (
        await session.scalars(
            select(DictionaryTerm)
            .where(*conditions)
            .order_by(DictionaryTerm.term)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(items), total
```

- [ ] **Step 4: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_rule_admin.py -q` → PASS (все тесты). Особенно проверить `test_concurrent_edits_do_not_lose_versions` (блокировка `FOR UPDATE` обязана дать версии `[1, 2, 3]`) и `test_the_last_enabled_rule_cannot_be_disabled`. Затем весь набор, ruff, mypy.

- [ ] **Step 5: Commit**

```bash
git add server/barysguard server/tests
git commit -m "feat(server): rule administration service with versions, terms and validation

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: API `/api/v1/rules`, контракт и типы консоли

**Files:**
- Modify: `server/barysguard/api/schemas.py`, `server/barysguard/main.py`, `api/gateway-v1.yaml`, `web/src/api/schema.d.ts`, `web/src/api/types.ts`
- Create: `server/barysguard/api/rules.py`, `server/tests/test_rules_api.py`

**Interfaces:**
- Consumes: сервис `rule_admin` (Task 4), `require_admin`, `record_audit`, `get_settings`.
- Produces: маршруты из спеки раздела 5; схемы `RuleSummary`, `RuleCreateRequest`, `RuleUpdateRequest`, `TermsAddRequest`, `TermItem`, `TermPage`, `RuleVersionItem`, `RuleTestRequest`, `RuleTestResponse`, `RuleMatchSpan`; типы консоли с теми же именами.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_rules_api.py`:

```python
"""/api/v1/rules: права, правила, термины, проверка шаблона, аудит."""

import uuid

from sqlalchemy import select

from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.inspection.rules import seed_rules
from tests.helpers import login_as

SAMPLE = "Договор №1234-567 подписан"


async def _admin(app_client, session, name="rules-admin"):
    await login_as(app_client, session, username=name, role=UserRole.ADMIN)
    await seed_rules(session)
    await session.commit()


async def _by_key(app_client, key: str) -> dict:
    items = (await app_client.get("/api/v1/rules")).json()
    return next(item for item in items if item["key"] == key)


async def test_every_route_is_closed_to_operators_and_anonymous(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()
    anonymous = await app_client.get("/api/v1/rules")
    assert anonymous.status_code == 401

    await login_as(app_client, session, username="rules-op")  # оператор
    rule_id = str(uuid.uuid4())
    calls = [
        ("get", "/api/v1/rules", None),
        ("post", "/api/v1/rules", {"kind": "dictionary", "title": "x", "weight": 1, "cap": 1}),
        ("get", f"/api/v1/rules/{rule_id}", None),
        ("patch", f"/api/v1/rules/{rule_id}", {"enabled": False}),
        ("get", f"/api/v1/rules/{rule_id}/versions", None),
        ("get", f"/api/v1/rules/{rule_id}/terms", None),
        ("post", f"/api/v1/rules/{rule_id}/terms", {"terms": ["a"]}),
        ("delete", f"/api/v1/rules/{rule_id}/terms/{uuid.uuid4()}", None),
        ("post", "/api/v1/rules/test", {"kind": "regex", "pattern": "a", "text": "a"}),
    ]
    for method, url, body in calls:
        response = await getattr(app_client, method)(url, **({"json": body} if body else {}))
        assert response.status_code == 403, (method, url, response.status_code)


async def test_list_has_builtin_rules_first(app_client, session) -> None:
    await _admin(app_client, session)

    items = (await app_client.get("/api/v1/rules")).json()

    assert {i["key"] for i in items} == {"iin_bin", "card", "markings"}
    assert all(i["builtin"] and i["enabled"] and i["version"] == 1 for i in items)
    markings = next(i for i in items if i["key"] == "markings")
    assert markings["kind"] == "dictionary" and markings["terms_count"] == 7


async def test_create_regex_rule_with_test_text(app_client, session) -> None:
    await _admin(app_client, session, "rules-create")

    created = await app_client.post(
        "/api/v1/rules",
        json={
            "kind": "regex", "title": "Номер договора", "weight": 30, "cap": 2,
            "pattern": r"№\s?\d{4}-\d{3}", "ignore_case": False, "test_text": SAMPLE,
        },
    )
    without_text = await app_client.post(
        "/api/v1/rules",
        json={"kind": "regex", "title": "x", "weight": 30, "cap": 2, "pattern": "a"},
    )
    lookahead = await app_client.post(
        "/api/v1/rules",
        json={"kind": "regex", "title": "x", "weight": 30, "cap": 2, "pattern": "(?=a)b", "test_text": "ab"},
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key"].startswith("custom_") and body["builtin"] is False and body["version"] == 1
    assert without_text.status_code == 422
    assert lookahead.status_code == 422 and "Шаблон не принят" in lookahead.text


async def test_patch_weight_enabled_and_errors(app_client, session) -> None:
    await _admin(app_client, session, "rules-patch")
    card = await _by_key(app_client, "card")

    heavier = await app_client.patch(f"/api/v1/rules/{card['id']}", json={"weight": 40})
    off = await app_client.patch(f"/api/v1/rules/{card['id']}", json={"enabled": False})
    bad = await app_client.patch(f"/api/v1/rules/{card['id']}", json={"weight": 0})
    unknown = await app_client.patch(f"/api/v1/rules/{uuid.uuid4()}", json={"enabled": False})

    assert heavier.json()["version"] == 2 and heavier.json()["weight"] == 40
    assert off.json()["enabled"] is False and off.json()["version"] == 2
    assert bad.status_code == 422 and unknown.status_code == 404


async def test_last_enabled_rule_cannot_be_disabled(app_client, session) -> None:
    await _admin(app_client, session, "rules-last")
    items = (await app_client.get("/api/v1/rules")).json()
    for item in items[:-1]:
        assert (await app_client.patch(f"/api/v1/rules/{item['id']}", json={"enabled": False})).status_code == 200

    last = await app_client.patch(f"/api/v1/rules/{items[-1]['id']}", json={"enabled": False})

    assert last.status_code == 409


async def test_terms_lifecycle(app_client, session) -> None:
    await _admin(app_client, session, "rules-terms")
    markings = await _by_key(app_client, "markings")
    base = f"/api/v1/rules/{markings['id']}"

    added = await app_client.post(f"{base}/terms", json={"terms": ["Только для своих", "секретно"]})
    page = await app_client.get(f"{base}/terms", params={"q": "своих"})
    term_id = page.json()["items"][0]["id"]
    deleted = await app_client.delete(f"{base}/terms/{term_id}")
    again = await app_client.delete(f"{base}/terms/{term_id}")
    versions = await app_client.get(f"{base}/versions")

    assert added.status_code == 200 and added.json() == {"added": 1}
    assert page.json()["total"] == 1 and page.json()["items"][0]["term"] == "Только для своих"
    assert deleted.status_code == 204 and again.status_code == 404
    assert [v["version"] for v in versions.json()] == [3, 2, 1]


async def test_terms_of_a_non_dictionary_rule_are_422(app_client, session) -> None:
    await _admin(app_client, session, "rules-terms-bad")
    card = await _by_key(app_client, "card")

    response = await app_client.get(f"/api/v1/rules/{card['id']}/terms")

    assert response.status_code == 422


async def test_test_endpoint_reports_positions_and_pattern_errors(app_client, session) -> None:
    await _admin(app_client, session, "rules-test")

    good = await app_client.post(
        "/api/v1/rules/test", json={"kind": "regex", "pattern": r"\d{3}", "text": "a 123 b 456"}
    )
    bad = await app_client.post(
        "/api/v1/rules/test", json={"kind": "regex", "pattern": "(?=a)b", "text": "x"}
    )
    words = await app_client.post(
        "/api/v1/rules/test", json={"kind": "dictionary", "terms": ["секретно"], "text": "Это СЕКРЕТНО"}
    )
    too_long = await app_client.post(
        "/api/v1/rules/test", json={"kind": "regex", "pattern": "a", "text": "a" * 20001}
    )

    assert good.json() == {"ok": True, "error": None, "count": 2, "matches": [
        {"start": 2, "end": 5}, {"start": 8, "end": 11}]}
    assert bad.status_code == 200 and bad.json()["ok"] is False and bad.json()["error"]
    assert words.json()["count"] == 1
    assert too_long.status_code == 422


async def test_writes_are_audited_without_test_text(app_client, session) -> None:
    await _admin(app_client, session, "rules-audit")
    created = await app_client.post(
        "/api/v1/rules",
        json={"kind": "regex", "title": "Договор", "weight": 30, "cap": 2,
              "pattern": r"№\s?\d{4}-\d{3}", "test_text": SAMPLE},
    )
    rule_id = created.json()["id"]
    await app_client.patch(f"/api/v1/rules/{rule_id}", json={"weight": 35})

    rows = (await session.scalars(select(AuditLog).where(AuditLog.target_type == "rule"))).all()

    assert [r.action for r in rows] == ["rule.create", "rule.update"]
    assert rows[1].payload["changes"] == {"weight": [30, 35]}
    assert "Договор №1234" not in str([r.payload for r in rows])
```

- [ ] **Step 2: Запустить — упасть** (404 на маршруты).

- [ ] **Step 3: Схемы**

В `server/barysguard/api/schemas.py` добавить (импорты `Field`, `Literal`, `datetime`, `uuid` уже доступны или добавить):

```python
class RuleSummary(BaseModel):
    id: uuid.UUID
    key: str
    kind: str
    title: str
    builtin: bool
    enabled: bool
    version: int
    weight: int
    cap: int
    pattern: str | None = None
    ignore_case: bool = False
    terms_count: int | None = None
    updated_at: datetime


class RuleCreateRequest(BaseModel):
    kind: Literal["dictionary", "regex"]
    title: str
    weight: int
    cap: int
    pattern: str | None = None
    ignore_case: bool = False
    test_text: str | None = None
    terms: list[str] | None = None


class RuleUpdateRequest(BaseModel):
    title: str | None = None
    enabled: bool | None = None
    weight: int | None = None
    cap: int | None = None
    pattern: str | None = None
    ignore_case: bool | None = None
    test_text: str | None = None


class TermsAddRequest(BaseModel):
    terms: list[str]


class TermsAddResponse(BaseModel):
    added: int


class TermItem(BaseModel):
    id: uuid.UUID
    term: str


class TermPage(BaseModel):
    items: list[TermItem]
    total: int


class RuleVersionItem(BaseModel):
    version: int
    params: dict[str, Any]
    created_at: datetime


class RuleTestRequest(BaseModel):
    kind: Literal["dictionary", "regex"]
    pattern: str | None = None
    ignore_case: bool = False
    terms: list[str] | None = None
    text: str


class RuleMatchSpan(BaseModel):
    start: int
    end: int


class RuleTestResponse(BaseModel):
    ok: bool
    error: str | None = None
    count: int
    matches: list[RuleMatchSpan]
```

Числовые ограничения (вес/потолок) проверяет сервис (сообщения на русском, код 422), а не Pydantic: так текст ошибки единообразен.

- [ ] **Step 4: Маршруты**

`server/barysguard/api/rules.py`:

```python
"""Правила и словари инспекции: только администратор."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import require_admin
from barysguard.api.schemas import (
    RuleCreateRequest,
    RuleMatchSpan,
    RuleSummary,
    RuleTestRequest,
    RuleTestResponse,
    RuleUpdateRequest,
    RuleVersionItem,
    TermItem,
    TermPage,
    TermsAddRequest,
    TermsAddResponse,
)
from barysguard.core.config import Settings, get_settings
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.inspection import rule_admin
from barysguard.services.inspection.rule_admin import RuleError, RuleView

router = APIRouter(prefix="/api/v1", tags=["rules"])


def _summary(view: RuleView) -> RuleSummary:
    return RuleSummary(**view.__dict__)


def _fail(error: RuleError) -> HTTPException:
    return HTTPException(error.status_code, error.message)


@router.get("/rules", response_model=list[RuleSummary])
async def list_rules(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[RuleSummary]:
    return [_summary(v) for v in await rule_admin.list_rules(session)]


@router.post("/rules/test", response_model=RuleTestResponse)
async def run_rule_test(
    payload: RuleTestRequest,
    _: User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> RuleTestResponse:
    """Проверка на тексте оператора; текст не сохраняется и в логи не попадает."""
    try:
        outcome = rule_admin.run_rule_test(
            payload.kind,
            pattern=payload.pattern,
            ignore_case=payload.ignore_case,
            terms=payload.terms,
            text=payload.text,
            max_pattern=settings.regex_max_pattern,
            max_match=settings.regex_max_match,
            max_text=settings.rules_test_max_text,
        )
    except RuleError as error:
        raise _fail(error) from None
    return RuleTestResponse(
        ok=outcome.ok,
        error=outcome.error,
        count=outcome.count,
        matches=[RuleMatchSpan(start=s, end=e) for s, e in outcome.matches],
    )


@router.post("/rules", response_model=RuleSummary, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleCreateRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RuleSummary:
    try:
        view = await rule_admin.create_rule(
            session,
            settings,
            kind=payload.kind,
            title=payload.title,
            weight=payload.weight,
            cap=payload.cap,
            pattern=payload.pattern,
            ignore_case=payload.ignore_case,
            test_text=payload.test_text,
            terms=payload.terms,
        )
    except RuleError as error:
        raise _fail(error) from None
    audit: dict[str, Any] = {
        "key": view.key,
        "kind": view.kind,
        "title": view.title,
        "weight": view.weight,
        "cap": view.cap,
    }
    if view.pattern is not None:
        audit["pattern"] = view.pattern
    await record_audit(
        session,
        user_id=user.id,
        action="rule.create",
        target_type="rule",
        target_id=view.id,
        payload=audit,
    )
    return _summary(view)


@router.get("/rules/{rule_id}", response_model=RuleSummary)
async def read_rule(
    rule_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> RuleSummary:
    try:
        return _summary(await rule_admin.get_rule(session, rule_id))
    except RuleError as error:
        raise _fail(error) from None


@router.patch("/rules/{rule_id}", response_model=RuleSummary)
async def update_rule(
    rule_id: uuid.UUID,
    payload: RuleUpdateRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RuleSummary:
    try:
        view, changes = await rule_admin.update_rule(
            session, settings, rule_id, payload.model_dump(exclude_unset=True)
        )
    except RuleError as error:
        raise _fail(error) from None
    if changes:
        await record_audit(
            session,
            user_id=user.id,
            action="rule.update",
            target_type="rule",
            target_id=view.id,
            payload={"key": view.key, "changes": changes},
        )
    return _summary(view)


@router.get("/rules/{rule_id}/versions", response_model=list[RuleVersionItem])
async def rule_versions(
    rule_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[RuleVersionItem]:
    try:
        versions = await rule_admin.list_versions(session, rule_id)
    except RuleError as error:
        raise _fail(error) from None
    return [
        RuleVersionItem(version=v.version, params=v.params, created_at=v.created_at)
        for v in versions
    ]


@router.get("/rules/{rule_id}/terms", response_model=TermPage)
async def rule_terms(
    rule_id: uuid.UUID,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> TermPage:
    try:
        items, total = await rule_admin.list_terms(session, rule_id, q, limit, offset)
    except RuleError as error:
        raise _fail(error) from None
    return TermPage(items=[TermItem(id=t.id, term=t.term) for t in items], total=total)


@router.post("/rules/{rule_id}/terms", response_model=TermsAddResponse)
async def add_rule_terms(
    rule_id: uuid.UUID,
    payload: TermsAddRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> TermsAddResponse:
    try:
        added = await rule_admin.add_terms(session, rule_id, payload.terms)
    except RuleError as error:
        raise _fail(error) from None
    if added:
        await record_audit(
            session,
            user_id=user.id,
            action="rule.terms_add",
            target_type="rule",
            target_id=rule_id,
            payload={"added": added},
        )
    return TermsAddResponse(added=added)


@router.delete("/rules/{rule_id}/terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule_term(
    rule_id: uuid.UUID,
    term_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await rule_admin.delete_term(session, rule_id, term_id)
    except RuleError as error:
        raise _fail(error) from None
    await record_audit(
        session,
        user_id=user.id,
        action="rule.term_delete",
        target_type="rule",
        target_id=rule_id,
        payload={"term_id": str(term_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

В `server/barysguard/main.py` рядом с остальными импортами/регистрациями: `from barysguard.api.rules import router as rules_router` и `app.include_router(rules_router)` (после `incidents_router`).

- [ ] **Step 5: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_rules_api.py -q` → PASS. Если `test_every_route...` падает на `401` для анонима — проверить, что `require_admin` отдаёт 401 без сессии (как у соседних маршрутов), и привести ожидание теста к фактическому поведению проекта (смотреть `tests/test_operator_api.py`).

- [ ] **Step 6: Контракт и типы консоли**

Из `server/`:

```bash
.venv/Scripts/python -c "import yaml; from barysguard.main import create_app; open('../api/gateway-v1.yaml', 'w', encoding='utf-8', newline='\n').write(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True))"
.venv/Scripts/python -m pytest tests/test_openapi_contract.py -q
```

Из `web/`: `npm run types`, затем в `web/src/api/types.ts` добавить:

```ts
export type RuleSummary = Schemas["RuleSummary"];
export type RuleCreateRequest = Schemas["RuleCreateRequest"];
export type RuleUpdateRequest = Schemas["RuleUpdateRequest"];
export type TermItem = Schemas["TermItem"];
export type TermPage = Schemas["TermPage"];
export type RuleVersionItem = Schemas["RuleVersionItem"];
export type RuleTestRequest = Schemas["RuleTestRequest"];
export type RuleTestResponse = Schemas["RuleTestResponse"];
```

и `npm run typecheck` (должен пройти).

- [ ] **Step 7: Полная проверка и commit**

Весь серверный набор, `ruff check .`, `ruff format --check .`, `mypy barysguard`.

```bash
git add server api web
git commit -m "feat(server): rules API for administrators with audit, terms and pattern test

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Консоль — список правил, меню и маршрут только для администратора

**Files:**
- Modify: `web/src/i18n/ru.ts`, `web/src/App.tsx`, `web/src/app/Shell.tsx`, `web/src/app/Shell.test.tsx`
- Create: `web/src/components/Textarea.tsx`, `web/src/features/rules/{queries.ts, RulesPage.tsx, rules.module.css, RulesPage.test.tsx}`

**Interfaces:**
- Consumes: типы из Task 5; `api`, `Button`, `Input`, `Select`, `Table`, `Spinner`, `ErrorState`, `EmptyState`, `Badge`, `useToast`, `describeError`, `useSession`.
- Produces: `useRules()`, `useUpdateRule(id)` (PATCH), `useRule`... (дальше расширяются в Tasks 7–8); страница `RulesPage` с переключателем включения, кнопкой «Создать правило» и «Изменить» (окна — Task 7), вкладкой «Как создавать правила» (содержимое — Task 8, здесь заглушка-контейнер `GuideTab`); `Textarea` как `Input`, но с `<textarea>`.

- [ ] **Step 1: i18n**

В `web/src/i18n/ru.ts`: в `nav` добавить `rules: "Правила"`; добавить верхнеуровневую секцию (рядом с `incidents`):

```ts
  rules: {
    title: "Правила",
    tabs: { list: "Правила", guide: "Как создавать правила", label: "Разделы" },
    caption: "Правила инспекции содержимого",
    columns: {
      title: "Название",
      type: "Тип",
      enabled: "Включено",
      weight: "Вес",
      cap: "Потолок",
      version: "Версия",
    },
    kinds: { detector: "Встроенное", dictionary: "Словарь", regex: "Шаблон" } as Record<string, string>,
    builtin: "встроенное",
    create: "Создать правило",
    edit: "Изменить",
    toggle: (title: string) => `Включить правило «${title}»`,
    toggled: { on: "Правило включено", off: "Правило отключено" },
    empty: "Правил пока нет",
    emptyHint: "Выполните barysguard-admin seed-rules, чтобы завести встроенные правила.",
    adminOnly: "Правила доступны только администратору",
    cannotDisableLast: "Нельзя отключить последнее включённое правило",
  },
```

Остальные строки (окна, термины, инструкция) добавляют Tasks 7–8.

- [ ] **Step 2: Падающие тесты**

`web/src/features/rules/RulesPage.test.tsx`:

```tsx
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
    enabled: false, version: 1, weight: 30, cap: 2, pattern: "№\\d{4}", ignore_case: false,
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
  });
});
```

В `Shell.test.tsx` добавить два теста: для `ADMIN` ссылка «Правила» (`/rules`) есть, для `OPERATOR` — нет.

- [ ] **Step 3: Запустить — упасть** (нет модуля `RulesPage`).

- [ ] **Step 4: Textarea**

`web/src/components/Textarea.tsx`:

```tsx
import { useId, type TextareaHTMLAttributes } from "react";

import styles from "./Field.module.css";

interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  label: string;
  error?: string;
  hint?: string;
}

export function Textarea({ label, error, hint, ...rest }: TextareaProps) {
  const id = useId();
  const noteId = `${id}-note`;
  const hasNote = Boolean(error || hint);

  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <textarea
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

- [ ] **Step 5: queries.ts**

`web/src/features/rules/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { RuleSummary, RuleUpdateRequest } from "../../api/types";

export function useRules() {
  return useQuery({ queryKey: ["rules"], queryFn: () => api.get<RuleSummary[]>("/rules") });
}

export function useUpdateRule(id: string) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (body: RuleUpdateRequest) =>
      api.patch<RuleSummary>(`/rules/${encodeURIComponent(id)}`, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["rules"] });
      void client.invalidateQueries({ queryKey: ["rule-versions", id] });
    },
  });
}
```

- [ ] **Step 6: RulesPage**

`web/src/features/rules/RulesPage.tsx` (окна «Создать»/«Изменить» подключатся в Task 7; здесь кнопки отрисованы, обработчики хранят выбранное правило в состоянии, окна пока не рендерятся):

```tsx
import { useState } from "react";

import type { RuleSummary } from "../../api/types";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { Table } from "../../components/Table";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import page from "../../styles/page.module.css";
import { useSession } from "../../app/session";
import { GuideTab } from "./GuideTab";
import styles from "./rules.module.css";
import { useRules, useUpdateRule } from "./queries";

type Tab = "list" | "guide";

export function RulesPage() {
  const { user } = useSession();
  const [tab, setTab] = useState<Tab>("list");

  if (user && user.role !== "admin") {
    return <p className={styles.note}>{ru.rules.adminOnly}</p>;
  }

  return (
    <>
      <div className={page.titleRow}>
        <h1 className={page.title}>{ru.rules.title}</h1>
      </div>

      <div role="tablist" aria-label={ru.rules.tabs.label} className={styles.tabs}>
        {(["list", "guide"] as const).map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`tab-${name}`}
            aria-selected={tab === name}
            aria-controls={`panel-${name}`}
            className={tab === name ? `${styles.tab} ${styles.tabActive}` : styles.tab}
            onClick={() => setTab(name)}
          >
            {ru.rules.tabs[name]}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "list" ? <RulesList /> : <GuideTab />}
      </div>
    </>
  );
}

function RulesList() {
  const rules = useRules();

  if (rules.isPending) return <Spinner label={ru.common.loading} />;
  if (rules.isError) {
    return <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />;
  }
  if (rules.data.length === 0) {
    return <EmptyState title={ru.rules.empty} hint={ru.rules.emptyHint} />;
  }

  return (
    <Table caption={ru.rules.caption}>
      <thead>
        <tr>
          <th>{ru.rules.columns.enabled}</th>
          <th>{ru.rules.columns.title}</th>
          <th>{ru.rules.columns.type}</th>
          <th>{ru.rules.columns.weight}</th>
          <th>{ru.rules.columns.cap}</th>
          <th>{ru.rules.columns.version}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {rules.data.map((rule) => (
          <RuleRow key={rule.id} rule={rule} />
        ))}
      </tbody>
    </Table>
  );
}

function RuleRow({ rule }: { rule: RuleSummary }) {
  const update = useUpdateRule(rule.id);
  const toast = useToast();
  // Показываем выбранное состояние сразу; при ошибке возвращаем серверное.
  const checked = update.isPending ? Boolean(update.variables?.enabled) : rule.enabled;

  async function toggle(enabled: boolean) {
    try {
      await update.mutateAsync({ enabled });
      toast.notify(enabled ? ru.rules.toggled.on : ru.rules.toggled.off, "ok");
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <tr>
      <td>
        <input
          type="checkbox"
          aria-label={ru.rules.toggle(rule.title)}
          checked={checked}
          disabled={update.isPending}
          onChange={(event) => void toggle(event.target.checked)}
        />
      </td>
      <td>{rule.title}</td>
      <td>{rule.builtin ? ru.rules.kinds.detector : (ru.rules.kinds[rule.kind] ?? rule.kind)}</td>
      <td>{rule.weight}</td>
      <td>{rule.cap}</td>
      <td>{rule.version}</td>
      <td>
        <Button disabled>{ru.rules.edit}</Button>
      </td>
    </tr>
  );
}
```

Замечание: «Встроенное» показывается для всех встроенных правил (включая словарь грифов); тест ожидает «Словарь» для встроенного `markings`? — в тесте `getByText("Словарь")` относится к правилу с `kind: "dictionary"` и `builtin: true`; поэтому тип в таблице выводится по `kind`, а признак встроенного — отдельным значком рядом с названием. Исправить: колонка «Тип» = `ru.rules.kinds[rule.kind]` (для `detector` → «Встроенное», для `dictionary` → «Словарь», для `regex` → «Шаблон»), а у встроенных в ячейке названия добавить `<span className={styles.builtin}>{ru.rules.builtin}</span>`.

Заглушка вкладки `web/src/features/rules/GuideTab.tsx` (содержимое — Task 8):

```tsx
export function GuideTab() {
  return <div />;
}
```

`web/src/features/rules/rules.module.css`:

```css
.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.tab {
  padding: 8px 14px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--muted);
  font: inherit;
  cursor: pointer;
}

.tabActive {
  border-bottom-color: var(--accent);
  color: inherit;
  font-weight: 650;
}

.note {
  color: var(--muted);
}

.builtin {
  margin-left: 8px;
  color: var(--muted);
  font-size: 12px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.muted {
  color: var(--muted);
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}

.hit {
  background: var(--warn-bg, rgba(255, 200, 0, 0.35));
}

.sample {
  white-space: pre-wrap;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
```

- [ ] **Step 7: Маршрут и меню**

`web/src/App.tsx`: импорт `RulesPage` и маршрут `<Route path="rules" element={<RulesPage />} />` после `incidents`. В `web/src/app/Shell.tsx` после ссылки «Инциденты» добавить ссылку на `/rules` только для администратора: `{user?.role === "admin" ? <NavLink to="/rules" className={linkClass}>{ru.nav.rules}</NavLink> : null}`.

- [ ] **Step 8: Запустить**

Run: `npx vitest run src/features/rules src/app` затем весь `npx vitest run`, `npm run typecheck`, `npm run build` → зелёные; вывод без предупреждений `act`.

- [ ] **Step 9: Commit**

```bash
git add web
git commit -m "feat(web): rules list with toggles, admin-only route and menu item

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Консоль — окна создания и правки, термины, проверка шаблона, история версий

**Files:**
- Modify: `web/src/i18n/ru.ts`, `web/src/features/rules/{queries.ts, RulesPage.tsx}`
- Create: `web/src/lib/highlight.ts`, `web/src/lib/highlight.test.ts`, `web/src/features/rules/{RuleTester.tsx, TermsEditor.tsx, VersionsList.tsx, RuleDialog.tsx, CreateRuleDialog.tsx, RuleDialog.test.tsx}`

**Interfaces:**
- Consumes: Task 6; типы `RuleSummary`, `RuleCreateRequest`, `RuleUpdateRequest`, `TermPage`, `RuleVersionItem`, `RuleTestRequest`, `RuleTestResponse`.
- Produces: `highlight(text, spans) -> Segment[]`; `useTestRule()`, `useCreateRule()`, `useTerms(id, q, limit)`, `useAddTerms(id)`, `useDeleteTerm(id)`, `useVersions(id)`; компоненты `RuleTester` (props: `kind`, `pattern`, `ignoreCase`, `terms`, `onResult(result: {ok: boolean; count: number; text: string})`), `TermsEditor({ruleId})`, `VersionsList({ruleId})`, `RuleDialog({rule, onClose})`, `CreateRuleDialog({open, onClose})`.

- [ ] **Step 1: i18n (расширение секции `rules`)**

Добавить в `ru.rules`:

```ts
    dialog: {
      createTitle: "Новое правило",
      editTitle: "Правило",
      kind: "Тип правила",
      kindOptions: { dictionary: "Словарь (список слов и фраз)", regex: "Шаблон (регулярное выражение)" },
      name: "Название",
      weight: "Вес (1–100)",
      cap: "Потолок совпадений (1–50)",
      weightHint: "Сколько баллов даёт одно совпадение",
      capHint: "Сколько совпадений учитывается в оценке",
      pattern: "Шаблон",
      patternHint: "Синтаксис RE2; для кириллицы используйте \\p{L} и \\p{Cyrillic}",
      ignoreCase: "Без учёта регистра",
      terms: "Термины (по одному в строке)",
      save: "Сохранить",
      create: "Создать",
      saved: "Правило сохранено",
      created: "Правило создано",
      versionLabel: (n: number) => `Версия ${n}`,
      testRequired: "Сначала проверьте шаблон на тестовом тексте: нужно хотя бы одно совпадение",
    },
    tester: {
      title: "Проверка",
      text: "Тестовый текст",
      hint: "Текст нигде не сохраняется",
      run: "Проверить",
      count: (n: number) => `Совпадений: ${n}`,
      none: "Совпадений нет",
      stale: "Шаблон изменён — проверьте заново",
    },
    termsEditor: {
      title: "Термины словаря",
      search: "Поиск терминов",
      add: "Добавить термины (по одному в строке)",
      addButton: "Добавить",
      added: (n: number) => (n === 0 ? "Новых терминов нет" : `Добавлено терминов: ${n}`),
      remove: (term: string) => `Удалить термин «${term}»`,
      removed: "Термин удалён",
      empty: "Терминов нет",
      total: (n: number) => `Всего терминов: ${n}`,
      loadMore: "Показать ещё",
    },
    versions: {
      title: "История версий",
      version: "Версия",
      created: "Создана",
      params: "Параметры",
      empty: "Версий нет",
    },
```

- [ ] **Step 2: highlight**

`web/src/lib/highlight.ts`:

```ts
export interface Segment {
  text: string;
  hit: boolean;
}

/** Режет текст на куски по позициям совпадений; перекрытия и выход за границы игнорируются. */
export function highlight(text: string, spans: { start: number; end: number }[]): Segment[] {
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  const result: Segment[] = [];
  let cursor = 0;

  for (const { start, end } of sorted) {
    if (start < cursor || end <= start || start >= text.length) continue;
    const to = Math.min(end, text.length);
    if (start > cursor) result.push({ text: text.slice(cursor, start), hit: false });
    result.push({ text: text.slice(start, to), hit: true });
    cursor = to;
  }
  if (cursor < text.length) result.push({ text: text.slice(cursor), hit: false });
  return result;
}
```

`web/src/lib/highlight.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { highlight } from "./highlight";

describe("highlight", () => {
  it("режет текст по совпадениям", () => {
    expect(highlight("a 123 b 456", [{ start: 2, end: 5 }, { start: 8, end: 11 }])).toEqual([
      { text: "a ", hit: false },
      { text: "123", hit: true },
      { text: " b ", hit: false },
      { text: "456", hit: true },
    ]);
  });

  it("без совпадений возвращает весь текст, пустой текст — пустой список", () => {
    expect(highlight("abc", [])).toEqual([{ text: "abc", hit: false }]);
    expect(highlight("", [])).toEqual([]);
  });

  it("игнорирует перекрытия, пустые и вышедшие за границы совпадения", () => {
    const parts = highlight("abcdef", [
      { start: 1, end: 4 },
      { start: 2, end: 5 },
      { start: 3, end: 3 },
      { start: 5, end: 99 },
      { start: 70, end: 80 },
    ]);
    expect(parts.map((p) => p.text).join("")).toBe("abcdef");
    expect(parts.filter((p) => p.hit).map((p) => p.text)).toEqual(["bcd", "f"]);
  });
});
```

- [ ] **Step 3: queries**

Дополнить `web/src/features/rules/queries.ts`:

```ts
import { keepPreviousData } from "@tanstack/react-query";

import type {
  RuleCreateRequest,
  RuleTestRequest,
  RuleTestResponse,
  RuleVersionItem,
  TermPage,
} from "../../api/types";

export const TERMS_PAGE_SIZE = 100;

export function useCreateRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RuleCreateRequest) => api.post<RuleSummary>("/rules", body),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["rules"] }),
  });
}

export function useTestRule() {
  return useMutation({
    mutationFn: (body: RuleTestRequest) => api.post<RuleTestResponse>("/rules/test", body),
  });
}

export function useTerms(ruleId: string, q: string, limit: number) {
  return useQuery({
    queryKey: ["rule-terms", ruleId, q, limit],
    queryFn: () =>
      api.get<TermPage>(`/rules/${encodeURIComponent(ruleId)}/terms`, { q, limit }),
    placeholderData: keepPreviousData,
  });
}

export function useAddTerms(ruleId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (terms: string[]) =>
      api.post<{ added: number }>(`/rules/${encodeURIComponent(ruleId)}/terms`, { terms }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["rule-terms", ruleId] });
      void client.invalidateQueries({ queryKey: ["rules"] });
      void client.invalidateQueries({ queryKey: ["rule-versions", ruleId] });
    },
  });
}

export function useDeleteTerm(ruleId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (termId: string) =>
      api.delete<void>(`/rules/${encodeURIComponent(ruleId)}/terms/${encodeURIComponent(termId)}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["rule-terms", ruleId] });
      void client.invalidateQueries({ queryKey: ["rules"] });
      void client.invalidateQueries({ queryKey: ["rule-versions", ruleId] });
    },
  });
}

export function useVersions(ruleId: string) {
  return useQuery({
    queryKey: ["rule-versions", ruleId],
    queryFn: () => api.get<RuleVersionItem[]>(`/rules/${encodeURIComponent(ruleId)}/versions`),
  });
}
```

(если в `api/client.ts` нет метода `delete`, использовать существующий способ удаления, как в `features/agents` или `auth`; найти командой `grep -n "delete" src/api/client.ts`; при отсутствии — добавить `delete: <T>(path: string) => request<T>("DELETE", path)` рядом с `put`, с тестом в `client.test.ts` на метод и обработку 204.)

- [ ] **Step 4: RuleTester**

`web/src/features/rules/RuleTester.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/Button";
import { Textarea } from "../../components/Textarea";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { highlight } from "../../lib/highlight";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";
import { useTestRule } from "./queries";

export interface TestResult {
  ok: boolean;
  count: number;
  text: string;
}

interface Props {
  kind: "regex" | "dictionary";
  pattern?: string;
  ignoreCase?: boolean;
  terms?: string[];
  /** Сообщает родителю результат последней проверки; null — результат устарел. */
  onResult: (result: TestResult | null) => void;
}

export function RuleTester({ kind, pattern, ignoreCase, terms, onResult }: Props) {
  const [text, setText] = useState("");
  const test = useTestRule();
  const [shown, setShown] = useState<{
    spans: { start: number; end: number }[];
    count: number;
    error: string | null;
    text: string;
  } | null>(null);
  const signature = JSON.stringify([kind, pattern, ignoreCase, terms]);
  const checked = useRef<string | null>(null);

  // Изменённый шаблон делает прежнюю проверку недействительной.
  useEffect(() => {
    if (checked.current !== null && checked.current !== signature) {
      onResult(null);
      checked.current = null;
      setShown(null);
    }
  }, [signature, onResult]);

  async function run() {
    try {
      const result = await test.mutateAsync({
        kind,
        pattern,
        ignore_case: ignoreCase ?? false,
        terms,
        text,
      });
      checked.current = signature;
      setShown({ spans: result.matches, count: result.count, error: result.error ?? null, text });
      onResult({ ok: result.ok, count: result.count, text });
    } catch (failure) {
      checked.current = null;
      setShown({ spans: [], count: 0, error: describeError(failure), text });
      onResult(null);
    }
  }

  return (
    <div>
      <h3 className={page.sectionTitle}>{ru.rules.tester.title}</h3>
      <Textarea
        label={ru.rules.tester.text}
        hint={ru.rules.tester.hint}
        rows={5}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className={styles.actions}>
        <Button loading={test.isPending} disabled={text === ""} onClick={() => void run()}>
          {ru.rules.tester.run}
        </Button>
      </div>

      {shown ? (
        shown.error ? (
          <p role="alert">{shown.error}</p>
        ) : (
          <>
            <p>{shown.count > 0 ? ru.rules.tester.count(shown.count) : ru.rules.tester.none}</p>
            <div className={styles.sample} aria-label={ru.rules.tester.title}>
              {highlight(shown.text, shown.spans).map((part, index) =>
                part.hit ? (
                  <mark key={index} className={styles.hit}>
                    {part.text}
                  </mark>
                ) : (
                  <span key={index}>{part.text}</span>
                ),
              )}
            </div>
          </>
        )
      ) : null}
    </div>
  );
}
```

- [ ] **Step 5: TermsEditor и VersionsList**

`web/src/features/rules/TermsEditor.tsx`:

```tsx
import { useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { Textarea } from "../../components/Textarea";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";
import { TERMS_PAGE_SIZE, useAddTerms, useDeleteTerm, useTerms } from "./queries";

export function TermsEditor({ ruleId }: { ruleId: string }) {
  const t = ru.rules.termsEditor;
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(TERMS_PAGE_SIZE);
  const [draft, setDraft] = useState("");
  const terms = useTerms(ruleId, q, limit);
  const add = useAddTerms(ruleId);
  const remove = useDeleteTerm(ruleId);
  const toast = useToast();

  async function submit() {
    const lines = draft.split("\n");
    try {
      const result = await add.mutateAsync(lines);
      toast.notify(t.added(result.added), "ok");
      setDraft("");
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  async function drop(termId: string) {
    try {
      await remove.mutateAsync(termId);
      toast.notify(t.removed, "ok");
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <div>
      <h3 className={page.sectionTitle}>{t.title}</h3>
      <Input
        label={t.search}
        value={q}
        onChange={(event) => {
          setQ(event.target.value);
          setLimit(TERMS_PAGE_SIZE);
        }}
      />

      {terms.isPending ? <Spinner label={ru.common.loading} /> : null}
      {terms.isError ? (
        <ErrorState error={terms.error} onRetry={() => void terms.refetch()} />
      ) : null}
      {terms.data ? (
        terms.data.items.length === 0 ? (
          <p className={styles.muted}>{t.empty}</p>
        ) : (
          <>
            <p className={styles.muted}>{t.total(terms.data.total)}</p>
            <ul>
              {terms.data.items.map((item) => (
                <li key={item.id}>
                  {item.term}{" "}
                  <Button
                    aria-label={t.remove(item.term)}
                    disabled={remove.isPending}
                    onClick={() => void drop(item.id)}
                  >
                    ×
                  </Button>
                </li>
              ))}
            </ul>
            {terms.data.total > terms.data.items.length ? (
              <Button onClick={() => setLimit(limit + TERMS_PAGE_SIZE)}>{t.loadMore}</Button>
            ) : null}
          </>
        )
      ) : null}

      <Textarea
        label={t.add}
        rows={4}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <div className={styles.actions}>
        <Button
          variant="primary"
          loading={add.isPending}
          disabled={draft.trim() === ""}
          onClick={() => void submit()}
        >
          {t.addButton}
        </Button>
      </div>
    </div>
  );
}
```

`web/src/features/rules/VersionsList.tsx`:

```tsx
import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import { formatDateTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";
import { useVersions } from "./queries";

export function VersionsList({ ruleId }: { ruleId: string }) {
  const versions = useVersions(ruleId);
  const t = ru.rules.versions;

  return (
    <div>
      <h3 className={page.sectionTitle}>{t.title}</h3>
      {versions.isPending ? <Spinner label={ru.common.loading} /> : null}
      {versions.isError ? (
        <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
      ) : null}
      {versions.data ? (
        versions.data.length === 0 ? (
          <p className={styles.muted}>{t.empty}</p>
        ) : (
          <Table caption={t.title}>
            <thead>
              <tr>
                <th>{t.version}</th>
                <th>{t.created}</th>
                <th>{t.params}</th>
              </tr>
            </thead>
            <tbody>
              {versions.data.map((item) => (
                <tr key={item.version}>
                  <td>{item.version}</td>
                  <td>{formatDateTime(item.created_at)}</td>
                  <td className={styles.mono}>{JSON.stringify(item.params)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )
      ) : null}
    </div>
  );
}
```

- [ ] **Step 6: RuleDialog (правка) и CreateRuleDialog**

`web/src/features/rules/RuleDialog.tsx`:

```tsx
import { useState } from "react";

import type { RuleSummary, RuleUpdateRequest } from "../../api/types";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { RuleTester, type TestResult } from "./RuleTester";
import { TermsEditor } from "./TermsEditor";
import { VersionsList } from "./VersionsList";
import { useUpdateRule } from "./queries";

export function RuleDialog({ rule, onClose }: { rule: RuleSummary | null; onClose: () => void }) {
  return (
    <Modal open={rule !== null} title={ru.rules.dialog.editTitle} onClose={onClose}>
      {rule ? <Form rule={rule} onClose={onClose} /> : null}
    </Modal>
  );
}

function Form({ rule, onClose }: { rule: RuleSummary; onClose: () => void }) {
  const t = ru.rules.dialog;
  const update = useUpdateRule(rule.id);
  const toast = useToast();
  const [title, setTitle] = useState(rule.title);
  const [weight, setWeight] = useState(String(rule.weight));
  const [cap, setCap] = useState(String(rule.cap));
  const [pattern, setPattern] = useState(rule.pattern ?? "");
  const [ignoreCase, setIgnoreCase] = useState(rule.ignore_case);
  const [tested, setTested] = useState<TestResult | null>(null);

  const isRegex = rule.kind === "regex" && !rule.builtin;
  const patternChanged = isRegex && (pattern !== (rule.pattern ?? "") || ignoreCase !== rule.ignore_case);
  const needsTest = patternChanged && !(tested && tested.ok && tested.count > 0);

  async function save() {
    const body: RuleUpdateRequest = {};
    if (title !== rule.title) body.title = title;
    if (Number(weight) !== rule.weight) body.weight = Number(weight);
    if (Number(cap) !== rule.cap) body.cap = Number(cap);
    if (patternChanged) {
      body.pattern = pattern;
      body.ignore_case = ignoreCase;
      body.test_text = tested?.text;
    }
    try {
      await update.mutateAsync(body);
      toast.notify(t.saved, "ok");
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <>
      <p>{t.versionLabel(rule.version)}</p>
      <Input label={t.name} value={title} onChange={(event) => setTitle(event.target.value)} />
      <Input
        label={t.weight}
        hint={t.weightHint}
        type="number"
        min={1}
        max={100}
        value={weight}
        onChange={(event) => setWeight(event.target.value)}
      />
      <Input
        label={t.cap}
        hint={t.capHint}
        type="number"
        min={1}
        max={50}
        value={cap}
        onChange={(event) => setCap(event.target.value)}
      />

      {isRegex ? (
        <>
          <Input
            label={t.pattern}
            hint={t.patternHint}
            value={pattern}
            onChange={(event) => setPattern(event.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={ignoreCase}
              onChange={(event) => setIgnoreCase(event.target.checked)}
            />{" "}
            {t.ignoreCase}
          </label>
          <RuleTester kind="regex" pattern={pattern} ignoreCase={ignoreCase} onResult={setTested} />
          {needsTest ? <p role="note">{t.testRequired}</p> : null}
        </>
      ) : null}

      {rule.kind === "dictionary" ? <TermsEditor ruleId={rule.id} /> : null}
      <VersionsList ruleId={rule.id} />

      <div>
        <Button variant="primary" loading={update.isPending} disabled={needsTest} onClick={() => void save()}>
          {t.save}
        </Button>
        <Button onClick={onClose}>{ru.common.cancel}</Button>
      </div>
    </>
  );
}
```

`web/src/features/rules/CreateRuleDialog.tsx`:

```tsx
import { useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { Select } from "../../components/Select";
import { Textarea } from "../../components/Textarea";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { RuleTester, type TestResult } from "./RuleTester";
import { useCreateRule } from "./queries";

type Kind = "dictionary" | "regex";

export function CreateRuleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} title={ru.rules.dialog.createTitle} onClose={onClose}>
      {open ? <Form onClose={onClose} /> : null}
    </Modal>
  );
}

function Form({ onClose }: { onClose: () => void }) {
  const t = ru.rules.dialog;
  const create = useCreateRule();
  const toast = useToast();
  const [kind, setKind] = useState<Kind>("dictionary");
  const [title, setTitle] = useState("");
  const [weight, setWeight] = useState("20");
  const [cap, setCap] = useState("3");
  const [terms, setTerms] = useState("");
  const [pattern, setPattern] = useState("");
  const [ignoreCase, setIgnoreCase] = useState(false);
  const [tested, setTested] = useState<TestResult | null>(null);

  const regexReady = kind !== "regex" || Boolean(tested && tested.ok && tested.count > 0);

  async function submit() {
    try {
      await create.mutateAsync({
        kind,
        title,
        weight: Number(weight),
        cap: Number(cap),
        ...(kind === "regex"
          ? { pattern, ignore_case: ignoreCase, test_text: tested?.text }
          : { terms: terms.split("\n") }),
      });
      toast.notify(t.created, "ok");
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <>
      <Select
        label={t.kind}
        value={kind}
        options={[
          { value: "dictionary", label: t.kindOptions.dictionary },
          { value: "regex", label: t.kindOptions.regex },
        ]}
        onChange={(event) => {
          setKind(event.target.value as Kind);
          setTested(null);
        }}
      />
      <Input label={t.name} value={title} onChange={(event) => setTitle(event.target.value)} />
      <Input
        label={t.weight}
        hint={t.weightHint}
        type="number"
        min={1}
        max={100}
        value={weight}
        onChange={(event) => setWeight(event.target.value)}
      />
      <Input
        label={t.cap}
        hint={t.capHint}
        type="number"
        min={1}
        max={50}
        value={cap}
        onChange={(event) => setCap(event.target.value)}
      />

      {kind === "dictionary" ? (
        <Textarea label={t.terms} rows={6} value={terms} onChange={(event) => setTerms(event.target.value)} />
      ) : (
        <>
          <Input
            label={t.pattern}
            hint={t.patternHint}
            value={pattern}
            onChange={(event) => setPattern(event.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={ignoreCase}
              onChange={(event) => setIgnoreCase(event.target.checked)}
            />{" "}
            {t.ignoreCase}
          </label>
          <RuleTester kind="regex" pattern={pattern} ignoreCase={ignoreCase} onResult={setTested} />
          {regexReady ? null : <p role="note">{t.testRequired}</p>}
        </>
      )}

      <div>
        <Button
          variant="primary"
          loading={create.isPending}
          disabled={title.trim() === "" || !regexReady}
          onClick={() => void submit()}
        >
          {t.create}
        </Button>
        <Button onClick={onClose}>{ru.common.cancel}</Button>
      </div>
    </>
  );
}
```

Подключить в `RulesPage.tsx`: состояние `editing: RuleSummary | null` и `creating: boolean`; кнопка «Создать правило» в `titleRow` (`<Button variant="primary" onClick={() => setCreating(true)}>`); кнопка «Изменить» в `RuleRow` получает `onEdit` и открывает `RuleDialog`; рендерить `<RuleDialog rule={editing} onClose={...} />` и `<CreateRuleDialog open={creating} onClose={...} />`. Активная «Изменить» вместо `disabled`.

- [ ] **Step 7: Тесты окон**

`web/src/features/rules/RuleDialog.test.tsx` (harness как в `RulesPage.test.tsx`; рендерить `RulesPage`, открывать окно кнопкой). Обязательные тесты:

1. Правка веса: открыть окно у «ИИН/БИН», изменить вес на 40, «Сохранить» → `PATCH /rules/r1` с телом `{weight: 40}` (только изменённые поля), toast «Правило сохранено», окно закрыто.
2. Regex: поменять шаблон → «Сохранить» заблокирована и виден текст «Сначала проверьте шаблон…»; ввести тестовый текст, «Проверить» (мок `POST /rules/test` → `{ok:true, error:null, count:1, matches:[{start:2,end:6}]}`) → подсветка (`mark`), «Сохранить» активна; сохранение отправляет `{pattern, ignore_case, test_text}`; повторная смена шаблона после проверки снова блокирует «Сохранить».
3. Ошибка шаблона от `/rules/test` (`ok:false, error:"Шаблон не принят: …"`) показывается как `alert`, «Сохранить» остаётся недоступной.
4. Словарь: окно показывает термины (мок `GET /rules/r2/terms`), добавление пачкой (`POST /rules/r2/terms` с `{terms:["a","b"]}` после ввода «a\nb»), удаление (`DELETE /rules/r2/terms/t1`), toast «Термин удалён».
5. История версий выводится (мок `GET /rules/r1/versions`).
6. Создание словаря: кнопка «Создать» неактивна без названия; отправляет `{kind:"dictionary", title, weight, cap, terms}`; создание шаблона: без успешной проверки «Создать» неактивна, после проверки отправляет `{kind:"regex", pattern, ignore_case, test_text, ...}`; 422 от сервера — toast с текстом и окно остаётся.

Каждый тест писать до реализации и видеть падение.

- [ ] **Step 8: Запустить**

`npx vitest run`, `npm run typecheck`, `npm run build` → зелёные, без предупреждений `act`.

- [ ] **Step 9: Commit** (2 коммита: `feat(web): highlight, tester, terms editor and versions list`; `feat(web): rule edit and create dialogs`), каждый с trailer.

---

### Task 8: Инструкция, названия своих правил в инцидентах, документация

**Files:**
- Modify: `web/src/i18n/ru.ts`, `web/src/features/rules/GuideTab.tsx`, `web/src/features/incidents/IncidentDetailPanel.tsx`, `web/src/features/rules/RulesPage.test.tsx`, `web/src/features/incidents/IncidentsPage.test.tsx`, `docs/DLP_WORKER.md`
- Create: `docs/RULES_GUIDE.md`, `web/src/features/rules/GuideTab.test.tsx`

**Interfaces:**
- Produces: вкладка «Как создавать правила» с разделами из `ru.rules.guide.sections`; `docs/RULES_GUIDE.md` с тем же текстом.

- [ ] **Step 1: Текст инструкции (источник правды)**

В `ru.rules.guide` добавить структуру (смысл и примеры — как ниже; формулировки можно слегка править, но состав разделов, пороги и примеры должны остаться):

```ts
    guide: {
      sections: [
        {
          title: "Что такое правило",
          paragraphs: [
            "Правило ищет в содержимом файла, скопированного на флешку, один вид чувствительных данных. Каждое совпадение даёт баллы; сумма баллов определяет вердикт и инцидент.",
            "Встроенные правила (ИИН/БИН, банковские карты, грифы) можно отключать и настраивать, но нельзя удалять. Свои правила бывают двух типов: словарь и шаблон.",
          ],
        },
        {
          title: "Вес, потолок и оценка",
          paragraphs: [
            "Вес — сколько баллов даёт одно совпадение. Потолок — сколько совпадений одного правила учитывается: вклад правила = вес × min(число совпадений, потолок). Общая оценка файла — сумма вкладов всех правил, но не больше 100.",
            "Вердикт по оценке: 0–19 — чисто; 20–49 — средняя; 50–79 — высокая; 80 и больше — критическая. Инцидент создаётся, когда оценка не меньше 20. Границы заданы системой и в консоли не меняются.",
          ],
          items: [
            "Правило-признак (редкое, но серьёзное): вес 40–60, потолок 1–2.",
            "Шумное правило (часто встречается случайно): вес 5–10 или отключите его.",
          ],
        },
        {
          title: "Правило-словарь",
          paragraphs: [
            "Список слов и фраз. Регистр и буквы е/ё не важны, слова ищутся целиком, между словами фразы допускаются несколько пробелов или перенос строки.",
          ],
          items: [
            "Добавляйте термины по одному в строке.",
            "Избегайте коротких аббревиатур, которые встречаются в обычных словах и документах («ДСП» — ещё и древесные плиты).",
            "Изменение терминов создаёт новую версию правила.",
          ],
        },
        {
          title: "Правило-шаблон (регулярное выражение)",
          paragraphs: [
            "Шаблон описывает форму значения: номер договора, внутренний код проекта. Используется движок RE2: он работает за линейное время, поэтому шаблон не может «повесить» сервер, но часть привычных возможностей недоступна.",
            "Важно: \\w, \\d и \\b в RE2 понимают только латиницу и цифры ASCII. Для русских букв используйте \\p{L} (любая буква) и \\p{Cyrillic}; для цифр — \\d или \\p{Nd}.",
          ],
          examples: [
            { pattern: "№\\s?\\d{4}-\\d{3}", note: "номер договора: «№1234-567», «№ 1234-567»" },
            { pattern: "ALFA-\\d{3,5}", note: "внутренний код проекта (включите «Без учёта регистра»)" },
            { pattern: "\\p{Lu}{2}\\d{6}", note: "серия и номер документа: две заглавные буквы и шесть цифр" },
          ],
          items: [
            "Нельзя: lookahead и lookbehind ((?=…), (?<=…)), обратные ссылки (\\1).",
            "Шаблон не длиннее 500 символов; совпадение не длиннее 200 символов (более длинные не учитываются); шаблон не должен совпадать с пустой строкой.",
            "Образцы найденного в системе маскируются: видны только последние два символа.",
          ],
        },
        {
          title: "Как проверить шаблон",
          paragraphs: [
            "Перед сохранением вставьте в поле «Тестовый текст» образец и нажмите «Проверить»: найденное подсвечивается. Сохранить шаблон можно только после проверки, на которой найдено хотя бы одно совпадение. Тестовый текст нигде не сохраняется.",
            "Не вставляйте в тестовый текст реальные персональные данные: достаточно похожего образца.",
          ],
        },
        {
          title: "Как применяются изменения",
          paragraphs: [
            "Любое изменение веса, потолка, шаблона или терминов создаёт новую версию правила; предыдущие остаются в истории. Новые версии действуют на файлы, проверяемые после изменения; уже вынесенные вердикты и инциденты не пересчитываются.",
            "Если правило даёт слишком много срабатываний, уменьшите вес или отключите его переключателем в списке. Последнее включённое правило отключить нельзя.",
          ],
        },
      ],
    },
```

Тип секции: `{ title: string; paragraphs: string[]; items?: string[]; examples?: { pattern: string; note: string }[] }`.

- [ ] **Step 2: Падающий тест вкладки**

`web/src/features/rules/GuideTab.test.tsx`: рендер `GuideTab` — все заголовки разделов присутствуют, примеры шаблонов выведены моноширинным текстом, текст про `\p{L}` и про пороги 20–49/50–79/80 присутствует.

- [ ] **Step 3: GuideTab**

```tsx
import { ru } from "../../i18n/ru";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";

export function GuideTab() {
  return (
    <div>
      {ru.rules.guide.sections.map((section) => (
        <section key={section.title} className={page.section}>
          <h2 className={page.sectionTitle}>{section.title}</h2>
          {section.paragraphs.map((text) => (
            <p key={text}>{text}</p>
          ))}
          {section.examples ? (
            <ul>
              {section.examples.map((example) => (
                <li key={example.pattern}>
                  <code className={styles.mono}>{example.pattern}</code> — {example.note}
                </li>
              ))}
            </ul>
          ) : null}
          {section.items ? (
            <ul>
              {section.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}
    </div>
  );
}
```

В тест `RulesPage.test.tsx` «вкладка открывается» добавить проверку заголовка раздела «Вес, потолок и оценка».

- [ ] **Step 4: Названия своих правил в инциденте**

В `IncidentDetailPanel.tsx` в таблице совпадений: `{ru.incidents.rules[match.rule_key] ?? (match.rule_title || match.rule_key)}`. Тест в `IncidentsPage.test.tsx`: совпадение `{rule_key: "custom_aa11bb22", rule_title: "Номер договора", ...}` показывается названием, а при пустом `rule_title` — ключом.

- [ ] **Step 5: Документация**

`docs/RULES_GUIDE.md` — тот же текст, что в `ru.rules.guide` (шесть разделов, примеры шаблонов в таблице или списке кода), плюс разделы «Где найти» («Консоль → Правила, только администратор») и «Через API» (перечень маршрутов `/api/v1/rules*`). В `docs/DLP_WORKER.md` заменить абзац про правку словаря на ссылку на «Правила» в консоли и `RULES_GUIDE.md`; указать, что `seed-rules` не перезаписывает правки оператора; добавить `google-re2` в список зависимостей, если он есть.

- [ ] **Step 6: Запустить и commit**

`npx vitest run`, `npm run typecheck`, `npm run build`; commit `feat(web): rules guide tab, custom rule titles in incidents and rules docs`.

---

### Task 9: Сквозной тест и проверка на стенде

**Files:**
- Create: `server/tests/test_rules_e2e.py`
- Modify: `docs/RULES_GUIDE.md` (раздел «Проверка на стенде»), `docs/superpowers/specs/2026-10-06-rules-editor-design.md` (статус)

- [ ] **Step 1: Сквозной тест**

`server/tests/test_rules_e2e.py` (по образцу `tests/test_inspection_e2e.py`: фикстура `artifact_env`, `_event`, `_upload`, `run_once`):

```python
"""Правки правил из API действуют на следующий проверяемый файл."""

# фикстуры и помощники скопировать из test_inspection_e2e.py: artifact_env, _event, _upload

CONTRACT = "Договор №1234-567 подписан директором. Строго конфиденциально."
CARD_ONLY = "Оплата картой 4111 1111 1111 1111 и больше ничего"


async def test_custom_regex_rule_flags_a_file_and_a_disabled_builtin_does_not(
    app_client, session, migrated_database_url, artifact_env
) -> None:
    await login_as(app_client, session, username="rules-e2e", role=UserRole.ADMIN)
    await seed_rules(session)
    await session.commit()
    agent = await enroll_agent(app_client, session, "rules-e2e-agent")

    created = await app_client.post(
        "/api/v1/rules",
        json={"kind": "regex", "title": "Номер договора", "weight": 30, "cap": 2,
              "pattern": r"№\s?\d{4}-\d{3}", "test_text": "Договор №1234-567"},
    )
    assert created.status_code == 201

    await _event(app_client, agent, CONTRACT.encode(), "E:\\contract.txt")
    await _upload(app_client, agent, CONTRACT.encode())
    await _run_worker(migrated_database_url)

    incidents = (await app_client.get("/api/v1/incidents")).json()["items"]
    assert len(incidents) == 1
    assert "Номер договора ×1" in incidents[0]["title"]
    detail = (await app_client.get(f"/api/v1/incidents/{incidents[0]['id']}")).json()
    assert any(m["rule_title"] == "Номер договора" for m in detail["matches"])
    assert "1234-567" not in str(detail)  # образец замаскирован

    cards = next(r for r in (await app_client.get("/api/v1/rules")).json() if r["key"] == "card")
    off = await app_client.patch(f"/api/v1/rules/{cards['id']}", json={"enabled": False})
    assert off.status_code == 200

    await _event(app_client, agent, CARD_ONLY.encode(), "E:\\card.txt")
    await _upload(app_client, agent, CARD_ONLY.encode())
    await _run_worker(migrated_database_url)

    events = (await app_client.get("/api/v1/events", params={"channel": "file"})).json()["items"]
    verdicts = {e["subject"]["dst_path"]: e["verdict"] for e in events}
    assert verdicts["E:\\card.txt"]["status"] == "clean"   # встроенное правило отключено
```

(`_run_worker` — небольшой помощник: создаёт движок, вызывает `run_once(session_factory(engine), build_store(settings), settings)` и закрывает движок, как в `test_inspection_e2e.py`.) Проверить также, что `ruleset_hash` после правки привёл к новому скану (число `artifact_scans` равно числу различных файлов × версий набора).

- [ ] **Step 2: Запустить**

Весь серверный набор, `ruff check .`, `ruff format --check .`, `mypy barysguard`; в `web/`: `npx vitest run`, `npm run typecheck`, `npm run build`; в `agent/`: `go test ./...` (не затронут).

- [ ] **Step 3: Стенд**

Если Docker запущен: `.\stand.cmd up`, `.\smoke.cmd` (все шаги ok), `.\stand.cmd seed`. Вручную по `docs/RULES_GUIDE.md` («Проверка на стенде»): войти `admin`, открыть «Правила», отключить «Банковские карты», создать словарь «Проект Альфа» с терминами, создать шаблон `ALFA-\d{3,5}` (проверка → «Создать»), скопировать на флешку тестовые файлы, убедиться, что в «Инцидентах» название нового правила и что отключённое правило не срабатывает. Если Docker недоступен — сказать об этом в отчёте.

- [ ] **Step 4: Статус спеки и commit**

В спеке строку «Статус» заменить на «реализовано (подпроект C2)».

```bash
git add server docs
git commit -m "test: end-to-end check of rule edits and docs for the rules editor

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** раздел 3 (данные, `builtin`, `updated_at`, `seed_rules`) → Task 1; раздел 4 (сканер, RegexDetector, загрузка правил, `rule_title`) → Tasks 2–3; раздел 5 (API) и 7 (безопасность на сервере) → Tasks 4–5; раздел 6 (консоль: список, окно, термины, проверка, инструкция, инциденты) → Tasks 6–8; раздел 8 (настройки) → Task 1; раздел 9 (тестирование) → тесты в каждой задаче и Task 9; раздел 10 (порядок) соблюдён.

**Отклонения от спеки, принятые в плане:** (1) проверка пустого совпадения делается `compiled.search("")`; (2) `POST /rules/test` отвечает 200 с `ok=false` на неверный шаблон (для встроенной подсказки в окне), 422 — только на превышение лимита текста и неверный тип; (3) тип «Встроенное» показывается для `kind=detector`, а признак встроенного правила — значком рядом с названием (в спеке «встроенное/словарь/шаблон» как типы); (4) совпадения regex длиннее `max_match` не засчитываются, и при таких совпадениях на стыке порций результат может отличаться от сканирования целиком — это ограничение документируется в инструкции (лимит 200 символов), тест инвариантности использует совпадения короче лимита.

**Placeholder scan:** шаги содержат полный код; пометки «найти командой/как в соседнем файле» остаются только для: обработчиков `DELETE` в `api/client.ts` (Task 7, Step 3), порядка 401/403 для анонима (Task 5, Step 5), мест импортов.

**Type consistency:** `RuleView` (Task 4) → `RuleSummary` (Task 5, поля совпадают по именам); `update_rule` возвращает `(RuleView, changes)` и используется в API; `RuleWeight.title` (Task 3) → `rule_title` в `matches` → `IncidentMatch.rule_title` → консоль (Task 8); `RuleTester.onResult(TestResult | null)` одинаков в `RuleDialog` и `CreateRuleDialog`; `useUpdateRule(id)` принимает `RuleUpdateRequest` и в списке (`{enabled}`) и в окне.
