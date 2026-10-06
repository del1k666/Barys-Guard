"""Очередь инспекции в PostgreSQL: постановка и захват через SKIP LOCKED."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Select, Uuid, and_, column, delete, or_, select, update, values
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import EventQueue

_COLUMNS = ["event_occurred_at", "event_id", "artifact_sha256"]
_BATCH = 500


@dataclass(frozen=True)
class QueueTask:
    id: int
    occurred_at: datetime
    event_id: uuid.UUID
    artifact_sha256: str
    attempts: int


async def _enqueue(session: AsyncSession, source: Select[Any]) -> int:
    statement = (
        pg_insert(EventQueue)
        .from_select(_COLUMNS, source)
        .on_conflict_do_nothing(index_elements=["event_occurred_at", "event_id"])
    )
    result = cast("CursorResult[Any]", await session.execute(statement))
    return int(result.rowcount or 0)


async def enqueue_for_artifact(session: AsyncSession, sha256: str) -> int:
    """Ставит события артефакта, у которых ещё нет вердикта (вызывается при завершении загрузки)."""
    return await _enqueue(
        session,
        select(Event.occurred_at, Event.event_id, Event.artifact_sha256).where(
            Event.artifact_sha256 == sha256, Event.verdict_id.is_(None)
        ),
    )


async def enqueue_new_events(
    session: AsyncSession, keys: Sequence[tuple[datetime, uuid.UUID]]
) -> int:
    """Ставит только что принятые события, чей артефакт уже загружен."""
    total = 0
    for start in range(0, len(keys), _BATCH):
        part = keys[start : start + _BATCH]
        wanted = values(
            column("occurred_at", SADateTime(timezone=True)),
            column("event_id", Uuid()),
            name="wanted",
        ).data(list(part))
        total += await _enqueue(
            session,
            select(Event.occurred_at, Event.event_id, Event.artifact_sha256)
            .join(
                wanted,
                and_(
                    Event.occurred_at == wanted.c.occurred_at, Event.event_id == wanted.c.event_id
                ),
            )
            .join(Artifact, Artifact.sha256 == Event.artifact_sha256)
            .where(Event.verdict_id.is_(None)),
        )
    return total


async def enqueue_missed(
    session: AsyncSession, now: datetime, window: timedelta = timedelta(hours=24)
) -> int:
    """Страховка от гонки вокруг завершения загрузки: свежие артефакты и их события."""
    recent = select(Artifact.sha256).where(Artifact.first_seen_at >= now - window)
    return await _enqueue(
        session,
        select(Event.occurred_at, Event.event_id, Event.artifact_sha256).where(
            Event.artifact_sha256.in_(recent), Event.verdict_id.is_(None)
        ),
    )


async def claim_batch(
    session: AsyncSession, *, limit: int, lock_seconds: int, now: datetime
) -> list[QueueTask]:
    """Забирает готовые задачи и блокирует их; параллельные воркеры не пересекаются."""
    ready = (
        select(EventQueue.id)
        .where(
            EventQueue.state == "pending",
            or_(EventQueue.locked_until.is_(None), EventQueue.locked_until < now),
        )
        .order_by(EventQueue.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    statement = (
        update(EventQueue)
        .where(EventQueue.id.in_(ready.scalar_subquery()))
        .values(
            attempts=EventQueue.attempts + 1,
            locked_until=now + timedelta(seconds=lock_seconds),
        )
        .returning(
            EventQueue.id,
            EventQueue.event_occurred_at,
            EventQueue.event_id,
            EventQueue.artifact_sha256,
            EventQueue.attempts,
        )
    )
    rows = (await session.execute(statement)).all()
    return [QueueTask(*row) for row in rows]


async def fail_exhausted(
    session: AsyncSession, *, max_attempts: int, now: datetime
) -> list[QueueTask]:
    """Задачи, исчерпавшие попытки и не удерживаемые воркером, получают состояние failed."""
    statement = (
        update(EventQueue)
        .where(
            EventQueue.state == "pending",
            EventQueue.attempts >= max_attempts,
            EventQueue.locked_until < now,
        )
        .values(state="failed")
        .returning(
            EventQueue.id,
            EventQueue.event_occurred_at,
            EventQueue.event_id,
            EventQueue.artifact_sha256,
            EventQueue.attempts,
        )
    )
    rows = (await session.execute(statement)).all()
    return [QueueTask(*row) for row in rows]


async def finish_task(session: AsyncSession, task_id: int) -> None:
    await session.execute(delete(EventQueue).where(EventQueue.id == task_id))
