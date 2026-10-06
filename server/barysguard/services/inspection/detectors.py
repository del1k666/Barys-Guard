"""Детекторы чувствительных данных и потоковый сканер текста.

Каждое совпадение проверяется контрольной суммой: случайные цифры не должны
давать инцидентов. Найденное значение не сохраняется: детектор возвращает
только маску (ИИН/БИН — 2 последние цифры, карта — 4 последние).
"""

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

MAX_SAMPLES = 5

_WEIGHTS_FIRST = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
_WEIGHTS_SECOND = (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2)
_CENTURY = {1: 1800, 2: 1800, 3: 1900, 4: 1900, 5: 2000, 6: 2000}


def normalize(text: str) -> str:
    """Нижний регистр и ё=е: словарь и текст сравниваются в одном виде."""
    return text.lower().replace("ё", "е")


def kz_control_ok(number: str) -> bool:
    """Контрольный разряд ИИН/БИН: веса 1..11, при остатке 10 — веса 3..11,1,2."""
    head = [int(char) for char in number[:11]]
    total = sum(w * d for w, d in zip(_WEIGHTS_FIRST, head, strict=True)) % 11
    if total == 10:
        total = sum(w * d for w, d in zip(_WEIGHTS_SECOND, head, strict=True)) % 11
        if total == 10:
            return False
    return total == int(number[11])


def is_iin(number: str) -> bool:
    if not kz_control_ok(number):
        return False
    century = _CENTURY.get(int(number[6]))
    if century is None:
        return False
    try:
        date(century + int(number[0:2]), int(number[2:4]), int(number[4:6]))
    except ValueError:
        return False
    return True


def is_bin(number: str) -> bool:
    return kz_control_ok(number) and 1 <= int(number[2:4]) <= 12 and number[4] in "456"


def luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _card_network_ok(digits: str) -> bool:
    size = len(digits)
    if digits[0] == "4" and size in (13, 16, 19):  # Visa
        return True
    if size == 16 and (51 <= int(digits[:2]) <= 55 or 2221 <= int(digits[:4]) <= 2720):
        return True  # Mastercard
    if size == 16 and 2200 <= int(digits[:4]) <= 2204:  # МИР
        return True
    return size == 15 and digits[:2] in ("34", "37")  # Amex


@dataclass
class Finding:
    count: int = 0
    samples: list[str] = field(default_factory=list)

    def add(self, sample: str) -> None:
        self.count += 1
        if len(self.samples) < MAX_SAMPLES:
            self.samples.append(sample)


class Detector(Protocol):
    key: str
    # Сколько символов от начала совпадения детектор может просмотреть (без учёта соседних
    # символов для проверки границ): столько текста сканер держит в запасе на стыке порций.
    max_length: int

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int]]:
        """Настоящие совпадения, начавшиеся в [start, end): (маска образца, конец совпадения).

        Совпадения не пересекаются и идут по возрастанию начала; результат для позиции
        зависит только от текста вокруг неё, а не от того, откуда начат поиск.
        """
        ...


class IinBinDetector:
    max_length = 12
    pattern = re.compile(r"(?<!\d)\d{12}(?!\d)")

    def __init__(self, key: str = "iin_bin") -> None:
        self.key = key

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int]]:
        for match in self.pattern.finditer(data, start):
            if match.start() >= end:
                return
            number = match.group()
            if is_iin(number) or is_bin(number):
                yield "*" * 10 + number[-2:], match.end()


class CardDetector:
    # До 19 цифр и до 18 одиночных разделителей между ними.
    max_length = 37
    pattern = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
    _group_start = re.compile(r"(?<=[ -])\d")

    def __init__(self, key: str = "card") -> None:
        self.key = key

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int]]:
        position = start
        while True:
            match = self.pattern.search(data, position)
            if match is None or match.start() >= end:
                return
            hit = self._check(match)
            if hit is None:
                # Кандидат мог захватить соседнее число («2024-01-15 4111…»): пробуем
                # начать с каждой следующей группы цифр внутри него, первая подходящая — карта.
                for group in self._group_start.finditer(data, match.start() + 1, match.end()):
                    if group.start() >= end:
                        return
                    retry = self.pattern.match(data, group.start())
                    hit = self._check(retry) if retry is not None else None
                    if hit is not None:
                        break
            if hit is None:
                position = match.end()
                continue
            yield hit
            position = hit[1]

    def _check(self, match: re.Match[str]) -> tuple[str, int] | None:
        digits = re.sub(r"\D", "", match.group())
        if luhn_ok(digits) and _card_network_ok(digits):
            return "*" * (len(digits) - 4) + digits[-4:], match.end()
        return None


class DictionaryDetector:
    def __init__(self, key: str, terms: Iterable[str]) -> None:
        self.key = key
        cleaned = sorted(
            {normalize(" ".join(term.split())) for term in terms if term.strip()},
            key=len,
            reverse=True,
        )
        # Пробелы между словами допускают до четырёх пробельных символов подряд.
        self.max_length = max((len(term) + 3 * term.count(" ") for term in cleaned), default=0) + 2
        parts = [r"\s{1,4}".join(re.escape(word) for word in term.split(" ")) for term in cleaned]
        body = "|".join(parts) if parts else "(?!)"
        self.pattern = re.compile(rf"(?<!\w)(?:{body})(?!\w)")

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int]]:
        for match in self.pattern.finditer(data, start):
            if match.start() >= end:
                return
            yield " ".join(match.group().split()), match.end()


class ContentScanner:
    """Потоковый поиск: текст подаётся порциями, совпадение на стыке не теряется.

    Порция склеивается с хвостом прошлой. Считаются только совпадения, начавшиеся
    до последних `overlap` символов (они гарантированно закончились внутри данных);
    остальное переходит в хвост вместе с одним символом контекста, чтобы
    проверка «не часть более длинного числа» видела предыдущий символ. Каждый детектор
    продолжает поиск с конца своего последнего совпадения, даже если оно заходит в хвост.
    """

    def __init__(self, detectors: Sequence[Detector]) -> None:
        self._detectors = list(detectors)
        self._overlap = max((d.max_length for d in self._detectors), default=0) + 2
        self._carry = ""
        self._context = 0
        # Позиция в хвосте, с которой каждый детектор продолжает поиск
        # (конец его прошлого совпадения, если оно зашло в хвост).
        self._resume = [0] * len(self._detectors)
        self._findings = {d.key: Finding() for d in self._detectors}

    def feed(self, chunk: str) -> None:
        data = self._carry + normalize(chunk)
        owned_end = len(data) - self._overlap
        if owned_end <= self._context:
            self._carry = data
            return
        cut = owned_end - 1  # хвост начинается с символа контекста
        for index, detector in enumerate(self._detectors):
            consumed = self._scan(index, detector, data, owned_end)
            self._resume[index] = max(consumed - cut, 0)
        self._carry = data[cut:]
        self._context = 1

    def finish(self) -> dict[str, Finding]:
        for index, detector in enumerate(self._detectors):
            self._scan(index, detector, self._carry, len(self._carry))
        self._carry = ""
        return self._findings

    def _scan(self, index: int, detector: Detector, data: str, end: int) -> int:
        """Считает совпадения детектора, начавшиеся до `end`; возвращает позицию продолжения."""
        consumed = self._resume[index]
        for sample, match_end in detector.find(data, max(self._context, consumed), end):
            self._findings[detector.key].add(sample)
            consumed = match_end
        return consumed
