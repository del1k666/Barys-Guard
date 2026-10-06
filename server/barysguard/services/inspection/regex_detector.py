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
