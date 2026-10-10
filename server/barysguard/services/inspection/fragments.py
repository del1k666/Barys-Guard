"""Фрагменты текста вокруг находок.

Контекст может содержать другие чувствительные значения, поэтому до показа он
проходит через те же детекторы, что находят значения: найденное заменяется маской.
Край окна, разрезавший слово или число, не показывается: недопоказанное число
хуже, чем отсутствующее.
"""

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Только для типов: detectors.py сам импортирует этот модуль.
    from barysguard.services.inspection.detectors import Detector

FRAGMENT_CONTEXT = 80
# Запас сверх видимого контекста: значение, начавшееся за краем окна, всё равно
# находится детектором целиком и маскируется. Это минимум: при более длинных значениях
# маскирующих детекторов запас равен самому длинному из них (см. history_for).
MASK_REACH = 40
MAX_FRAGMENTS = 5
# Окно по умолчанию (для вызовов без своих детекторов).
HISTORY = FRAGMENT_CONTEXT + MASK_REACH

_SPACES = re.compile(r"\s+")
_ELLIPSIS = "…"


def history_for(detectors: Sequence["Detector"]) -> int:
    """Окно маскирования с каждой стороны совпадения для данного набора детекторов.

    Значение маскирующего детектора, задевшее видимые FRAGMENT_CONTEXT символов, целиком
    лежит в окне, поэтому находится детектором и маскируется, а не показывается хвостом.
    """
    reach = max((d.max_length for d in detectors if d.masks_hits), default=0)
    return FRAGMENT_CONTEXT + max(MASK_REACH, reach)


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", text)


def mask_text(text: str, detectors: Sequence["Detector"]) -> str:
    """Заменяет маской все совпадения чувствительных детекторов в `text`."""
    spans: list[tuple[int, int, str]] = []
    for detector in detectors:
        if not detector.masks_hits:
            continue
        for sample, start, end in detector.find(text, 0, len(text)):
            if sample:
                spans.append((start, end, sample))
    # При общем начале первым идёт более длинное совпадение.
    spans.sort(key=lambda span: (span[0], -span[1]))
    parts: list[str] = []
    position = 0
    for start, end, sample in spans:
        if start < position:
            # Пересечение: хвост за уже выведенным не показываем (ни текстом, ни маской),
            # иначе часть более длинного значения осталась бы открытой.
            position = max(position, end)
            continue
        parts.append(text[position:start])
        parts.append(sample)
        position = end
    parts.append(text[position:])
    return "".join(parts)


# Видимая зона отсчитывается в исходных символах, до схлопывания пробелов: иначе текст
# из одних пробелов и переводов строк растянул бы её на запас маскирования, где хвост
# значения, начавшегося за окном, уже не узнаётся детектором. Поэтому в таком тексте
# видно меньше FRAGMENT_CONTEXT символов.


def _clip_left(masked: str, more_before: bool) -> str:
    cut = len(masked) > FRAGMENT_CONTEXT
    text = _collapse(masked[-FRAGMENT_CONTEXT:])
    if not (cut or more_before):
        return text
    if text.startswith(" "):
        text = text.lstrip()
    else:
        space = text.find(" ")
        text = text[space + 1 :] if space != -1 else ""
    return _ELLIPSIS + text


def _clip_right(masked: str, more_after: bool) -> str:
    cut = len(masked) > FRAGMENT_CONTEXT
    text = _collapse(masked[:FRAGMENT_CONTEXT])
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
    detectors: Sequence["Detector"],
    at_doc_start: bool,
    *,
    history: int = HISTORY,
) -> dict[str, str]:
    """Фрагмент вокруг совпадения `data[start:end]`.

    `at_doc_start` — `data[0]` является началом документа (слева ничего нет).

    `history` — окно маскирования с каждой стороны, обычно `history_for(detectors)`.

    Контракт вызывающего: `at_doc_start=False` означает, что слева от `start` передано
    не меньше `history` символов, если текст там есть, то есть `data` не начинается посреди
    значения, которое детектор мог бы не узнать. Защиты от обратного здесь нет.
    """
    left = max(0, start - history)
    more_before = left > 0 or not at_doc_start
    more_after = len(data) > end + history
    before = mask_text(data[left:start], detectors)
    after = mask_text(data[end : end + history], detectors)
    hit = sample if masks_hits else _collapse(data[start:end])
    return {
        "before": _clip_left(before, more_before),
        "hit": hit,
        "after": _clip_right(after, more_after),
    }
