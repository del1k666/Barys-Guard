"""Разбор пакета событий и запись в таблицу events."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.event import Event
from barysguard.gateway.event_schemas import MAX_EVENT_BYTES, SCHEMA_VERSION, EventEnvelope
from barysguard.services.event_subjects import subject_is_valid

# Параметров привязки у asyncpg не больше 32767: пакет в 10000 событий по 13
# колонок в один INSERT не поместится, поэтому вставка идёт порциями.
INSERT_CHUNK = 1000


class BatchUnreadableError(Exception):
    """Тело не разбирается как NDJSON: не UTF-8 или нет ни одной строки."""


class BatchTooLargeError(Exception):
    """В пакете больше событий, чем разрешает конфигурация агента."""


@dataclass(frozen=True)
class RejectedLine:
    line: int
    reason: str


@dataclass
class ParsedBatch:
    events: list[tuple[int, EventEnvelope]] = field(default_factory=list)
    rejected: list[RejectedLine] = field(default_factory=list)


def _is_unstorable(value: Any) -> bool:
    """Значение, которое PostgreSQL не сохранит.

    NUL запрещён в text и jsonb, а одиночный суррогат не кодируется в UTF-8.
    Такое событие роняло бы INSERT всего пакета (500), агент считал бы сбой
    временным и повторял тот же пакет вечно: очередь агента встала бы навсегда.
    """
    if isinstance(value, str):
        if "\x00" in value:
            return True
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(value, dict):
        return any(_is_unstorable(key) or _is_unstorable(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_is_unstorable(item) for item in value)
    return False


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

    if _is_unstorable(document):
        return None, "invalid_event"

    try:
        envelope = EventEnvelope.model_validate(document)
        # Крайние даты проходят валидацию, но не переводятся в UTC при записи.
        envelope.occurred_at.astimezone(UTC)
    except (ValidationError, OverflowError, ValueError):
        return None, "invalid_event"
    if not subject_is_valid(envelope.channel, envelope.action, envelope.subject):
        return None, "invalid_event"
    return envelope, None


def parse_batch(body: bytes, max_lines: int) -> ParsedBatch:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BatchUnreadableError("body is not UTF-8") from exc

    numbered = [
        (number, line.strip())
        for number, line in enumerate(text.split("\n"), start=1)
        if line.strip()
    ]
    if not numbered:
        raise BatchUnreadableError("empty batch")
    if len(numbered) > max_lines:
        raise BatchTooLargeError(f"{len(numbered)} events, limit {max_lines}")

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
