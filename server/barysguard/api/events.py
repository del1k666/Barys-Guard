"""Чтение событий оператором."""

import base64
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import current_user
from barysguard.api.schemas import EventPage, EventSummary, VerdictSummary
from barysguard.db.models.agent import Agent
from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Verdict
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.scope import scope_group_ids

router = APIRouter(prefix="/api/v1", tags=["api"])


def _aware(value: datetime) -> datetime:
    # Время без пояса из строки запроса база не примет: считаем его UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _encode_cursor(occurred_at: datetime, event_id: uuid.UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{event_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        stamp, identifier = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(stamp), uuid.UUID(identifier)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from None


@router.get("/events", response_model=EventPage)
async def list_events(
    agent_id: uuid.UUID | None = None,
    channel: str | None = None,
    action: str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EventPage:
    """События от новых к старым. Оператор видит только свою область."""
    conditions: list[ColumnElement[bool]] = []

    visible = await scope_group_ids(session, user)
    if visible is not None:
        conditions.append(Agent.group_id.in_(visible))

    if agent_id is not None:
        conditions.append(Event.agent_id == agent_id)
    if channel:
        conditions.append(Event.channel == channel)
    if action:
        conditions.append(Event.action == action)
    if severity:
        conditions.append(Event.severity == severity)
    if since is not None:
        conditions.append(Event.occurred_at >= _aware(since))
    if until is not None:
        conditions.append(Event.occurred_at <= _aware(until))
    if cursor:
        at, identifier = _decode_cursor(cursor)
        # Составной курсор: строго «старше» по (occurred_at, event_id).
        conditions.append(
            or_(
                Event.occurred_at < at,
                and_(Event.occurred_at == at, Event.event_id < identifier),
            )
        )

    uploaded = (
        exists(select(Artifact.id).where(Artifact.sha256 == Event.artifact_sha256))
        .correlate(Event)
        .label("artifact_uploaded")
    )
    rows = (
        await session.execute(
            select(Event, Agent.hostname, uploaded, Verdict.status, Verdict.score, Verdict.severity)
            .join(Agent, Agent.id == Event.agent_id)
            .outerjoin(Verdict, Verdict.id == Event.verdict_id)
            .where(*conditions)
            .order_by(Event.occurred_at.desc(), Event.event_id.desc())
            .limit(limit + 1)
        )
    ).all()

    page = rows[:limit]
    # Курсор выдаётся только когда за страницей есть ещё строки: иначе
    # оператор увидит «дальше», за которым ничего нет.
    next_cursor = (
        _encode_cursor(page[-1][0].occurred_at, page[-1][0].event_id) if len(rows) > limit else None
    )

    return EventPage(
        items=[
            EventSummary(
                event_id=event.event_id,
                agent_id=event.agent_id,
                hostname=hostname,
                occurred_at=event.occurred_at,
                received_at=event.received_at,
                channel=event.channel,
                action=event.action,
                severity=event.severity,
                actor=event.actor,
                process=event.process,
                subject=event.subject,
                labels=event.labels,
                artifact_sha256=event.artifact_sha256,
                artifact_uploaded=bool(is_uploaded),
                verdict=(
                    VerdictSummary(status=v_status, score=v_score, severity=v_severity)
                    if v_status is not None
                    else None
                ),
            )
            for event, hostname, is_uploaded, v_status, v_score, v_severity in page
        ],
        next_cursor=next_cursor,
    )
