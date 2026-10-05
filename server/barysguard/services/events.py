"""Разбор пакета событий и запись в таблицу events."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.event import Event
from barysguard.gateway.event_schemas import MAX_EVENT_BYTES, SCHEMA_VERSION, EventEnvelope

# Параметров привязки у asyncpg не больше 32767: пакет в 10000 событий по 13
# колонок в один INSERT не поместится, поэтому вставка идёт порциями.
INSERT_CHUNK = 1000


class BatchUnreadable(Exception):
    """Тело не разбирается как NDJSON: не UTF-8 или нет ни одной строки."""


class BatchTooLarge(Exception):
    """В пакете больше событий, чем разрешает конфигурация агента."""


@dataclass(frozen=True)
class RejectedLine:
    line: int
    reason: str


@dataclass
class ParsedBatch:
    events: list[tuple[int, EventEnvelope]] = field(default_factory=list)
    rejected: list[RejectedLine] = field(default_factory=list)


def _parse_line(raw: str) -> tuple[EventEnvelope | None, str | None]:
    if len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
        return None, "too_large"

    try:
        document: Any = json.loads(raw)
    except ValueError:
        return None, "invalid_json"

    if not isinstance(document, dict):
        return None, "not_an_object"

    # Версия проверяется до полной валидации: событие будущей схемы нельзя
    # разбирать по правилам схемы 1, а причина отказа должна быть понятной.
    if document.get("schema_version") not in (None, SCHEMA_VERSION):
        return None, "unsupported_schema"

    try:
        return EventEnvelope.model_validate(document), None
    except ValidationError:
        return None, "invalid_event"


def parse_batch(body: bytes, max_lines: int) -> ParsedBatch:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BatchUnreadable("body is not UTF-8") from exc

    numbered = [
        (number, line.strip())
        for number, line in enumerate(text.split("\n"), start=1)
        if line.strip()
    ]
    if not numbered:
        raise BatchUnreadable("empty batch")
    if len(numbered) > max_lines:
        raise BatchTooLarge(f"{len(numbered)} events, limit {max_lines}")

    parsed = ParsedBatch()
    for number, raw in numbered:
        envelope, reason = _parse_line(raw)
        if envelope is None:
            parsed.rejected.append(RejectedLine(number, reason or "invalid_event"))
        else:
            parsed.events.append((number, envelope))
    return parsed


async def store_events(
    session: AsyncSession,
    agent_id: uuid.UUID,
    envelopes: list[EventEnvelope],
    received_at: datetime,
) -> int:
    """Вставляет события идемпотентно. Возвращает число реально вставленных строк."""
    rows = [
        {
            "event_id": envelope.event_id,
            "agent_id": agent_id,
            "schema_version": envelope.schema_version,
            "channel": envelope.channel.value,
            "action": envelope.action,
            "occurred_at": envelope.occurred_at,
            "received_at": received_at,
            "actor": envelope.actor,
            "process": envelope.process,
            "subject": envelope.subject,
            "labels": envelope.labels,
            "artifact_sha256": envelope.artifact.sha256 if envelope.artifact else None,
            "severity": envelope.severity_hint.value,
        }
        for envelope in envelopes
    ]

    inserted = 0
    for start in range(0, len(rows), INSERT_CHUNK):
        statement = (
            pg_insert(Event)
            .values(rows[start : start + INSERT_CHUNK])
            .on_conflict_do_nothing(index_elements=["occurred_at", "event_id"])
            .returning(Event.event_id)
        )
        inserted += len((await session.execute(statement)).all())
    return inserted
