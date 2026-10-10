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
# находится детектором целиком и маскируется.
MASK_REACH = 40
MAX_FRAGMENTS = 5
HISTORY = FRAGMENT_CONTEXT + MASK_REACH

_SPACES = re.compile(r"\s+")
_ELLIPSIS = "…"


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
    detectors: Sequence["Detector"],
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
