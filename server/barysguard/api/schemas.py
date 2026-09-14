import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from barysguard.services.config import AgentConfigDocument


class CreateEnrollmentTokenRequest(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=10_000)
    ttl_hours: int | None = Field(default=None, ge=1, le=8760)
    group_id: uuid.UUID | None = None


class EnrollmentTokenResponse(BaseModel):
    token: str
    expires_at: datetime
    max_uses: int


class AgentSummary(BaseModel):
    id: uuid.UUID
    hostname: str
    os: str
    agent_version: str
    status: str
    last_heartbeat_at: datetime | None


class ConfigUpdateRequest(BaseModel):
    document: AgentConfigDocument


class ConfigResponse(BaseModel):
    scope: str
    group_id: uuid.UUID | None
    document: dict[str, Any]
    version: int
    updated_at: datetime | None


class EffectiveConfigResponse(BaseModel):
    agent_id: uuid.UUID
    document: dict[str, Any]
    version: int
    # То, что агент сообщил в последнем heartbeat. Расхождение с version
    # показывает оператору, кто отстал.
    applied_version: int
