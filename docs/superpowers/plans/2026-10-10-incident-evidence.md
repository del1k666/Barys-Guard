# Доказательства инцидента и тёмно-красная тема: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В карточке инцидента показать, по каким правилам он сработал и какие маскированные фрагменты текста найдены, и перевести консоль на чёрно-красную тему с новыми шрифтами и анимациями.

**Architecture:** Детекторы отдают позицию совпадения; сканер строит фрагменты (контекст ±80 символов, маскирование чувствительных значений в контексте) и копит их в `Finding`; `evaluate()` переносит фрагменты, вес и потолок в `matches` вердикта; API инцидента отдаёт их; веб показывает выезжающую панель с блоками «Почему сработало» и «Что нашли в документе». Тема меняется переопределением CSS-токенов, шрифты подключаются локально.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, pytest; React 19, Vite, CSS-модули, vitest; `@fontsource/ibm-plex-sans`, `@fontsource/jetbrains-mono`.

**Spec:** `docs/superpowers/specs/2026-10-10-incident-evidence-design.md`

## Global Constraints

- Полное значение ИИН/БИН, карты и совпадения правила-шаблона никогда не попадает в БД и в ответ API: в `hit` и в контексте только маски.
- Фрагментов не больше `MAX_FRAGMENTS = 5` на правило; контекст `FRAGMENT_CONTEXT = 80` символов с каждой стороны; запас на маскирование `MASK_REACH = 40`.
- Пороги и формула оценки (`scoring.py`) не меняются: `вес × min(совпадений, потолок)`, итог ≤ 100, пороги 20/50/80.
- Старые вердикты без фрагментов остаются рабочими: поле приходит пустым, интерфейс показывает пояснение.
- Тема только тёмная (`color-scheme: dark`), светлой нет. Шрифты локально, без CDN. Лицензия шрифтов OFL-1.1 должна проходить `npm run licenses`.
- Все анимации отключаются при `prefers-reduced-motion: reduce`.
- Строки интерфейса и документация на русском; строки интерфейса только через `web/src/i18n/ru.ts`.
- Команды сервера выполняются из `server/`: `BG_TEST_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard" .venv/Scripts/python -m pytest -q`, плюс `.venv/Scripts/ruff check .`, `.venv/Scripts/ruff format --check .`, `.venv/Scripts/mypy barysguard`. Веб из `web/`: `npx vitest run`, `npm run typecheck`, `npm run build`.

## Review Focus

- Контекст рядом с находкой содержит другой ИИН или номер карты, в том числе записанную с пробелами: в фрагменте они замаскированы (Task 3).
- Значение лежит на самом краю окна контекста: обрезанный «хвост» числа не показывается (Task 2).
- Находка у границы порции текста при разных размерах порций: фрагмент тот же, что и при одной порции (Task 3).
- Совпадение у самого начала или конца документа: фрагмент строится без ошибок, без «…» на настоящем краю (Task 2).
- Вердикт, созданный до обновления (в `matches` нет `fragments`, `weight`, `cap`): API отдаёт пустой список и нули, панель не падает (Task 4, Task 7).
- Слово-термин словаря в контексте не маскируется, а в `hit` показывается с исходным регистром (Task 2).

---

## Файловая структура

Сервер:
- `server/barysguard/services/inspection/detectors.py` — протокол детектора (`find` отдаёт `(маска, начало, конец)`, флаг `masks_hits`), сканер с историей и фрагментами.
- `server/barysguard/services/inspection/regex_detector.py` — тот же протокол.
- `server/barysguard/services/inspection/fragments.py` — новый модуль: маскирование окна, обрезка краёв, `build_fragment`.
- `server/barysguard/services/inspection/engine.py`, `scoring.py` — перенос фрагментов, веса и потолка.
- `server/barysguard/api/schemas.py`, `api/incidents.py`, `api/gateway-v1.yaml` — отдача новых полей.

Веб:
- `web/src/styles/global.css` — токены, шрифты, ключевые кадры.
- `web/src/main.tsx` — импорт шрифтов.
- `web/src/components/Modal.tsx` + `.module.css` — вариант `drawer`.
- `web/src/components/Badge.*`, `StatusBadge.tsx`, `Table.module.css`, `app/Shell.*`, `Toast.module.css`, `Button.module.css` — тема и анимации.
- `web/src/features/incidents/` — `ScoreRing.tsx`, `WhyTriggered.tsx`, `Evidence.tsx`, `ruleName.ts`, обновлённые `IncidentDetailPanel.tsx`, `incidents.module.css`, тесты.
- `web/src/i18n/ru.ts` — новые строки.

---

### Task 1: Детекторы отдают начало совпадения и флаг маскирования

**Files:**
- Modify: `server/barysguard/services/inspection/detectors.py`
- Modify: `server/barysguard/services/inspection/regex_detector.py`
- Test: `server/tests/test_inspection_detectors.py`

**Interfaces:**
- Produces: `Detector.find(data, start, end) -> Iterator[tuple[str, int, int]]` — `(маска или "", начало совпадения, конец совпадения)`; `Detector.masks_hits: bool` — `True` у `IinBinDetector`, `CardDetector`, `RegexDetector`, `False` у `DictionaryDetector`.
- Consumes: ничего.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `server/tests/test_inspection_detectors.py` (импорт `RegexDetector` добавить в начало файла: `from barysguard.services.inspection.regex_detector import RegexDetector`):

```python
def test_detectors_report_the_span_of_each_match() -> None:
    iin_text = f"номер {IIN_FIRST_PASS} конец"
    [(sample, start, end)] = list(IinBinDetector().find(iin_text, 0, len(iin_text)))
    assert iin_text[start:end] == IIN_FIRST_PASS
    assert sample == "*" * 10 + IIN_FIRST_PASS[-2:]

    card_text = f"карта {VISA[:4]} {VISA[4:8]} {VISA[8:12]} {VISA[12:]} ок"
    [(_, start, end)] = list(CardDetector().find(card_text, 0, len(card_text)))
    assert card_text[start:end].replace(" ", "") == VISA

    word_text = "Это КОНФИДЕНЦИАЛЬНО!"
    [(sample, start, end)] = list(
        DictionaryDetector("m", ["конфиденциально"]).find(word_text, 0, len(word_text))
    )
    assert word_text[start:end] == "КОНФИДЕНЦИАЛЬНО"
    assert sample == "конфиденциально"

    regex_text = "Договор №1234 и №5678"
    spans = [
        regex_text[s:e] for _, s, e in RegexDetector("c", r"№\d+").find(regex_text, 0, len(regex_text))
    ]
    assert spans == ["№1234", "№5678"]


def test_only_value_detectors_mask_their_hits() -> None:
    assert IinBinDetector().masks_hits is True
    assert CardDetector().masks_hits is True
    assert RegexDetector("c", r"\d+").masks_hits is True
    assert DictionaryDetector("m", ["гриф"]).masks_hits is False
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python -m pytest -q tests/test_inspection_detectors.py -k "span or mask"`
Expected: FAIL (`ValueError: not enough values to unpack` / `AttributeError: masks_hits`).

- [ ] **Step 3: Реализовать**

В `detectors.py`:

1. Протокол `Detector`: добавить атрибут и поменять сигнатуру.

```python
class Detector(Protocol):
    key: str
    # Сколько символов от начала совпадения детектор может просмотреть (без учёта соседних
    # символов для проверки границ): столько текста сканер держит в запасе на стыке порций.
    max_length: int
    # Значение совпадения чувствительно (ИИН, карта, шаблон): в контексте фрагментов такие
    # совпадения заменяются маской. Словарные термины маски не требуют.
    masks_hits: bool

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int, int]]:
        """Настоящие совпадения, начавшиеся в [start, end): (маска образца, начало, конец).

        Совпадения не пересекаются и идут по возрастанию начала; результат для позиции
        зависит только от текста вокруг неё, а не от того, откуда начат поиск.
        Пустой образец ("") означает «участок поглощён, но не засчитан» (например, слишком
        длинное совпадение): сканер продолжит поиск за его концом и не посчитает остаток.
        """
        ...
```

2. `IinBinDetector`: `masks_hits = True`; в `find` — `yield "*" * 10 + number[-2:], match.start(), match.end()`; тип возврата `Iterator[tuple[str, int, int]]`.

3. `CardDetector`: `masks_hits = True`; `find` возвращает `Iterator[tuple[str, int, int]]`; `_check` возвращает `tuple[str, int, int] | None`:

```python
    def _check(self, match: re.Match[str]) -> tuple[str, int, int] | None:
        digits = re.sub(r"\D", "", match.group())
        if luhn_ok(digits) and _card_network_ok(digits):
            return "*" * (len(digits) - 4) + digits[-4:], match.start(), match.end()
        return None
```

и в конце цикла `find`: `yield hit` и `position = hit[2]`.

4. `DictionaryDetector`: `masks_hits = False`; `yield normalize(" ".join(match.group().split())), match.start(), match.end()`.

5. В `ContentScanner._scan` цикл: `for sample, _begin, match_end in detector.find(...)` (начало пока не используется).

В `regex_detector.py`: `RegexDetector.masks_hits = True` (атрибут класса), `find` возвращает `Iterator[tuple[str, int, int]]`, оба `yield` получают `match.start()`: `yield "", match.start(), match.end()` и `yield mask_tail(match.group()), match.start(), match.end()`.

- [ ] **Step 4: Прогнать тесты и проверки**

Run: `.venv/Scripts/python -m pytest -q tests/test_inspection_detectors.py tests/test_regex_detector.py tests/test_inspection_scoring.py`
Expected: PASS (все, включая прежние).
Run: `.venv/Scripts/ruff check . ; .venv/Scripts/ruff format --check . ; .venv/Scripts/mypy barysguard`
Expected: без ошибок.

- [ ] **Step 5: Commit**

```bash
git add server/barysguard/services/inspection/detectors.py server/barysguard/services/inspection/regex_detector.py server/tests/test_inspection_detectors.py
git commit -m "refactor(server): detectors report match start and whether they mask hits"
```

---

### Task 2: Модуль фрагментов

**Files:**
- Create: `server/barysguard/services/inspection/fragments.py`
- Test: `server/tests/test_inspection_fragments.py`

**Interfaces:**
- Consumes: `Detector` с `find` и `masks_hits` (Task 1).
- Produces:
  - константы `FRAGMENT_CONTEXT = 80`, `MASK_REACH = 40`, `MAX_FRAGMENTS = 5`, `HISTORY = FRAGMENT_CONTEXT + MASK_REACH`;
  - `mask_text(text: str, detectors: Sequence[Detector]) -> str`;
  - `build_fragment(data: str, start: int, end: int, sample: str, masks_hits: bool, detectors: Sequence[Detector], at_doc_start: bool) -> dict[str, str]` с ключами `before`, `hit`, `after`.

- [ ] **Step 1: Написать падающие тесты**

`server/tests/test_inspection_fragments.py`:

```python
"""Фрагменты вокруг находок: маскирование контекста и обрезка краёв."""

from barysguard.services.inspection.detectors import (
    CardDetector,
    DictionaryDetector,
    IinBinDetector,
)
from barysguard.services.inspection.fragments import (
    FRAGMENT_CONTEXT,
    build_fragment,
    mask_text,
)

IIN = "900101300017"
IIN_OTHER = "900101300811"
VISA = "4111111111111111"
DETECTORS = [IinBinDetector(), CardDetector(), DictionaryDetector("markings", ["конфиденциально"])]


def _fragment(text: str, needle: str, masks: bool = True, at_start: bool = True) -> dict[str, str]:
    start = text.index(needle)
    end = start + len(needle)
    sample = "*" * 10 + needle[-2:] if masks else needle.lower()
    return build_fragment(text, start, end, sample, masks, DETECTORS, at_start)


def test_mask_text_replaces_other_values_but_keeps_dictionary_terms() -> None:
    text = f"ИИН {IIN_OTHER}, карта 4111 1111 1111 1111, гриф конфиденциально"

    masked = mask_text(text, DETECTORS)

    assert IIN_OTHER not in masked
    assert "4111 1111" not in masked
    assert "конфиденциально" in masked


def test_hit_is_the_mask_and_context_is_masked() -> None:
    text = f"Сотрудник {IIN} вместе с {IIN_OTHER} и картой {VISA} в списке."

    fragment = _fragment(text, IIN)

    assert fragment["hit"] == "*" * 10 + "17"
    joined = fragment["before"] + fragment["hit"] + fragment["after"]
    assert IIN not in joined and IIN_OTHER not in joined and VISA not in joined
    assert "в списке" in fragment["after"]


def test_dictionary_hit_keeps_original_case() -> None:
    text = "Документ имеет гриф КОНФИДЕНЦИАЛЬНО и не подлежит выдаче."

    fragment = _fragment(text, "КОНФИДЕНЦИАЛЬНО", masks=False)

    assert fragment["hit"] == "КОНФИДЕНЦИАЛЬНО"
    assert fragment["before"].endswith("гриф ")


def test_edges_of_the_document_have_no_ellipsis() -> None:
    text = f"{IIN} в начале и конце {IIN_OTHER}"

    first = _fragment(text, IIN)

    assert first["before"] == ""
    assert not first["after"].endswith("…")


def test_cut_context_drops_the_partial_word_and_marks_the_cut() -> None:
    filler = "слово " * 40
    text = f"{filler}{IIN} {filler}"

    fragment = _fragment(text, IIN)

    assert fragment["before"].startswith("…")
    assert fragment["after"].endswith("…")
    assert len(fragment["before"]) <= FRAGMENT_CONTEXT + 1
    assert len(fragment["after"]) <= FRAGMENT_CONTEXT + 1


def test_number_cut_by_the_window_edge_is_not_shown() -> None:
    # Число стоит ровно на границе окна: целиком оно не помещается и не должно показаться обрезанным.
    text = "x" + IIN_OTHER + " " + "а" * 200 + f" {IIN} " + "б" * 200

    fragment = _fragment(text, IIN)

    assert IIN_OTHER[:6] not in fragment["before"]


def test_not_at_document_start_marks_the_left_cut() -> None:
    text = f"середина {IIN} дальше"

    fragment = _fragment(text, IIN, at_start=False)

    assert fragment["before"].startswith("…")
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python -m pytest -q tests/test_inspection_fragments.py`
Expected: FAIL (`ModuleNotFoundError: barysguard.services.inspection.fragments`).

- [ ] **Step 3: Реализовать**

`server/barysguard/services/inspection/fragments.py`:

```python
"""Фрагменты текста вокруг находок.

Контекст может содержать другие чувствительные значения, поэтому до показа он
проходит через те же детекторы, что находят значения: найденное заменяется маской.
Край окна, разрезавший слово или число, не показывается: недопоказанное число
хуже, чем отсутствующее.
"""

import re
from collections.abc import Sequence

from barysguard.services.inspection.detectors import Detector

FRAGMENT_CONTEXT = 80
# Запас сверх видимого контекста: значение, начавшееся за краем окна, всё равно
# находится детектором целиком и маскируется.
MASK_REACH = 40
MAX_FRAGMENTS = 5
HISTORY = FRAGMENT_CONTEXT + MASK_REACH

_SPACES = re.compile(r"\s+")
_ELLIPSIS = "…"


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", text)


def mask_text(text: str, detectors: Sequence[Detector]) -> str:
    """Заменяет маской все совпадения чувствительных детекторов в `text`."""
    spans: list[tuple[int, int, str]] = []
    for detector in detectors:
        if not detector.masks_hits:
            continue
        for sample, start, end in detector.find(text, 0, len(text)):
            if sample:
                spans.append((start, end, sample))
    spans.sort()
    parts: list[str] = []
    position = 0
    for start, end, sample in spans:
        if start < position:
            continue
        parts.append(text[position:start])
        parts.append(sample)
        position = end
    parts.append(text[position:])
    return "".join(parts)


def _clip_left(masked: str, more_before: bool) -> str:
    text = _collapse(masked)
    cut = len(text) > FRAGMENT_CONTEXT
    if cut:
        text = text[-FRAGMENT_CONTEXT:]
    if not (cut or more_before):
        return text
    if text.startswith(" "):
        text = text.lstrip()
    else:
        space = text.find(" ")
        text = text[space + 1 :] if space != -1 else ""
    return _ELLIPSIS + text


def _clip_right(masked: str, more_after: bool) -> str:
    text = _collapse(masked)
    cut = len(text) > FRAGMENT_CONTEXT
    if cut:
        text = text[:FRAGMENT_CONTEXT]
    if not (cut or more_after):
        return text
    if text.endswith(" "):
        text = text.rstrip()
    else:
        space = text.rfind(" ")
        text = text[:space] if space != -1 else ""
    return text + _ELLIPSIS


def build_fragment(
    data: str,
    start: int,
    end: int,
    sample: str,
    masks_hits: bool,
    detectors: Sequence[Detector],
    at_doc_start: bool,
) -> dict[str, str]:
    """Фрагмент вокруг совпадения `data[start:end]`.

    `at_doc_start` — `data[0]` является началом документа (слева ничего нет).
    """
    left = max(0, start - HISTORY)
    more_before = left > 0 or not at_doc_start
    more_after = len(data) > end + HISTORY
    before = mask_text(data[left:start], detectors)
    after = mask_text(data[end : end + HISTORY], detectors)
    hit = sample if masks_hits else _collapse(data[start:end])
    return {
        "before": _clip_left(before, more_before),
        "hit": hit,
        "after": _clip_right(after, more_after),
    }
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python -m pytest -q tests/test_inspection_fragments.py`
Expected: PASS. Если `test_number_cut_by_the_window_edge_is_not_shown` или `test_cut_context_...` не проходят из-за разной длины после маскирования, поправить реализацию (не тест): инвариант — обрезанный край не содержит части числа, длина каждой стороны не больше `FRAGMENT_CONTEXT + 1`.
Run: `.venv/Scripts/ruff check . ; .venv/Scripts/ruff format --check . ; .venv/Scripts/mypy barysguard`
Expected: без ошибок.

- [ ] **Step 5: Commit**

```bash
git add server/barysguard/services/inspection/fragments.py server/tests/test_inspection_fragments.py
git commit -m "feat(server): masked context fragments around findings"
```

---

### Task 3: Сканер собирает фрагменты, держит историю слева

**Files:**
- Modify: `server/barysguard/services/inspection/detectors.py`
- Modify: `server/barysguard/services/inspection/engine.py`
- Test: `server/tests/test_inspection_detectors.py`

**Interfaces:**
- Consumes: `build_fragment`, `MAX_FRAGMENTS`, `HISTORY` (Task 2); `Detector.find` с началом (Task 1).
- Produces: `Finding.fragments: list[dict[str, str]]`; `ScanOutcome.findings[key]["fragments"]`.

Замечание по устройству: `ContentScanner` сейчас оставляет в хвосте `overlap + 1` символов, а совпадения, начавшиеся в хвосте, обрабатываются в следующей порции — слева у них контекста нет. Поэтому хвост теперь начинается на `HISTORY` символов раньше (`_history`), а `overlap` растёт на `HISTORY`, чтобы справа тоже хватало контекста.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `server/tests/test_inspection_detectors.py`:

```python
def _scan_chunks(text: str, size: int, *detectors):
    scanner = ContentScanner(list(detectors))
    for index in range(0, len(text), size):
        scanner.feed(text[index : index + size])
    return scanner.finish()


FILLER = "обычный текст без значений " * 12


def test_fragments_are_collected_with_context_and_capped() -> None:
    text = (FILLER + f"сотрудник {IIN_FIRST_PASS} в списке. ") * 7

    found = ContentScanner([IinBinDetector()])
    found.feed(text)
    result = found.finish()["iin_bin"]

    assert result.count == 7
    assert len(result.fragments) == 5
    first = result.fragments[0]
    assert first["hit"] == "*" * 10 + IIN_FIRST_PASS[-2:]
    assert "сотрудник" in first["before"] and "в списке" in first["after"]
    assert IIN_FIRST_PASS not in repr(result.fragments)


@pytest.mark.parametrize("size", [1, 7, 50, 200, 1000])
def test_fragments_do_not_depend_on_chunk_size(size: int) -> None:
    text = FILLER + f"номер {IIN_FIRST_PASS} дальше " + FILLER + f"гриф КОНФИДЕНЦИАЛЬНО тут " + FILLER
    detectors = (IinBinDetector(), DictionaryDetector("markings", ["конфиденциально"]))

    whole = _scan_chunks(text, len(text), *detectors)
    chunked = _scan_chunks(text, size, *detectors)

    assert {k: f.fragments for k, f in chunked.items()} == {k: f.fragments for k, f in whole.items()}


def test_neighbour_values_stay_masked_across_chunk_boundaries() -> None:
    text = FILLER + f"{IIN_FIRST_PASS} рядом {IIN_SECOND_PASS} и {VISA}. " + FILLER

    for size in (3, 11, 64):
        found = _scan_chunks(text, size, IinBinDetector(), CardDetector())
        shown = repr([f.fragments for f in found.values()])
        assert IIN_FIRST_PASS not in shown
        assert IIN_SECOND_PASS not in shown
        assert VISA not in shown


def test_match_at_the_edges_of_the_document_has_no_false_ellipsis() -> None:
    result = _scan_chunks(f"{IIN_FIRST_PASS} конец", 4, IinBinDetector())["iin_bin"]

    assert result.fragments[0]["before"] == ""
    assert not result.fragments[0]["after"].endswith("…")
```

(`IIN_SECOND_PASS` и `VISA` уже определены в файле.)

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python -m pytest -q tests/test_inspection_detectors.py -k "fragment or neighbour or edges"`
Expected: FAIL (`AttributeError: 'Finding' object has no attribute 'fragments'`).

- [ ] **Step 3: Реализовать**

В `detectors.py`:

1. Импорт: `from barysguard.services.inspection.fragments import HISTORY, MAX_FRAGMENTS, build_fragment`. Чтобы не получить циклический импорт (`fragments.py` импортирует `Detector` из `detectors.py`), в `fragments.py` заменить импорт на условный:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from barysguard.services.inspection.detectors import Detector
```

и в аннотациях `fragments.py` писать `Sequence["Detector"]`.

2. `Finding`:

```python
@dataclass
class Finding:
    count: int = 0
    samples: list[str] = field(default_factory=list)
    fragments: list[dict[str, str]] = field(default_factory=list)
```

3. `ContentScanner.__init__`: добавить `self._at_start = True` и поменять `self._overlap = max(...) + 2 + HISTORY`.

4. `feed` — хвост с историей слева:

```python
    def feed(self, chunk: str) -> None:
        data = self._carry + chunk
        owned_end = len(data) - self._overlap
        if owned_end <= self._context:
            self._carry = data
            return
        cut = owned_end - 1  # последний засчитанный символ становится контекстом
        keep_from = max(cut - HISTORY, 0)  # слева ещё HISTORY символов — для фрагментов
        for index, detector in enumerate(self._detectors):
            consumed = self._scan(index, detector, data, owned_end)
            self._resume[index] = max(consumed - keep_from, 0)
        self._carry = data[keep_from:]
        self._context = cut - keep_from + 1
        self._at_start = self._at_start and keep_from == 0
```

5. `_scan`: заменить цикл по совпадениям

```python
        for sample, begin, match_end in detector.find(data, max(self._context, consumed), end):
            if sample:
                finding = self._findings[detector.key]
                finding.add(sample)
                if len(finding.fragments) < MAX_FRAGMENTS:
                    finding.fragments.append(
                        build_fragment(
                            data, begin, match_end, sample, detector.masks_hits,
                            self._detectors, self._at_start,
                        )
                    )
            self._open[index] = not sample and match_end >= len(data)
            consumed = match_end
```

В `engine.py`: в словаре `findings` добавить `"fragments": finding.fragments`:

```python
    findings = {
        key: {"count": finding.count, "samples": finding.samples, "fragments": finding.fragments}
        for key, finding in found.items()
        if finding.count > 0
    }
```

и обновить комментарий в `ScanOutcome`: `# {rule_key: {"count": n, "samples": [...], "fragments": [...]}}`.

- [ ] **Step 4: Прогнать весь набор детекторов**

Run: `.venv/Scripts/python -m pytest -q tests/test_inspection_detectors.py tests/test_regex_detector.py tests/test_inspection_fragments.py`
Expected: PASS, включая прежние тесты на независимость от размера порции и поглощённые длинные совпадения. Если прежние тесты на стыках порций упали, ошибка в арифметике `keep_from`/`_context`/`_resume`: сверить с правилом «позиция `consumed` в старом буфере → `consumed - keep_from` в хвосте; считаются только совпадения, начавшиеся в хвосте на индексе `_context` и дальше».
Run: `.venv/Scripts/python -m pytest -q` (весь набор сервера) и `ruff`/`mypy`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/barysguard/services/inspection/detectors.py server/barysguard/services/inspection/engine.py server/barysguard/services/inspection/fragments.py server/tests/test_inspection_detectors.py
git commit -m "feat(server): scanner collects fragments with left history"
```

---

### Task 4: Вердикт и API отдают фрагменты, вес и потолок

**Files:**
- Modify: `server/barysguard/services/inspection/scoring.py`
- Modify: `server/barysguard/api/schemas.py`
- Modify: `server/barysguard/api/incidents.py`
- Modify: `api/gateway-v1.yaml` (перегенерировать)
- Test: `server/tests/test_inspection_scoring.py`, `server/tests/test_incidents_api.py`

**Interfaces:**
- Consumes: `findings[key]["fragments"]` (Task 3); `RuleWeight.weight`, `RuleWeight.cap`.
- Produces: элемент `matches` вердикта: `{"rule_key", "rule_title", "rule_version_id", "count", "points", "samples", "weight", "cap", "fragments"}`; в API `IncidentMatch.weight: int`, `cap: int`, `fragments: list[MatchFragment]` (`MatchFragment{before, hit, after}`), все с умолчаниями (`0`, `0`, `[]`).

- [ ] **Step 1: Написать падающие тесты**

В `server/tests/test_inspection_scoring.py` добавить:

```python
def test_matches_carry_weight_cap_and_fragments() -> None:
    fragment = {"before": "до ", "hit": "**********17", "after": " после"}
    findings = {"iin_bin": {"count": 2, "samples": ["**********17"], "fragments": [fragment]}}

    [match] = evaluate(findings, WEIGHTS).matches

    assert match["weight"] == 20 and match["cap"] == 5
    assert match["fragments"] == [fragment]


def test_matches_without_fragments_get_an_empty_list() -> None:
    [match] = evaluate({"card": {"count": 1, "samples": ["***"]}}, WEIGHTS).matches

    assert match["fragments"] == []
```

В `server/tests/test_incidents_api.py`: в `MATCHES` добавить ключи `"weight": 20, "cap": 5, "fragments": [{"before": "ИИН ", "hit": "**********17", "after": " в списке"}]`; в ожидаемом `body["matches"]` (тест `test_detail_has_verdict_matches_and_events_without_full_values`) добавить те же `"weight": 20, "cap": 5, "fragments": [...]`. Новый тест на старый вердикт:

```python
async def test_old_verdict_without_fragments_gives_empty_defaults(app_client, session) -> None:
    await login_as(app_client, session, username="inc-old", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-old")
    old = [{"rule_key": "iin_bin", "rule_version_id": "v1", "count": 1, "points": 20, "samples": []}]
    global MATCHES
    saved, MATCHES = MATCHES, old
    try:
        incident = await _flagged(session, agent.agent_id)
    finally:
        MATCHES = saved

    body = (await app_client.get(f"/api/v1/incidents/{incident.id}")).json()

    assert body["matches"][0]["fragments"] == []
    assert body["matches"][0]["weight"] == 0 and body["matches"][0]["cap"] == 0
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python -m pytest -q tests/test_inspection_scoring.py tests/test_incidents_api.py`
Expected: FAIL (`KeyError: 'weight'` и расхождение тела ответа).

- [ ] **Step 3: Реализовать**

`scoring.py`, в `evaluate()` словарь совпадения:

```python
        matches.append(
            {
                "rule_key": rule.key,
                "rule_title": rule.title,
                "rule_version_id": rule.rule_version_id,
                "count": count,
                "points": points,
                "weight": rule.weight,
                "cap": rule.cap,
                "samples": list(found.get("samples", [])),
                "fragments": list(found.get("fragments", [])),
            }
        )
```

`api/schemas.py`:

```python
class MatchFragment(BaseModel):
    before: str
    hit: str
    after: str


class IncidentMatch(BaseModel):
    rule_key: str
    rule_title: str = ""
    count: int
    points: int
    weight: int = 0
    cap: int = 0
    samples: list[str]
    fragments: list[MatchFragment] = []
```

`api/incidents.py`, в сборке `IncidentMatch(...)` добавить:

```python
                weight=m.get("weight", 0),
                cap=m.get("cap", 0),
                fragments=[MatchFragment(**fragment) for fragment in m.get("fragments", [])],
```

и импортировать `MatchFragment` из `barysguard.api.schemas`.

Перегенерировать контракт командой `REGENERATE` из `server/tests/test_openapi_contract.py` (запуск из `server/`):

```bash
.venv/Scripts/python -c "import yaml; from barysguard.main import create_app; open('../api/gateway-v1.yaml', 'w', encoding='utf-8', newline='\n').write(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True))"
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS (включая `test_openapi_contract.py`).
Run: `.venv/Scripts/ruff check . ; .venv/Scripts/ruff format --check . ; .venv/Scripts/mypy barysguard`
Expected: без ошибок.

- [ ] **Step 5: Commit**

```bash
git add server/barysguard/services/inspection/scoring.py server/barysguard/api/schemas.py server/barysguard/api/incidents.py api/gateway-v1.yaml server/tests/test_inspection_scoring.py server/tests/test_incidents_api.py
git commit -m "feat(server): incident matches expose weight, cap and masked fragments"
```

---

### Task 5: Документация сервера

**Files:**
- Modify: `docs/DLP_WORKER.md`
- Modify: `docs/superpowers/specs/2026-10-10-incident-evidence-design.md`

- [ ] **Step 1: Обновить `DLP_WORKER.md`**

В разделе «Как это работает» заменить фразу «В базе хранятся только счётчики и маски (ИИН/БИН — две последние цифры, карта — четыре последние), полные значения не сохраняются.» на:

```
В базе хранятся счётчики, маски (ИИН/БИН — две последние цифры, карта — четыре последние) и короткие фрагменты текста вокруг находок: до 5 на правило, по 80 символов слева и справа. В фрагментах все чувствительные значения (в самом совпадении и в соседнем тексте) заменены масками, полные значения не сохраняются. Края фрагмента, разрезавшие слово или число, отбрасываются и отмечаются «…». Словарные термины показываются как есть.
```

В разделе «API» к строке `GET /api/v1/incidents/{id}` дописать: «совпадения содержат `weight`, `cap` и `fragments` (`before`, `hit`, `after`)». В «Ограничения этой версии» добавить: «Вердикты, созданные до появления фрагментов, их не содержат и не пересчитываются.»

- [ ] **Step 2: Поправить спеку**

В спеке `2026-10-10-incident-evidence-design.md` раздел 3.3: заменить абзац «Известное ограничение…» на «Сканер держит слева от хвоста порции `HISTORY = FRAGMENT_CONTEXT + MASK_REACH` символов истории, а запас справа увеличен на столько же, поэтому фрагмент не зависит от размера порций; короче 80 символов контекст бывает только у настоящих краёв документа». Статус в шапке спеки: «реализуется».

- [ ] **Step 3: Commit**

```bash
git add docs/DLP_WORKER.md docs/superpowers/specs/2026-10-10-incident-evidence-design.md
git commit -m "docs: masked fragments in the DLP worker guide and spec"
```

---

### Task 6: Тема: токены, шрифты, анимации

**Files:**
- Modify: `web/package.json` (зависимости шрифтов; скрипт `licenses`)
- Modify: `web/src/main.tsx`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/components/Badge.tsx`, `Badge.module.css`, `StatusBadge.tsx`, `Table.module.css`, `Button.module.css`, `Modal.module.css`, `Toast.module.css`
- Modify: `web/src/app/Shell.tsx`, `Shell.module.css`
- Test: `web/src/components/StatusBadge.test.tsx`, `web/src/app/Shell.test.tsx` (проверить, что проходят)

**Interfaces:**
- Produces: токены `--accent-soft`, `--glow`, `--link`, `--dur-fast|mid|slow`, `--ease-out`; ключевые кадры `fade-up`, `row-in`, `slide-in-right`, `pop-in`, `pulse-glow`, `shimmer`; `Tone` расширен значением `"critical"`.

- [ ] **Step 1: Установить шрифты и проверить имена файлов**

Run (из `web/`): `npm install @fontsource/ibm-plex-sans @fontsource/jetbrains-mono`
Run: `ls node_modules/@fontsource/ibm-plex-sans/ | grep -E "^(cyrillic|latin)-(400|500|600|700)\.css$"` и то же для `jetbrains-mono` (нужны 400 и 500).
Expected: файлы существуют. Если названия отличаются, использовать фактические.

В `package.json` скрипта `licenses` в списке `--onlyAllow` добавить `;OFL-1.1`.

- [ ] **Step 2: Подключить шрифты в `main.tsx`**

Перед `import "./styles/global.css";` добавить:

```ts
import "@fontsource/ibm-plex-sans/cyrillic-400.css";
import "@fontsource/ibm-plex-sans/cyrillic-500.css";
import "@fontsource/ibm-plex-sans/cyrillic-600.css";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/jetbrains-mono/cyrillic-400.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
```

- [ ] **Step 3: Переписать токены и добавить анимации в `global.css`**

Заменить блок `:root { ... }` и блок `@media (prefers-color-scheme: dark) { ... }` одним:

```css
:root {
  color-scheme: dark;
  --bg: #0a0a0c;
  --surface: #131316;
  --surface-2: #1b1b20;
  --border: #2a2a31;
  --text: #ececf0;
  --muted: #9b9ba6;
  --accent: #d4202f;
  --accent-hover: #ee3b4a;
  --accent-soft: rgba(212, 32, 47, 0.16);
  --on-accent: #ffffff;
  --link: #ff7b82;
  --danger: #ff6b73;
  --danger-hover: #ff8a90;
  --on-danger: #1a0507;
  --danger-bg: #2c1013;
  --ok: #6fd69a;
  --ok-bg: #11271a;
  --warn: #f0b95a;
  --warn-bg: #2d210b;
  --info: #a8b3c7;
  --info-bg: #1c212b;
  --neutral-bg: #222229;
  --shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
  --glow: 0 0 0 1px rgba(212, 32, 47, 0.5), 0 0 18px rgba(212, 32, 47, 0.35);
  --overlay: rgba(0, 0, 0, 0.72);
  --radius: 6px;
  --font: "IBM Plex Sans", system-ui, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
  --dur-fast: 120ms;
  --dur-mid: 220ms;
  --dur-slow: 420ms;
  --ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
}
```

В `body` добавить `-webkit-font-smoothing: antialiased;` и `font-feature-settings: "tnum" 1;`. Ссылки: `a { color: var(--link); }`. После существующих правил добавить:

```css
h1,
h2,
h3 {
  letter-spacing: -0.01em;
}

@keyframes fade-up {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes row-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes slide-in-right {
  from {
    opacity: 0;
    transform: translateX(36px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes pop-in {
  from {
    opacity: 0;
    transform: scale(0.97);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes pulse-glow {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(255, 70, 80, 0);
  }
  50% {
    box-shadow: 0 0 10px 2px rgba(255, 70, 80, 0.45);
  }
}

@keyframes shimmer {
  to {
    background-position: -200% 0;
  }
}

.skeleton {
  height: 14px;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--surface-2) 25%, var(--border) 50%, var(--surface-2) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
}
```

Существующий блок `@media (prefers-reduced-motion: reduce)` расширить:

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
}
```

(правило `.spinner__dot { animation: none; }` оставить внутри этого же блока.)

- [ ] **Step 4: Обновить компоненты**

`Badge.tsx`: `export type Tone = "neutral" | "ok" | "warn" | "danger" | "info" | "critical";`

`Badge.module.css` — добавить:

```css
.critical {
  background: var(--danger-bg);
  color: var(--danger);
  box-shadow: inset 0 0 0 1px rgba(255, 107, 115, 0.45);
  animation: pulse-glow 2.4s ease-in-out infinite;
}
```

и сделать значки «чипами»: `.badge { border-radius: 4px; letter-spacing: 0.02em; }`.

`StatusBadge.tsx`: в `SEVERITY_TONES` значение `critical: "critical"`.

`Table.module.css` — добавить после существующих правил:

```css
.table tbody tr {
  animation: row-in var(--dur-mid) var(--ease-out) both;
  transition: background var(--dur-fast);
}

.table tbody tr:hover {
  background: var(--surface-2);
}

.table tbody tr:nth-child(2) { animation-delay: 30ms; }
.table tbody tr:nth-child(3) { animation-delay: 60ms; }
.table tbody tr:nth-child(4) { animation-delay: 90ms; }
.table tbody tr:nth-child(5) { animation-delay: 120ms; }
.table tbody tr:nth-child(6) { animation-delay: 150ms; }
.table tbody tr:nth-child(7) { animation-delay: 180ms; }
.table tbody tr:nth-child(8) { animation-delay: 210ms; }
.table tbody tr:nth-child(n + 9) { animation-delay: 240ms; }

.table th {
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: 11px;
}
```

`Button.module.css`: добавить в `.button` `transition: background var(--dur-fast), border-color var(--dur-fast), box-shadow var(--dur-fast);`, а `.primary:hover:not(:disabled) { background: var(--accent-hover); box-shadow: var(--glow); }`.

`Modal.module.css`: у `.dialog` добавить `animation: pop-in var(--dur-mid) var(--ease-out);` у `.overlay` — `animation: fade-up var(--dur-mid) ease-out;` не нужно, достаточно `animation: pop-in`.

`Toast.module.css`: у `.toast` добавить `animation: slide-in-right var(--dur-mid) var(--ease-out);`.

`Shell.module.css`: `.active { background: var(--accent-soft); color: var(--text); font-weight: 600; box-shadow: inset 2px 0 0 var(--accent); }`; `.link { transition: background var(--dur-fast); }`; добавить `.page { animation: fade-up var(--dur-slow) var(--ease-out); }`.

`Shell.tsx`: найти `<Outlet />` и обернуть: `<div key={location.pathname} className={styles.page}><Outlet /></div>` (`location` уже получен из `useLocation`).

- [ ] **Step 5: Проверки**

Run (из `web/`): `npx vitest run`
Expected: PASS. Если тест, проверявший цвет/класс критичности, упал из-за `critical`-тона, обновить ожидание на новый класс.
Run: `npm run typecheck ; npm run build ; npm run licenses`
Expected: без ошибок; `licenses` проходит с `OFL-1.1`.

- [ ] **Step 6: Commit**

```bash
git add web
git commit -m "feat(web): dark red theme, local fonts and motion tokens"
```

---

### Task 7: Выезжающая панель инцидента: «Почему сработало» и «Что нашли»

**Files:**
- Modify: `web/src/components/Modal.tsx`, `Modal.module.css` (вариант `drawer`)
- Create: `web/src/features/incidents/ruleName.ts`, `ScoreRing.tsx`, `WhyTriggered.tsx`, `Evidence.tsx`
- Modify: `web/src/features/incidents/IncidentDetailPanel.tsx`, `incidents.module.css`
- Modify: `web/src/i18n/ru.ts`
- Modify: `web/src/api/schema.d.ts` (перегенерировать: `npm run types`)
- Test: `web/src/features/incidents/IncidentsPage.test.tsx`, `web/src/components/Modal.test.tsx`

**Interfaces:**
- Consumes: `IncidentMatch` с `weight?`, `cap?`, `fragments?` (Task 4, после `npm run types`).
- Produces: `Modal` принимает `variant?: "dialog" | "drawer"` (по умолчанию `"dialog"`); `ruleName(match: IncidentMatch): string`; компоненты `ScoreRing({ score, severity })`, `WhyTriggered({ matches, score })`, `Evidence({ matches })`.

- [ ] **Step 1: Перегенерировать типы**

Run (из `web/`): `npm run types`
Expected: в `schema.d.ts` у `IncidentMatch` появились `weight`, `cap`, `fragments` (необязательные).

- [ ] **Step 2: Написать падающие тесты**

В `IncidentsPage.test.tsx` заменить данные `DETAIL.matches` на (все старые ключи остаются, добавляются вес/потолок/фрагменты):

```ts
  matches: [
    {
      rule_key: "iin_bin", count: 2, points: 60, weight: 30, cap: 5,
      samples: ["**********17", "**********42"],
      fragments: [
        { before: "…сотрудник ", hit: "**********17", after: " принят" },
        { before: "…и ", hit: "**********42", after: " тоже" },
      ],
    },
    { rule_key: "card", count: 1, points: 20, weight: 20, cap: 4, samples: ["************1111"], fragments: [] },
    { rule_key: "custom_rule", rule_title: "", count: 1, points: 5, weight: 5, cap: 3, samples: [], fragments: [] },
    { rule_key: "custom_aa11bb22", rule_title: "Номер договора", count: 1, points: 15, weight: 15, cap: 2, samples: ["*****67"], fragments: [] },
  ],
```

Тест «показывает вердикт…»: убрать две строки про `samples` (`"**********17, **********42"`, `"************1111"`) и добавить:

```ts
    expect(within(dialog).getByText("сотрудник", { exact: false })).toBeInTheDocument();
    expect(within(dialog).getByText("**********17")).toBeInTheDocument();
    expect(within(dialog).getByText("30 × 2 = 60")).toBeInTheDocument();
    expect(within(dialog).getByText(/Набрано 80 из 100/)).toBeInTheDocument();
```

Новые тесты в конце `describe("IncidentDetailPanel")`:

```ts
  it("для правила без фрагментов объясняет, что файл проверен до обновления", async () => {
    setup(incidentPage([INCIDENT]));
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    expect(
      (await within(dialog).findAllByText("Фрагменты недоступны: файл проверен до обновления.")).length,
    ).toBeGreaterThan(0);
  });

  it("показывает первые три фрагмента и открывает остальные по кнопке", async () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ before: `до${i} `, hit: "**********17", after: ` после${i}` }));
    mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "GET /incidents": incidentPage([INCIDENT]),
      [`GET /incidents/${ID}`]: json(200, {
        ...DETAIL,
        matches: [{ rule_key: "iin_bin", count: 9, points: 80, weight: 20, cap: 5, samples: [], fragments: many }],
      }),
    });
    renderPage(<IncidentsPage />, route());
    const dialog = await openPanel();

    expect(await within(dialog).findAllByText("**********17")).toHaveLength(3);
    expect(within(dialog).getByText("Показаны первые 5 из 9")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Показать ещё" }));
    expect(within(dialog).getAllByText("**********17")).toHaveLength(5);
  });
```

В `Modal.test.tsx` добавить:

```ts
  it("вариант drawer остаётся диалогом с ловушкой фокуса", () => {
    render(<Modal open title="Панель" onClose={() => {}} variant="drawer"><button>Внутри</button></Modal>);
    expect(screen.getByRole("dialog", { name: "Панель" })).toBeInTheDocument();
  });
```

(Импорты и обёртки — как в соседних тестах этого файла.)

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `npx vitest run src/features/incidents src/components/Modal.test.tsx`
Expected: FAIL (нет текстов «Набрано…», пропса `variant`).

- [ ] **Step 4: Строки интерфейса**

В `web/src/i18n/ru.ts` в `incidents.detail` добавить:

```ts
      why: {
        title: "Почему сработало",
        summary: (score: number) => `Набрано ${score} из 100. Порог инцидента — 20.`,
        formula: (weight: number, count: number, cap: number) =>
          `${weight} × ${Math.min(count, cap)}`,
        points: (points: number) => `${points} балл.`,
        capped: (cap: number) => `учтено не больше ${cap} совпадений`,
      },
      evidence: {
        title: "Что нашли в документе",
        none: "Фрагменты недоступны: файл проверен до обновления.",
        more: "Показать ещё",
        less: "Свернуть",
        shown: (shown: number, total: number) => `Показаны первые ${shown} из ${total}`,
        matches: (count: number) => `совпадений: ${count}`,
      },
```

Формула в тесте — `"30 × 2 = 60"`: для её вывода в `WhyTriggered` использовать `${ru...formula(...)} = ${points}`.

- [ ] **Step 5: Вариант `drawer` у Modal**

`Modal.tsx`: в интерфейс добавить `variant?: "dialog" | "drawer";`, в деструктуризацию `variant = "dialog"`; в разметке:

```tsx
    <div
      className={`${styles.overlay} ${variant === "drawer" ? styles.drawerOverlay : ""}`}
      ...
    >
      <div
        ref={dialogRef}
        className={`${styles.dialog} ${variant === "drawer" ? styles.drawer : ""}`}
```

`Modal.module.css` — добавить:

```css
.drawerOverlay {
  align-items: stretch;
  justify-content: flex-end;
  padding: 0;
}

.drawer {
  width: min(720px, 100%);
  height: 100%;
  max-height: 100%;
  border-radius: 0;
  border-width: 0 0 0 1px;
  animation: slide-in-right var(--dur-mid) var(--ease-out);
}
```

- [ ] **Step 6: Компоненты инцидента**

`ruleName.ts`:

```ts
import type { IncidentMatch } from "../../api/types";
import { ru } from "../../i18n/ru";

export function ruleName(match: IncidentMatch): string {
  return ru.incidents.rules[match.rule_key] ?? (match.rule_title || match.rule_key);
}
```

`ScoreRing.tsx`:

```tsx
const RADIUS = 34;
const LENGTH = 2 * Math.PI * RADIUS;

export function ScoreRing({ score, severity }: { score: number; severity: string }) {
  const clamped = Math.max(0, Math.min(100, score));
  return (
    <svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label={`${clamped} / 100`} data-severity={severity}>
      <circle cx="44" cy="44" r={RADIUS} fill="none" stroke="var(--surface-2)" strokeWidth="7" />
      <circle
        cx="44" cy="44" r={RADIUS} fill="none" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round"
        strokeDasharray={LENGTH} strokeDashoffset={LENGTH * (1 - clamped / 100)}
        transform="rotate(-90 44 44)" style={{ transition: "stroke-dashoffset var(--dur-slow) var(--ease-out)" }}
      />
      <text x="44" y="50" textAnchor="middle" fontSize="22" fontWeight="600" fill="var(--text)">
        {clamped}
      </text>
    </svg>
  );
}
```

`WhyTriggered.tsx`:

```tsx
import type { IncidentMatch } from "../../api/types";
import { ru } from "../../i18n/ru";
import styles from "./incidents.module.css";
import { ruleName } from "./ruleName";

export function WhyTriggered({ matches, score }: { matches: IncidentMatch[]; score: number }) {
  const t = ru.incidents.detail.why;
  const ordered = [...matches].sort((a, b) => b.points - a.points);
  const total = Math.max(score, ordered.reduce((sum, m) => sum + m.points, 0), 1);
  return (
    <section className={styles.block}>
      <h3 className={styles.blockTitle}>{t.title}</h3>
      <p className={styles.note}>{t.summary(score)}</p>
      <ul className={styles.rules}>
        {ordered.map((match) => {
          const weight = match.weight ?? 0;
          const cap = match.cap ?? 0;
          return (
            <li key={match.rule_key} className={styles.rule}>
              <div className={styles.ruleHead}>
                <span className={styles.ruleName}>{ruleName(match)}</span>
                <span className={styles.mono}>
                  {weight > 0 ? `${t.formula(weight, match.count, cap)} = ${match.points}` : match.points}
                </span>
              </div>
              <div className={styles.bar} aria-hidden>
                <div className={styles.barFill} style={{ width: `${Math.round((match.points / total) * 100)}%` }} />
              </div>
              {cap > 0 && match.count > cap ? <span className={styles.muted}>{t.capped(cap)}</span> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
```

`Evidence.tsx`:

```tsx
import { useState } from "react";

import type { IncidentMatch } from "../../api/types";
import { Button } from "../../components/Button";
import { ru } from "../../i18n/ru";
import styles from "./incidents.module.css";
import { ruleName } from "./ruleName";

const FIRST = 3;

function Rule({ match }: { match: IncidentMatch }) {
  const t = ru.incidents.detail.evidence;
  const [expanded, setExpanded] = useState(false);
  const fragments = match.fragments ?? [];
  const visible = expanded ? fragments : fragments.slice(0, FIRST);
  return (
    <div className={styles.evidenceRule}>
      <div className={styles.ruleHead}>
        <span className={styles.ruleName}>{ruleName(match)}</span>
        <span className={styles.muted}>{t.matches(match.count)}</span>
      </div>
      {fragments.length === 0 ? (
        <p className={styles.muted}>{t.none}</p>
      ) : (
        <>
          <ul className={styles.fragments}>
            {visible.map((fragment, index) => (
              <li key={index} className={styles.fragment}>
                <span>{fragment.before}</span>
                <mark className={styles.hit}>{fragment.hit}</mark>
                <span>{fragment.after}</span>
              </li>
            ))}
          </ul>
          {match.count > fragments.length ? (
            <span className={styles.muted}>{t.shown(fragments.length, match.count)}</span>
          ) : null}
          {fragments.length > FIRST ? (
            <Button onClick={() => setExpanded((value) => !value)}>
              {expanded ? t.less : t.more}
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}

export function Evidence({ matches }: { matches: IncidentMatch[] }) {
  return (
    <section className={styles.block}>
      <h3 className={styles.blockTitle}>{ru.incidents.detail.evidence.title}</h3>
      {[...matches].sort((a, b) => b.points - a.points).map((match) => (
        <Rule key={match.rule_key} match={match} />
      ))}
    </section>
  );
}
```

`IncidentDetailPanel.tsx`: заменить `<Modal ...>` на `<Modal open=... title=... onClose=... variant="drawer">`; в `Detail`:
- шапку (до `<dl>`) заменить на блок `.head`: слева `SeverityBadge`, `IncidentStatusBadge`, заголовок `incident.title` (`<h3 className={styles.headTitle}>`), справа `ScoreRing` и кнопки действий (перенести существующий блок кнопок из конца в `.head`, логику `change/pending` не менять);
- таблицу «Совпадения» (`<h3>{t.matches}</h3>` и `Table` с совпадениями) заменить на `<WhyTriggered matches={incident.matches} score={incident.score} />` и `<Evidence matches={incident.matches} />`; если совпадений нет — оставить `<p className={styles.muted}>{t.matchesEmpty}</p>`;
- `<dl>` с фактами и таблицу событий оставить, расположив после доказательств.

Состояние загрузки в `Body`: вместо `<Spinner>` — скелетон:

```tsx
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">{ru.common.loading}</span>
      {[80, 60, 90, 50].map((width) => (
        <div key={width} className="skeleton" style={{ width: `${width}%`, margin: "14px 0" }} />
      ))}
    </div>
  );
```

`incidents.module.css` — добавить (токены из Task 6):

```css
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.headTitle {
  margin: 8px 0 0;
  font-size: 18px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.block {
  margin-top: 22px;
  animation: fade-up var(--dur-slow) var(--ease-out) both;
}

.blockTitle {
  margin: 0 0 10px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}

.rules,
.fragments {
  margin: 0;
  padding: 0;
  list-style: none;
}

.rule {
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}

.ruleHead {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.ruleName {
  font-weight: 600;
}

.bar {
  height: 4px;
  margin: 8px 0 4px;
  border-radius: 2px;
  background: var(--surface-2);
}

.barFill {
  height: 100%;
  border-radius: 2px;
  background: var(--accent);
  transition: width var(--dur-slow) var(--ease-out);
}

.evidenceRule {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}

.fragment {
  padding: 8px 10px;
  margin-bottom: 6px;
  border-left: 2px solid var(--accent);
  background: var(--surface-2);
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.hit {
  padding: 1px 3px;
  border-radius: 3px;
  background: var(--accent-soft);
  color: var(--link);
  box-shadow: inset 0 0 0 1px rgba(212, 32, 47, 0.5);
}
```

В `.mono` заменить шрифт на `font-family: var(--mono);`.

- [ ] **Step 7: Прогнать тесты**

Run: `npx vitest run`
Expected: PASS. Старые тесты «Принять/Закрыть/409» проходят: кнопки остались внутри `dialog`.
Run: `npm run typecheck ; npm run build`
Expected: без ошибок.

- [ ] **Step 8: Commit**

```bash
git add web
git commit -m "feat(web): incident drawer with rule breakdown and masked document fragments"
```

---

### Task 8: Проверка на стенде и заметки

**Files:**
- Modify: `C:\Users\User\.claude\projects\C--Users-User-Desktop-BarysGuard-BarysGuard\memory\project-pending-next-steps.md` (память)

- [ ] **Step 1: Полные проверки**

Run (сервер, из `server/`): `.venv/Scripts/python -m pytest -q ; .venv/Scripts/ruff check . ; .venv/Scripts/ruff format --check . ; .venv/Scripts/mypy barysguard`
Run (веб, из `web/`): `npx vitest run ; npm run typecheck ; npm run build ; npm run licenses`
Run (агент, из `agent/`): `go test ./...`
Expected: всё зелёное.

- [ ] **Step 2: Пересобрать стенд и посмотреть**

Run (из корня, cmd/PowerShell): `.\stand.cmd up` затем `.\smoke.cmd`.
Ручная проверка: в Windows-агенте (`C:\BarysGuardTest`) загрузить через Brave на Google Drive документ, в котором есть ИИН, номер карты и слово «конфиденциально»; открыть http://localhost:8080 → «Инциденты» → «Подробнее». Ожидается: панель выезжает справа; видны «Почему сработало» с формулой и полосой; «Что нашли в документе» с подсвеченными масками; соседний ИИН и карта замаскированы; полное значение нигде не видно; страница в чёрно-красной теме.

- [ ] **Step 3: Обновить заметки**

В `project-pending-next-steps.md` дописать: «Этап 1 доказательств инцидента и темы реализован (дата), ветка …; этап 2 — редизайн остальных экранов; B3 IoC/YARA после».

- [ ] **Step 4: Commit (если менялись файлы репозитория)**

```bash
git status --short
```
