"""Скан байтов файла: извлечение текста и поиск, в одном синхронном вызове.

Вызывается через asyncio.to_thread: разбор файла не должен занимать цикл событий.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from barysguard.core.config import Settings
from barysguard.services.inspection.detectors import ContentScanner, Detector
from barysguard.services.inspection.extract import Deadline, ExtractFailure, Limits, extract


@dataclass(frozen=True)
class ScanOutcome:
    status: str
    truncated: bool
    # {rule_key: {"count": n, "samples": [...]}} — только ключи с ненулевым счётчиком.
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
        for part in stream:
            seen += len(part.strip())
            scanner.feed(part)
            deadline.check()
        if seen == 0:
            return ScanOutcome("no_text", False, {})
        found = scanner.finish()
    except ExtractFailure as failure:
        return ScanOutcome(failure.status, False, {})

    findings = {
        key: {"count": finding.count, "samples": finding.samples}
        for key, finding in found.items()
        if finding.count > 0
    }
    return ScanOutcome("ok", stream.truncated, findings)
