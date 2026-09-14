import uuid
from datetime import datetime
from typing import Any

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
