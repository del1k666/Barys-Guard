import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HostFacts(BaseModel):
    machine_id: str = Field(max_length=255)
    hostname: str = Field(max_length=255)
    os: str = Field(max_length=32)
    os_version: str = Field(max_length=128)
    arch: str = Field(max_length=32)
    agent_version: str = Field(max_length=32)


class EnrollRequest(BaseModel):
    token: str = Field(max_length=64)
    csr_pem: str = Field(max_length=8192)
    host: HostFacts


class EnrollResponse(BaseModel):
    agent_id: uuid.UUID
    certificate_pem: str
    ca_pem: str
    config_version: int
    heartbeat_interval_seconds: int


class RenewRequest(BaseModel):
    csr_pem: str = Field(max_length=8192)


class RenewResponse(BaseModel):
    certificate_pem: str
    ca_pem: str
    not_after: datetime


class AgentConfigResponse(BaseModel):
    version: int
    document: dict[str, Any]


class HeartbeatRequest(BaseModel):
    agent_version: str = Field(max_length=32)
    config_version: int = Field(ge=0)
    sent_at: datetime
    buffered_events: int = Field(default=0, ge=0)
    buffer_bytes: int = Field(default=0, ge=0)


class QueuedCommand(BaseModel):
    id: uuid.UUID
    type: str
    payload: dict[str, Any]
    expires_at: datetime


class HeartbeatResponse(BaseModel):
    server_time: datetime
    config_version: int
    heartbeat_interval_seconds: int
    commands: list[QueuedCommand]


class CommandResultRequest(BaseModel):
    status: Literal["done", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)


class RejectedLine(BaseModel):
    line: int
    reason: str


class EventsResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[RejectedLine]
