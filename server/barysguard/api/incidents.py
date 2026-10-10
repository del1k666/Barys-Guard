"""Инциденты DLP: чтение и смена статуса оператором."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import current_user
from barysguard.api.events import _decode_cursor, _encode_cursor  # общий формат курсора
from barysguard.api.schemas import (
    IncidentDetail,
    IncidentEventRef,
    IncidentMatch,
    IncidentPage,
    IncidentStatusUpdate,
    IncidentSummary,
    MatchFragment,
    VerdictSummary,
)
from barysguard.db.models.agent import Agent
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident, IncidentEvent, Verdict
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.scope import scope_group_ids

router = APIRouter(prefix="/api/v1", tags=["api"])


def _summary(incident: Incident, hostname: str) -> IncidentSummary:
    return IncidentSummary(
        id=incident.id,
        agent_id=incident.agent_id,
        hostname=hostname,
        artifact_sha256=incident.artifact_sha256,
        title=incident.title,
        severity=incident.severity,
        score=incident.score,
        status=incident.status,
        events_count=incident.events_count,
        first_event_at=incident.first_event_at,
        last_event_at=incident.last_event_at,
        assignee=incident.assignee,
    )


@router.get("/incidents", response_model=IncidentPage)
async def list_incidents(
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    agent_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentPage:
    """Инциденты от свежих к старым. Оператор видит только свою область."""
    conditions: list[ColumnElement[bool]] = []

    visible = await scope_group_ids(session, user)
    if visible is not None:
        conditions.append(Agent.group_id.in_(visible))

    if status_filter:
        conditions.append(Incident.status == status_filter)
    if severity:
        conditions.append(Incident.severity == severity)
    if agent_id is not None:
        conditions.append(Incident.agent_id == agent_id)
    if cursor:
        at, identifier = _decode_cursor(cursor)
        conditions.append(
            or_(
                Incident.last_event_at < at,
                and_(Incident.last_event_at == at, Incident.id < identifier),
            )
        )

    rows = (
        await session.execute(
            select(Incident, Agent.hostname)
            .join(Agent, Agent.id == Incident.agent_id)
            .where(*conditions)
            .order_by(Incident.last_event_at.desc(), Incident.id.desc())
            .limit(limit + 1)
        )
    ).all()

    page = rows[:limit]
    next_cursor = (
        _encode_cursor(page[-1][0].last_event_at, page[-1][0].id) if len(rows) > limit else None
    )
    return IncidentPage(
        items=[_summary(incident, hostname) for incident, hostname in page],
        next_cursor=next_cursor,
    )


async def _visible_incident(
    session: AsyncSession, user: User, incident_id: uuid.UUID, *, lock: bool = False
) -> tuple[Incident, str]:
    """Инцидент из области пользователя; `lock` — блокировка строки до конца транзакции."""
    conditions: list[ColumnElement[bool]] = [Incident.id == incident_id]
    visible = await scope_group_ids(session, user)
    if visible is not None:
        conditions.append(Agent.group_id.in_(visible))
    statement = (
        select(Incident, Agent.hostname)
        .join(Agent, Agent.id == Incident.agent_id)
        .where(*conditions)
    )
    if lock:
        statement = statement.with_for_update(of=Incident)
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return row[0], row[1]


def _linked(incident_id: uuid.UUID) -> ColumnElement[bool]:
    return and_(
        IncidentEvent.incident_id == incident_id,
        IncidentEvent.event_occurred_at == Event.occurred_at,
        IncidentEvent.event_id == Event.event_id,
    )


async def _detail(session: AsyncSession, incident: Incident, hostname: str) -> IncidentDetail:
    best = (
        await session.execute(
            select(Verdict)
            .join(
                IncidentEvent,
                and_(
                    IncidentEvent.event_occurred_at == Verdict.event_occurred_at,
                    IncidentEvent.event_id == Verdict.event_id,
                ),
            )
            .where(IncidentEvent.incident_id == incident.id)
            .order_by(Verdict.score.desc(), Verdict.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    events = (
        (
            await session.execute(
                select(Event)
                .join(IncidentEvent, _linked(incident.id))
                .order_by(Event.occurred_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return IncidentDetail(
        **_summary(incident, hostname).model_dump(),
        verdict=(
            VerdictSummary(status=best.status, score=best.score, severity=best.severity)
            if best
            else None
        ),
        matches=[
            IncidentMatch(
                rule_key=m["rule_key"],
                rule_title=m.get("rule_title", ""),
                count=m["count"],
                points=m["points"],
                weight=m.get("weight", 0),
                cap=m.get("cap", 0),
                samples=m.get("samples", []),
                fragments=[MatchFragment(**fragment) for fragment in m.get("fragments", [])],
            )
            for m in (best.matches if best else [])
        ],
        events=[
            IncidentEventRef(
                event_id=event.event_id,
                occurred_at=event.occurred_at,
                action=event.action,
                severity=event.severity,
                dst_path=(event.subject or {}).get("dst_path"),
            )
            for event in events
        ],
    )


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def read_incident(
    incident_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    incident, hostname = await _visible_incident(session, user, incident_id)
    return await _detail(session, incident, hostname)


@router.patch("/incidents/{incident_id}", response_model=IncidentDetail)
async def update_incident(
    incident_id: uuid.UUID,
    payload: IncidentStatusUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    # Блокировка строки: параллельное закрытие не будет перезаписано устаревшим статусом.
    incident, hostname = await _visible_incident(session, user, incident_id, lock=True)

    if incident.status != payload.status:
        if incident.status == "closed":
            raise HTTPException(status.HTTP_409_CONFLICT, "incident is closed")
        previous = incident.status
        incident.status = payload.status
        if payload.status == "closed":
            incident.closed_at = datetime.now(UTC)
        await session.flush()
        await record_audit(
            session,
            user_id=user.id,
            action="incident.update",
            target_type="incident",
            target_id=incident.id,
            payload={"from": previous, "to": payload.status},
        )

    return await _detail(session, incident, hostname)
