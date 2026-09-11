import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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
