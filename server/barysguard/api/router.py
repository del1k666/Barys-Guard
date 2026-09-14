import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import agent_in_scope, current_user, require_admin
from barysguard.api.schemas import (
    AgentSummary,
    CommandResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    CreateCommandRequest,
    CreateEnrollmentTokenRequest,
    EffectiveConfigResponse,
    EnrollmentTokenResponse,
)
from barysguard.core.config import Settings, get_settings
from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.command import Command
from barysguard.db.models.config import AgentConfig, ConfigScope
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.commands import queue_command
from barysguard.services.config import (
    AgentConfigDocument,
    compute_config_version,
    effective_config_for_agent,
    global_heartbeat_interval,
)
from barysguard.services.enrollment import create_enrollment_token
from barysguard.services.presence import derive_status

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


@router.get("/agents", response_model=list[AgentSummary])
async def list_agents(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentSummary]:
    statement = select(Agent).order_by(Agent.hostname)
    if user.scope_group_id is not None:
        statement = statement.where(Agent.group_id == user.scope_group_id)

    # Интервал берётся из глобальной конфигурации: собирать эффективный
    # документ на каждую строку списка значило бы обходить дерево групп
    # тысячи раз ради сдвига границы на десятки секунд.
    interval = await global_heartbeat_interval(session)
    now = datetime.now(UTC)

    agents = (await session.execute(statement)).scalars().all()
    return [
        AgentSummary(
            id=agent.id,
            hostname=agent.hostname,
            os=agent.os,
            agent_version=agent.agent_version,
            status=derive_status(agent, interval, now),
            last_heartbeat_at=agent.last_heartbeat_at,
        )
        for agent in agents
    ]


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
