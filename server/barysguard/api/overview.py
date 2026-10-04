"""Сводка дашборда, сквозной список команд и журнал аудита."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from barysguard.api.deps import current_user, require_admin
from barysguard.api.schemas import (
    AuditEntry,
    AuditIntegrity,
    AuditPage,
    CommandCounts,
    CommandPage,
    FleetCommand,
    FleetCounts,
    Overview,
    VersionCount,
)
from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.certificate import AgentCertificate
from barysguard.db.models.command import Command, CommandStatus
from barysguard.db.models.enrollment import EnrollmentToken
from barysguard.db.models.user import User, UserRole
from barysguard.db.session import get_session
from barysguard.services.audit import verify_audit_chain
from barysguard.services.config import global_heartbeat_interval
from barysguard.services.presence import status_condition
from barysguard.services.scope import scope_group_ids

router = APIRouter(prefix="/api/v1", tags=["api"])

# Порог «скоро истекает» для сертификата агента. Продление начинается
# за 30 дней, поэтому две недели молчания означают, что агент не выходит
# на связь и продлиться уже не успеет.
CERTIFICATE_WARNING_DAYS = 14


async def _fleet_conditions(
    session: AsyncSession, user: User
) -> tuple[list[ColumnElement[bool]], set[uuid.UUID] | None]:
    visible = await scope_group_ids(session, user)
    if visible is None:
        return [], None
    return [Agent.group_id.in_(visible)], visible


@router.get("/overview", response_model=Overview)
async def read_overview(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Overview:
    """Одним запросом всё, что показывает дашборд.

    Именно одним: шесть отдельных запросов из браузера дали бы шесть
    проверок сессии и шесть обходов дерева групп ради одной страницы.
    """
    conditions, visible = await _fleet_conditions(session, user)
    interval = await global_heartbeat_interval(session)
    now = datetime.now(UTC)

    async def count_status(value: AgentStatus) -> int:
        return (
            await session.execute(
                select(func.count())
                .select_from(Agent)
                .where(*conditions, status_condition(value.value, interval, now))
            )
        ).scalar_one()

    total = (
        await session.execute(select(func.count()).select_from(Agent).where(*conditions))
    ).scalar_one()

    certificates_expiring = (
        await session.execute(
            select(func.count())
            .select_from(AgentCertificate)
            .join(Agent, Agent.id == AgentCertificate.agent_id)
            .where(
                *conditions,
                AgentCertificate.revoked_at.is_(None),
                AgentCertificate.not_after > now,
                AgentCertificate.not_after <= now + timedelta(days=CERTIFICATE_WARNING_DAYS),
            )
        )
    ).scalar_one()

    token_conditions: list[ColumnElement[bool]] = [
        EnrollmentToken.revoked_at.is_(None),
        EnrollmentToken.expires_at > now,
        EnrollmentToken.used_count < EnrollmentToken.max_uses,
    ]
    if visible is not None:
        token_conditions.append(EnrollmentToken.group_id.in_(visible))

    tokens_active = (
        await session.execute(
            select(func.count()).select_from(EnrollmentToken).where(*token_conditions)
        )
    ).scalar_one()

    queued = (
        await session.execute(
            select(func.count())
            .select_from(Command)
            .join(Agent, Agent.id == Command.agent_id)
            .where(*conditions, Command.status == CommandStatus.QUEUED)
        )
    ).scalar_one()

    failed = (
        await session.execute(
            select(func.count())
            .select_from(Command)
            .join(Agent, Agent.id == Command.agent_id)
            .where(
                *conditions,
                Command.status.in_((CommandStatus.FAILED, CommandStatus.EXPIRED)),
                Command.created_at > now - timedelta(hours=24),
            )
        )
    ).scalar_one()

    async def distribution(column: InstrumentedAttribute[str]) -> list[VersionCount]:
        rows = (
            await session.execute(
                select(column, func.count())
                .select_from(Agent)
                .where(*conditions)
                .group_by(column)
                .order_by(func.count().desc())
            )
        ).all()
        return [VersionCount(value=value, count=count) for value, count in rows]

    return Overview(
        agents=FleetCounts(
            total=total,
            active=await count_status(AgentStatus.ACTIVE),
            offline=await count_status(AgentStatus.OFFLINE),
            pending=await count_status(AgentStatus.PENDING),
            quarantined=await count_status(AgentStatus.QUARANTINED),
            revoked=await count_status(AgentStatus.REVOKED),
        ),
        certificates_expiring=certificates_expiring,
        tokens_active=tokens_active,
        commands=CommandCounts(queued=queued, failed_24h=failed),
        agent_versions=await distribution(Agent.agent_version),
        operating_systems=await distribution(Agent.os),
    )


@router.get("/commands", response_model=CommandPage)
async def list_fleet_commands(
    status_filter: str | None = Query(default=None, alias="status"),
    agent_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CommandPage:
    conditions, _ = await _fleet_conditions(session, user)

    if status_filter:
        conditions.append(Command.status == status_filter.upper())
    if agent_id is not None:
        conditions.append(Command.agent_id == agent_id)

    total = (
        await session.execute(
            select(func.count())
            .select_from(Command)
            .join(Agent, Agent.id == Command.agent_id)
            .where(*conditions)
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(Command, Agent.hostname)
            .join(Agent, Agent.id == Command.agent_id)
            .where(*conditions)
            .order_by(Command.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return CommandPage(
        items=[
            FleetCommand(
                id=command.id,
                agent_id=command.agent_id,
                hostname=hostname,
                type=command.type.value,
                status=command.status.value,
                payload=command.payload,
                result=command.result,
                created_at=command.created_at,
                sent_at=command.sent_at,
                completed_at=command.completed_at,
                expires_at=command.expires_at,
            )
            for command, hostname in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/audit", response_model=AuditPage)
async def read_audit(
    action: str | None = None,
    user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AuditPage:
    """Журнал действий операторов, от новых к старым.

    Администратор видит весь журнал, оператор — только свои записи:
    читать, кто именно из коллег открывал чей перехват, — это надзорное
    право, а не рядовое.
    """
    conditions: list[ColumnElement[bool]] = []

    if user.role is not UserRole.ADMIN:
        conditions.append(AuditLog.user_id == user.id)
    elif user_id is not None:
        conditions.append(AuditLog.user_id == user_id)

    if action:
        conditions.append(AuditLog.action == action)
    if since is not None:
        conditions.append(AuditLog.at >= since)
    if until is not None:
        conditions.append(AuditLog.at <= until)
    if cursor is not None:
        conditions.append(AuditLog.seq < cursor)

    rows = (
        (
            await session.execute(
                select(AuditLog, User.username)
                .outerjoin(User, AuditLog.user_id == User.id)
                .where(*conditions)
                .order_by(AuditLog.seq.desc())
                .limit(limit)
            )
        )
        .tuples()
        .all()
    )

    items = [
        AuditEntry(
            id=entry.id,
            seq=entry.seq,
            at=entry.at,
            user_id=entry.user_id,
            username=username,
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            payload=entry.payload,
        )
        for entry, username in rows
    ]

    # Курсор выдаётся только когда страница полна: иначе оператор увидит
    # кнопку «дальше», за которой ничего нет.
    next_cursor = items[-1].seq if len(items) == limit else None

    return AuditPage(items=items, next_cursor=next_cursor)


@router.get("/audit/verify", response_model=AuditIntegrity)
async def verify_audit(
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AuditIntegrity:
    broken = await verify_audit_chain(session)
    return AuditIntegrity(intact=broken is None, broken_seq=broken)


@router.get("/audit/{entry_id}", response_model=AuditEntry)
async def read_audit_entry(
    entry_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AuditEntry:
    entry = await session.get(AuditLog, entry_id)
    if entry is None or (user.role is not UserRole.ADMIN and entry.user_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audit entry not found")

    username = (
        await session.execute(select(User.username).where(User.id == entry.user_id))
    ).scalar_one_or_none()

    return AuditEntry(
        id=entry.id,
        seq=entry.seq,
        at=entry.at,
        user_id=entry.user_id,
        username=username,
        action=entry.action,
        target_type=entry.target_type,
        target_id=entry.target_id,
        payload=entry.payload,
    )
