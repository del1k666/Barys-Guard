"""Запись вердикта и привязка его к событию."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Verdict


async def write_verdict(
    session: AsyncSession,
    *,
    occurred_at: datetime,
    event_id: uuid.UUID,
    scan_id: uuid.UUID | None,
    status: str,
    reason: str | None,
    score: int,
    severity: str,
    matches: list[dict[str, Any]],
) -> Verdict:
    """Вердикт события. Задача удерживается одним воркером, поэтому гонки здесь нет."""
    verdict = Verdict(
        event_occurred_at=occurred_at,
        event_id=event_id,
        scan_id=scan_id,
        status=status,
        reason=reason,
        score=score,
        severity=severity,
        matches=matches,
    )
    session.add(verdict)
    await session.flush()
    await session.execute(
        update(Event)
        .where(Event.occurred_at == occurred_at, Event.event_id == event_id)
        .values(verdict_id=verdict.id)
    )
    return verdict
