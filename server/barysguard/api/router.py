import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import agent_in_scope, current_user, require_admin
from barysguard.api.schemas import (
    AgentDetail,
    AgentPage,
    AgentSummary,
    CertificateSummary,
    CommandResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    CreateCommandRequest,
    CreateEnrollmentTokenRequest,
    EffectiveConfigResponse,
    EnrollmentTokenResponse,
    EnrollmentTokenSummary,
    RevokeAgentRequest,
    UpdateAgentRequest,
)
from barysguard.core.config import Settings, get_settings
from barysguard.db.models.agent import Agent, AgentGroup, AgentStatus
from barysguard.db.models.certificate import AgentCertificate
from barysguard.db.models.command import Command
from barysguard.db.models.config import AgentConfig, ConfigScope
from barysguard.db.models.enrollment import EnrollmentToken
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.pki.service import revoke_certificate
from barysguard.services.audit import record_audit
from barysguard.services.commands import queue_command
from barysguard.services.config import (
    AgentConfigDocument,
    compute_config_version,
    effective_config_for_agent,
    global_heartbeat_interval,
)
from barysguard.services.enrollment import create_enrollment_token
from barysguard.services.presence import derive_status, status_condition
from barysguard.services.scope import scope_group_ids

router = APIRouter(prefix="/api/v1", tags=["api"])


@router.post(
    "/enrollment-tokens",
    response_model=EnrollmentTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    payload: CreateEnrollmentTokenRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> EnrollmentTokenResponse:
    ttl_hours = payload.ttl_hours or settings.enrollment_token_ttl_hours
    raw, record = await create_enrollment_token(
        session,
        created_by=user.id,
        group_id=payload.group_id,
        ttl_hours=ttl_hours,
        max_uses=payload.max_uses,
    )

    await record_audit(
        session,
        user_id=user.id,
        action="enrollment_token.create",
        target_type="enrollment_token",
        target_id=record.id,
        payload={"max_uses": payload.max_uses, "ttl_hours": ttl_hours},
    )

    # Открытая форма токена возвращается единственный раз.
    return EnrollmentTokenResponse(
        token=raw, expires_at=record.expires_at, max_uses=record.max_uses
    )


def _token_state(record: EnrollmentToken, now: datetime) -> str:
    """Состояние токена, выведенное из его же полей.

    Не хранится колонкой: истечение наступает само собой, а хранимое
    значение пришлось бы обновлять по расписанию и однажды разойтись
    с действительностью.
    """
    if record.revoked_at is not None:
        return "revoked"
    if record.expires_at <= now:
        return "expired"
    if record.used_count >= record.max_uses:
        return "exhausted"
    return "active"


@router.get("/enrollment-tokens", response_model=list[EnrollmentTokenSummary])
async def list_tokens(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[EnrollmentTokenSummary]:
    statement = (
        select(EnrollmentToken, AgentGroup.name, User.username)
        .outerjoin(AgentGroup, EnrollmentToken.group_id == AgentGroup.id)
        .outerjoin(User, EnrollmentToken.created_by == User.id)
        .order_by(EnrollmentToken.created_at.desc())
    )

    visible = await scope_group_ids(session, user)
    if visible is not None:
        # Токен без группы регистрирует агента вне всяких групп, то есть
        # вне области видимости офицера филиала. Показывать его ему незачем.
        statement = statement.where(EnrollmentToken.group_id.in_(visible))

    now = datetime.now(UTC)
    rows = (await session.execute(statement)).all()

    return [
        EnrollmentTokenSummary(
            id=record.id,
            group_id=record.group_id,
            group_name=group_name,
            created_by=record.created_by,
            created_by_username=username,
            created_at=record.created_at,
            expires_at=record.expires_at,
            max_uses=record.max_uses,
            used_count=record.used_count,
            revoked_at=record.revoked_at,
            state=_token_state(record, now),
        )
        for record, group_name, username in rows
    ]


@router.post("/enrollment-tokens/{token_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    record = await session.get(EnrollmentToken, token_id)

    visible = await scope_group_ids(session, user)
    out_of_scope = visible is not None and (
        record is not None and (record.group_id is None or record.group_id not in visible)
    )
    if record is None or out_of_scope:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")

    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await session.flush()

        await record_audit(
            session,
            user_id=user.id,
            action="enrollment_token.revoke",
            target_type="enrollment_token",
            target_id=record.id,
            payload={},
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _summary(agent: Agent, group_name: str | None, status_value: str) -> AgentSummary:
    return AgentSummary(
        id=agent.id,
        hostname=agent.hostname,
        os=agent.os,
        os_version=agent.os_version,
        arch=agent.arch,
        agent_version=agent.agent_version,
        status=status_value,
        group_id=agent.group_id,
        group_name=group_name,
        last_heartbeat_at=agent.last_heartbeat_at,
        # asyncpg отдаёт INET объектом ipaddress, а не строкой.
        last_ip=str(agent.last_ip) if agent.last_ip is not None else None,
        clock_skew_ms=agent.clock_skew_ms,
        config_version=agent.config_version,
        enrolled_at=agent.enrolled_at,
    )


@router.get("/agents", response_model=AgentPage)
async def list_agents(
    q: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    group_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentPage:
    # Интервал берётся из глобальной конфигурации: собирать эффективный
    # документ на каждую строку списка значило бы обходить дерево групп
    # тысячи раз ради сдвига границы на десятки секунд.
    interval = await global_heartbeat_interval(session)
    now = datetime.now(UTC)

    conditions: list[ColumnElement[bool]] = []

    visible = await scope_group_ids(session, user)
    if visible is not None:
        # Пустая область видимости обязана давать пустой список, а не весь
        # флот: IN () в SQL как раз и даёт ложь для любой строки.
        conditions.append(Agent.group_id.in_(visible))

    if q:
        conditions.append(Agent.hostname.ilike(f"%{q}%"))

    if group_id is not None:
        conditions.append(Agent.group_id == group_id)

    if status_filter:
        conditions.append(status_condition(status_filter, interval, now))

    total = (
        await session.execute(select(func.count()).select_from(Agent).where(*conditions))
    ).scalar_one()

    rows = (
        await session.execute(
            select(Agent, AgentGroup.name)
            .outerjoin(AgentGroup, Agent.group_id == AgentGroup.id)
            .where(*conditions)
            .order_by(Agent.hostname)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return AgentPage(
        items=[
            _summary(agent, group_name, derive_status(agent, interval, now))
            for agent, group_name in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _agent_detail(session: AsyncSession, agent: Agent) -> AgentDetail:
    group_name = None
    if agent.group_id is not None:
        group_name = (
            await session.execute(select(AgentGroup.name).where(AgentGroup.id == agent.group_id))
        ).scalar_one_or_none()

    interval = await global_heartbeat_interval(session)
    summary = _summary(agent, group_name, derive_status(agent, interval, datetime.now(UTC)))

    # Действующий сертификат — тот, что не отозван и ещё не истёк. Их
    # не может быть двух: продление помечает прежний заменённым.
    now = datetime.now(UTC)
    certificate = (
        await session.execute(
            select(AgentCertificate)
            .where(
                AgentCertificate.agent_id == agent.id,
                AgentCertificate.revoked_at.is_(None),
                AgentCertificate.not_after > now,
            )
            .order_by(AgentCertificate.issued_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return AgentDetail(
        **summary.model_dump(),
        machine_id=agent.machine_id,
        tags=agent.tags,
        certificate=_certificate_summary(certificate) if certificate else None,
    )


def _certificate_summary(certificate: AgentCertificate) -> CertificateSummary:
    return CertificateSummary(
        id=certificate.id,
        serial=certificate.serial,
        fingerprint_sha256=certificate.fingerprint_sha256,
        not_before=certificate.not_before,
        not_after=certificate.not_after,
        issued_at=certificate.issued_at,
        revoked_at=certificate.revoked_at,
        revocation_reason=certificate.revocation_reason,
    )


@router.get("/agents/{agent_id}", response_model=AgentDetail)
async def read_agent(
    agent_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentDetail:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    return await _agent_detail(session, agent)


@router.patch("/agents/{agent_id}", response_model=AgentDetail)
async def update_agent(
    agent_id: uuid.UUID,
    payload: UpdateAgentRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentDetail:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    fields = payload.model_fields_set
    changes: dict[str, Any] = {}

    if "group_id" in fields:
        visible = await scope_group_ids(session, user)
        if payload.group_id is not None:
            target = (
                await session.execute(select(AgentGroup).where(AgentGroup.id == payload.group_id))
            ).scalar_one_or_none()
            if target is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")

        # Перенос за пределы своей области видимости — это передача хоста
        # вместе со всем его будущим перехватом в чужие руки. Целевая группа
        # обязана быть видимой тому, кто переносит.
        if visible is not None and (payload.group_id is None or payload.group_id not in visible):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")

        changes["group_id"] = str(payload.group_id) if payload.group_id else None
        agent.group_id = payload.group_id

    if "tags" in fields and payload.tags is not None:
        changes["tags"] = payload.tags
        agent.tags = payload.tags

    await session.flush()

    if changes:
        await record_audit(
            session,
            user_id=user.id,
            action="agent.update",
            target_type="agent",
            target_id=agent.id,
            payload=changes,
        )

    return await _agent_detail(session, agent)


@router.get("/agents/{agent_id}/certificates", response_model=list[CertificateSummary])
async def list_agent_certificates(
    agent_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CertificateSummary]:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    rows = (
        (
            await session.execute(
                select(AgentCertificate)
                .where(AgentCertificate.agent_id == agent.id)
                .order_by(AgentCertificate.issued_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_certificate_summary(row) for row in rows]


@router.post("/agents/{agent_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent(
    agent_id: uuid.UUID,
    payload: RevokeAgentRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Отзывает все действующие сертификаты агента и снимает его с обслуживания.

    Отзыв доступен только администратору: это действие мгновенно выводит
    хост из-под наблюдения, и оно же — способ скрыть следы.
    """
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    now = datetime.now(UTC)
    active = (
        (
            await session.execute(
                select(AgentCertificate).where(
                    AgentCertificate.agent_id == agent.id,
                    AgentCertificate.revoked_at.is_(None),
                    AgentCertificate.not_after > now,
                )
            )
        )
        .scalars()
        .all()
    )

    for certificate in active:
        await revoke_certificate(session, certificate.serial, payload.reason)

    agent.status = AgentStatus.REVOKED
    await session.flush()

    await record_audit(
        session,
        user_id=user.id,
        action="agent.revoke",
        target_type="agent",
        target_id=agent.id,
        payload={"reason": payload.reason, "serials": [row.serial for row in active]},
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _load_config_row(
    session: AsyncSession, scope: ConfigScope, group_id: uuid.UUID | None
) -> AgentConfig | None:
    statement = select(AgentConfig).where(AgentConfig.scope == scope)
    if group_id is not None:
        statement = statement.where(AgentConfig.group_id == group_id)
    return (await session.execute(statement)).scalar_one_or_none()


async def _store_config(
    session: AsyncSession,
    *,
    scope: ConfigScope,
    group_id: uuid.UUID | None,
    document: dict[str, Any],
    user: User,
) -> AgentConfig:
    row = await _load_config_row(session, scope, group_id)
    if row is None:
        row = AgentConfig(scope=scope, group_id=group_id)
        session.add(row)
    row.document = document
    row.updated_by = user.id
    await session.flush()

    # После UPDATE с onupdate=func.now() значение вычислено сервером БД,
    # и атрибут помечен просроченным. Обычное чтение попыталось бы сходить
    # в базу синхронно и упало бы с MissingGreenlet: обновляем явно.
    await session.refresh(row, ["updated_at"])

    await record_audit(
        session,
        user_id=user.id,
        action="config.update",
        target_type="agent_config",
        target_id=row.id,
        payload={"scope": scope.value, "group_id": str(group_id) if group_id else None},
    )
    return row


@router.get("/config", response_model=ConfigResponse)
async def read_global_config(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    row = await _load_config_row(session, ConfigScope.GLOBAL, None)
    # На пустой базе отдаём значения по умолчанию, а не 404: конфигурация
    # существует всегда, строка в таблице — лишь её переопределение.
    document = row.document if row else AgentConfigDocument().model_dump(mode="json")
    return ConfigResponse(
        scope=ConfigScope.GLOBAL.value,
        group_id=None,
        document=document,
        version=compute_config_version(document),
        updated_at=row.updated_at if row else None,
    )


@router.put("/config", response_model=ConfigResponse)
async def replace_global_config(
    payload: ConfigUpdateRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    document = payload.document.model_dump(mode="json")
    row = await _store_config(
        session, scope=ConfigScope.GLOBAL, group_id=None, document=document, user=user
    )
    return ConfigResponse(
        scope=ConfigScope.GLOBAL.value,
        group_id=None,
        document=document,
        version=compute_config_version(document),
        updated_at=row.updated_at,
    )


@router.get("/groups/{group_id}/config", response_model=ConfigResponse)
async def read_group_config(
    group_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    row = await _load_config_row(session, ConfigScope.GROUP, group_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group has no config override")
    return ConfigResponse(
        scope=ConfigScope.GROUP.value,
        group_id=group_id,
        document=row.document,
        version=compute_config_version(row.document),
        updated_at=row.updated_at,
    )


@router.put("/groups/{group_id}/config", response_model=ConfigResponse)
async def replace_group_config(
    group_id: uuid.UUID,
    payload: ConfigUpdateRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    group = (
        await session.execute(select(AgentGroup).where(AgentGroup.id == group_id))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")

    document = payload.document.model_dump(mode="json")
    row = await _store_config(
        session, scope=ConfigScope.GROUP, group_id=group_id, document=document, user=user
    )
    return ConfigResponse(
        scope=ConfigScope.GROUP.value,
        group_id=group_id,
        document=document,
        version=compute_config_version(document),
        updated_at=row.updated_at,
    )


@router.delete("/groups/{group_id}/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group_config(
    group_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await _load_config_row(session, ConfigScope.GROUP, group_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group has no config override")

    row_id = row.id
    await session.delete(row)
    await record_audit(
        session,
        user_id=user.id,
        action="config.delete",
        target_type="agent_config",
        target_id=row_id,
        payload={"scope": ConfigScope.GROUP.value, "group_id": str(group_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents/{agent_id}/config", response_model=EffectiveConfigResponse)
async def read_effective_agent_config(
    agent_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EffectiveConfigResponse:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    document, version = await effective_config_for_agent(session, agent)
    return EffectiveConfigResponse(
        agent_id=agent.id,
        document=document,
        version=version,
        applied_version=agent.config_version,
    )


def _command_response(command: Command) -> CommandResponse:
    return CommandResponse(
        id=command.id,
        type=command.type.value,
        status=command.status.value,
        payload=command.payload,
        result=command.result,
        created_at=command.created_at,
        sent_at=command.sent_at,
        completed_at=command.completed_at,
        expires_at=command.expires_at,
    )


@router.post(
    "/agents/{agent_id}/commands",
    response_model=CommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_command(
    agent_id: uuid.UUID,
    payload: CreateCommandRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CommandResponse:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=payload.type,
        payload=payload.payload,
        created_by=user.id,
        ttl_seconds=payload.ttl_seconds,
    )

    await record_audit(
        session,
        user_id=user.id,
        action="command.create",
        target_type="command",
        target_id=command.id,
        payload={"agent_id": str(agent.id), "type": payload.type.value},
    )
    return _command_response(command)


@router.get("/agents/{agent_id}/commands", response_model=list[CommandResponse])
async def list_commands(
    agent_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CommandResponse]:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    commands = (
        (
            await session.execute(
                select(Command)
                .where(Command.agent_id == agent.id)
                .order_by(Command.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [_command_response(command) for command in commands]
