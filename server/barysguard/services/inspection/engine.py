"""Скан байтов файла: извлечение текста и поиск, в одном синхронном вызове.

Вызывается через asyncio.to_thread: разбор файла не должен занимать цикл событий.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from barysguard.core.config import Settings
from barysguard.services.inspection.detectors import ContentScanner, Detector
from barysguard.services.inspection.extract import Deadline, ExtractFailure, Limits, extract

# Порции извлечения бывают крошечными (прогон docx, ячейка xlsx, перевод строки), а каждая
# подача сканеру — полный проход детекторов по хвосту в сотни символов. Поэтому порции
# копятся до этого размера и подаются сканеру крупными кусками: результат скана от
# разбиения текста не зависит.
FEED_SIZE = 32 * 1024


@dataclass(frozen=True)
class ScanOutcome:
    status: str
    truncated: bool
    # {rule_key: {"count": n, "samples": [...], "fragments": [...]}} —
    # только ключи с ненулевым счётчиком.
    findings: dict[str, dict[str, Any]]


def limits_from_settings(settings: Settings) -> Limits:
    return Limits(
        max_text=settings.inspect_max_text_bytes,
        max_unpacked=settings.inspect_max_unpacked_bytes,
        max_entries=settings.inspect_max_entries,
        max_ratio=settings.inspect_max_ratio,
        timeout=float(settings.inspect_timeout_seconds),
    )


def scan_bytes(
    name: str, data: bytes, detectors: Sequence[Detector], limits: Limits
) -> ScanOutcome:
    deadline = Deadline(limits.timeout)
    try:
        stream = extract(name, data, limits, deadline)
        scanner = ContentScanner(detectors)
        seen = 0
        pending: list[str] = []
        pending_size = 0
        for part in stream:
            seen += len(part.strip())
            pending.append(part)
            pending_size += len(part)
            if pending_size >= FEED_SIZE:
                scanner.feed("".join(pending))
                pending.clear()
                pending_size = 0
            deadline.check()
        if pending:
            scanner.feed("".join(pending))
        if seen == 0:
            return ScanOutcome("no_text", False, {})
        found = scanner.finish()
    except ExtractFailure as failure:
        return ScanOutcome(failure.status, False, {})
    except Exception:  # noqa: BLE001 - вход недоверенный: сбой разбора — свойство файла, не инфраструктуры
        return ScanOutcome("error", False, {})

    findings = {
        key: {"count": finding.count, "samples": finding.samples, "fragments": finding.fragments}
        for key, finding in found.items()
        if finding.count > 0
    }
    return ScanOutcome("ok", stream.truncated, findings)
