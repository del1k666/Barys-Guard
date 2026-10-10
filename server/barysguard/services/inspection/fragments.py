"""Фрагменты текста вокруг находок.

Контекст может содержать другие чувствительные значения, поэтому до показа он
проходит через те же детекторы, что находят значения: найденное заменяется маской.
Край окна, разрезавший слово или число, не показывается: недопоказанное число
хуже, чем отсутствующее.

Маскирование в окне может разойтись с поиском по всему тексту: совпадения детекторов
сцепляются нелокально (ложная карта у края окна съедает цифру настоящей, и настоящая
в окне уже не находится), а значения с необычными разделителями не находятся вовсе.
Поэтому последний рубеж не зависит от детекторов: открытые числа из видимого контекста
убирает `scrub_digits`. Фрагмент — контекст находки, а не доказательство числа, так что
заодно пропадают и безобидные длинные числа, даты и телефоны.
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

# Что склеивает цифры в одно число: любые пробелы (в т. ч. неразрывный, табуляция,
# разделитель строк), дефисы и тире, невидимые символы форматирования (мягкий перенос,
# нулевой пробел и т. п.) и знаки, которыми пишут номера и даты. `*` сюда не входит:
# маска разрывает число, а её хвост сам становится коротким числом.
_DIGIT_JOINERS = (
    r"\s\-\u2010-\u2015\u2212\ufe58\ufe63\uff0d"
    r"\u00ad\u180e\u200b-\u200f\u2060-\u2064\ufeff"
    r".,/\\_()'"
)
_DIGIT_RUN = re.compile(rf"\d(?:[{_DIGIT_JOINERS}]*\d)*")
_ONLY_JOINERS = re.compile(rf"[{_DIGIT_JOINERS}]*")
_DIGIT = re.compile(r"\d")
# Число из стольких цифр уже может быть частью значения: убирается всегда.
MIN_HIDDEN_DIGITS = 5
# Чем заменяется убранное число: один символ, так что текст не удлиняется.
HIDDEN_NUMBER = "•"


def history_for(detectors: Sequence["Detector"]) -> int:
    """Окно маскирования с каждой стороны совпадения для данного набора детекторов.

    Значение маскирующего детектора, задевшее видимые FRAGMENT_CONTEXT символов, целиком
    лежит в окне, поэтому находится детектором и маскируется, а не показывается хвостом.
    """
    reach = max((d.max_length for d in detectors if d.masks_hits), default=0)
    return FRAGMENT_CONTEXT + max(MASK_REACH, reach)


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", text)


def scrub_digits(text: str, *, open_start: bool, open_end: bool) -> str:
    """Заменяет открытые числа в видимом контексте на `HIDDEN_NUMBER`.

    Число — наибольший отрезок цифр, между которыми только `_DIGIT_JOINERS`. Оно
    убирается целиком, если:
    - в нём не меньше `MIN_HIDDEN_DIGITS` цифр;
    - оно касается открытого края (между ним и краем только склейки): у совпадения или
      у обреза «…» число могло продолжаться, и видимой части мало, чтобы судить;
    - вплотную за ним начинается маска `*`: это голова значения, начало которого
      маскирование не нашло.
    Хвост нашей маски (`*` и до четырёх цифр карты или двух ИИН) — короткое число и
    остаётся, если рядом нет других цифр; с соседними цифрами он убирается вместе с ними.
    """

    def hide(match: re.Match[str]) -> str:
        digits = len(_DIGIT.findall(match.group()))
        if (
            digits >= MIN_HIDDEN_DIGITS
            or (open_start and _ONLY_JOINERS.fullmatch(text, 0, match.start()))
            or (open_end and _ONLY_JOINERS.fullmatch(text, match.end()))
            or text.startswith("*", match.end())
        ):
            return HIDDEN_NUMBER
        return match.group()

    return _DIGIT_RUN.sub(hide, text)


def _mask_spans(text: str, detectors: Sequence["Detector"]) -> list[tuple[int, int, str]]:
    """Скрываемые участки текста: (начало, конец, маска), по возрастанию, без пересечений.

    Пересекающиеся совпадения сливаются в один участок с маской первого: хвост за ним
    не показывается ни текстом, ни маской, иначе часть более длинного значения осталась
    бы открытой. Маска не длиннее своего участка (лишнее слева отрезается), чтобы фрагмент
    не выходил за видимую длину.
    """
    found: list[tuple[int, int, str]] = []
    for detector in detectors:
        if not detector.masks_hits:
            continue
        for sample, start, end in detector.find(text, 0, len(text)):
            if sample:
                found.append((start, end, sample))
    # При общем начале первым идёт более длинное совпадение.
    found.sort(key=lambda span: (span[0], -span[1]))
    spans: list[tuple[int, int, str]] = []
    for start, end, sample in found:
        if spans and start < spans[-1][1]:
            first_start, first_end, first_sample = spans[-1]
            spans[-1] = (first_start, max(first_end, end), first_sample)
            continue
        spans.append((start, end, sample))
    return [(start, end, sample[-(end - start) :]) for start, end, sample in spans]


def mask_text(text: str, detectors: Sequence["Detector"]) -> str:
    """Заменяет маской все совпадения чувствительных детекторов в `text`."""
    visible, _ = _visible(text, _mask_spans(text, detectors), 0, len(text))
    return visible


def _visible(
    text: str, spans: Sequence[tuple[int, int, str]], low: int, high: int
) -> tuple[str, bool]:
    """Зона `text[low:high]` с масками вместо значений; второй результат — что-то отброшено.

    Значение, целиком лежащее в зоне, заменяется маской. Значение, пересекающее край
    зоны, отбрасывается целиком (ни текстом, ни маской): его часть за краем — запас
    маскирования, а не видимый текст.
    """
    parts: list[str] = []
    position = low
    dropped = False
    for start, end, sample in spans:
        if end <= low or start >= high:
            continue
        parts.append(text[position : max(start, low)])
        if start < low or end > high:
            dropped = True
        else:
            parts.append(sample)
        position = min(end, high)
    parts.append(text[position:high])
    return "".join(parts), dropped


# Видимая зона — FRAGMENT_CONTEXT исходных символов окна у совпадения: не после
# маскирования (маска бывает короче значения) и не после схлопывания пробелов. Иначе
# зона уезжала бы в запас маскирования, где хвост значения, начавшегося за окном,
# детектором уже не узнаётся. Поэтому в тексте с пробелами и масками видно меньше
# FRAGMENT_CONTEXT символов. Маски не длиннее значений, схлопывание не удлиняет текст:
# каждая сторона не длиннее FRAGMENT_CONTEXT + 1 (с «…»). `scrub_digits` заменяет
# число одним символом и тоже не удлиняет текст. Он работает до добавления «…»: край у
# совпадения открыт всегда, внешний — когда есть обрез.


def _clip_left(window: str, detectors: Sequence["Detector"], more_before: bool) -> str:
    low = max(0, len(window) - FRAGMENT_CONTEXT)
    visible, dropped = _visible(window, _mask_spans(window, detectors), low, len(window))
    cut = low > 0 or dropped
    text = _collapse(visible)
    if not (cut or more_before):
        return scrub_digits(text, open_start=False, open_end=True)
    if text.startswith(" "):
        text = text.lstrip()
    else:
        space = text.find(" ")
        text = text[space + 1 :] if space != -1 else ""
    return _ELLIPSIS + scrub_digits(text, open_start=True, open_end=True)


def _clip_right(window: str, detectors: Sequence["Detector"], more_after: bool) -> str:
    high = min(len(window), FRAGMENT_CONTEXT)
    visible, dropped = _visible(window, _mask_spans(window, detectors), 0, high)
    cut = high < len(window) or dropped
    text = _collapse(visible)
    if not (cut or more_after):
        return scrub_digits(text, open_start=True, open_end=False)
    if text.endswith(" "):
        text = text.rstrip()
    else:
        space = text.rfind(" ")
        text = text[:space] if space != -1 else ""
    return scrub_digits(text, open_start=True, open_end=True) + _ELLIPSIS


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
    hit = sample if masks_hits else _collapse(data[start:end])
    return {
        "before": _clip_left(data[left:start], detectors, more_before),
        "hit": hit,
        "after": _clip_right(data[end : end + history], detectors, more_after),
    }
