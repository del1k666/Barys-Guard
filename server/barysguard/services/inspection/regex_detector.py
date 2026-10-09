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

    def skip_rest(self, data: str, pos: int) -> int:
        """Конец продолжения поглощённого длинного совпадения, оборванного на `pos`.

        Вызывается сканером только если прошлый участок кончился ровно на конце данных.
        Хвост сканера хранит не меньше max_match + 2 символов до `pos`, поэтому начало
        совпадения ищется среди них: первая позиция, с которой совпадение заходит за `pos`.
        Точно, если уже эти символы удовлетворяют шаблону (его минимальная длина не больше
        хранимого хвоста); иначе остаток считается по прежнему правилу и не продолжается.
        """
        for begin in range(max(pos - self._max_match - 2, 0), pos):
            earlier = self._pattern.match(data, begin)
            if earlier is not None and earlier.end() > pos:
                return int(earlier.end())
        return pos

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int]]:
        for match in self._pattern.finditer(data, start):
            if match.start() >= end:
                return
            length = match.end() - match.start()
            if length == 0:
                continue
            # Слишком длинное совпадение не засчитывается (перекрытие на стыке порций
            # рассчитано на max_match), но поглощается: иначе его остаток после среза
            # порции или хвоста засчитался бы как короткое совпадение.
            if length > self._max_match:
                yield "", match.end()
                continue
            yield mask_tail(match.group()), match.end()
