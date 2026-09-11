from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import current_user
from barysguard.api.schemas import (
    AgentSummary,
    CreateEnrollmentTokenRequest,
    EnrollmentTokenResponse,
)
from barysguard.core.config import Settings, get_settings
from barysguard.db.models.agent import Agent
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.enrollment import create_enrollment_token

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

    agents = (await session.execute(statement)).scalars().all()
    return [
        AgentSummary(
            id=agent.id,
            hostname=agent.hostname,
            os=agent.os,
            agent_version=agent.agent_version,
            status=agent.status.value,
            last_heartbeat_at=agent.last_heartbeat_at,
        )
        for agent in agents
    ]
