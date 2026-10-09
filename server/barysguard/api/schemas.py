import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from barysguard.db.models.command import CommandType
from barysguard.db.models.user import User
from barysguard.services.config import AgentConfigDocument


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)

    # Двенадцать символов — не украшение: пароль оператора DLP защищает
    # доступ ко всему перехвату организации, и восьмизначный перебирается
    # на одной видеокарте за часы.
    new_password: str = Field(min_length=12, max_length=1024)


class SessionSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


class SessionUser(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    scope_group_id: uuid.UUID | None
    must_change_password: bool

    @classmethod
    def from_user(cls, user: User) -> "SessionUser":
        return cls(
            id=user.id,
            username=user.username,
            role=user.role.value,
            scope_group_id=user.scope_group_id,
            must_change_password=user.must_change_password,
        )


class CreateEnrollmentTokenRequest(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=10_000)
    ttl_hours: int | None = Field(default=None, ge=1, le=8760)
    group_id: uuid.UUID | None = None


class EnrollmentTokenResponse(BaseModel):
    token: str
    expires_at: datetime
    max_uses: int


class UserSummary(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    scope_group_id: uuid.UUID | None
    is_active: bool
    must_change_password: bool
    has_password: bool
    has_api_key: bool
    last_login_at: datetime | None
    locked_until: datetime | None
    created_at: datetime


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    role: Literal["admin", "operator"] = "operator"
    scope_group_id: uuid.UUID | None = None


class UpdateUserRequest(BaseModel):
    role: Literal["admin", "operator"] | None = None
    scope_group_id: uuid.UUID | None = None
    is_active: bool | None = None


class IssuedPasswordResponse(BaseModel):
    user: UserSummary
    password: str
    must_change_password: bool


class ApiKeyResponse(BaseModel):
    user: UserSummary
    api_key: str


class EnrollmentTokenSummary(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID | None
    group_name: str | None
    created_by: uuid.UUID | None
    created_by_username: str | None
    created_at: datetime
    expires_at: datetime
    max_uses: int
    used_count: int
    revoked_at: datetime | None
    state: str


class AgentSummary(BaseModel):
    id: uuid.UUID
    hostname: str
    os: str
    os_version: str
    arch: str
    agent_version: str
    status: str
    group_id: uuid.UUID | None
    group_name: str | None
    last_heartbeat_at: datetime | None
    last_ip: str | None
    clock_skew_ms: int
    config_version: int
    enrolled_at: datetime


class AgentPage(BaseModel):
    items: list[AgentSummary]
    total: int
    limit: int
    offset: int


class GroupSummary(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    agent_count: int
    created_at: datetime


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None


class UpdateGroupRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None


class CertificateSummary(BaseModel):
    id: uuid.UUID
    serial: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    issued_at: datetime
    revoked_at: datetime | None
    revocation_reason: str | None


class AgentDetail(AgentSummary):
    machine_id: str
    tags: dict[str, Any]
    certificate: CertificateSummary | None


class UpdateAgentRequest(BaseModel):
    group_id: uuid.UUID | None = None
    tags: dict[str, Any] | None = None


class RevokeAgentRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


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


class CreateCommandRequest(BaseModel):
    type: CommandType
    payload: dict[str, Any] = Field(default_factory=dict)
    # Час по умолчанию, неделя максимум: команда со сроком годности в месяц
    # равносильна команде без срока годности.
    ttl_seconds: int = Field(default=3600, ge=60, le=604800)


class CommandResponse(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    created_at: datetime
    sent_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime


class FleetCommand(CommandResponse):
    agent_id: uuid.UUID
    hostname: str


class CommandPage(BaseModel):
    items: list[FleetCommand]
    total: int
    limit: int
    offset: int


class AuditEntry(BaseModel):
    id: uuid.UUID
    seq: int
    at: datetime
    user_id: uuid.UUID | None
    username: str | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    payload: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditEntry]
    # Курсор — это seq последней показанной записи. Смещение по offset
    # на растущей таблице пропускает строки: пока оператор листает,
    # сверху добавляются новые.
    next_cursor: int | None


class AuditIntegrity(BaseModel):
    intact: bool
    broken_seq: int | None


class FleetCounts(BaseModel):
    total: int
    active: int
    offline: int
    pending: int
    quarantined: int
    revoked: int


class CommandCounts(BaseModel):
    queued: int
    failed_24h: int


class VersionCount(BaseModel):
    value: str
    count: int


class Overview(BaseModel):
    agents: FleetCounts
    certificates_expiring: int
    tokens_active: int
    commands: CommandCounts
    events_24h: int
    agent_versions: list[VersionCount]
    operating_systems: list[VersionCount]


class VerdictSummary(BaseModel):
    status: str
    score: int
    severity: str


class EventSummary(BaseModel):
    event_id: uuid.UUID
    agent_id: uuid.UUID
    hostname: str
    occurred_at: datetime
    received_at: datetime
    channel: str
    action: str
    severity: str
    actor: dict[str, Any]
    process: dict[str, Any]
    subject: dict[str, Any]
    labels: dict[str, Any]
    artifact_sha256: str | None
    artifact_uploaded: bool = False
    verdict: VerdictSummary | None = None


class EventPage(BaseModel):
    items: list[EventSummary]
    next_cursor: str | None


class IncidentSummary(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    hostname: str
    artifact_sha256: str
    title: str
    severity: str
    score: int
    status: str
    events_count: int
    first_event_at: datetime
    last_event_at: datetime
    assignee: uuid.UUID | None = None


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    next_cursor: str | None


class IncidentMatch(BaseModel):
    rule_key: str
    rule_title: str = ""
    count: int
    points: int
    samples: list[str]


class IncidentEventRef(BaseModel):
    event_id: uuid.UUID
    occurred_at: datetime
    action: str
    severity: str
    dst_path: str | None = None


class IncidentDetail(IncidentSummary):
    verdict: VerdictSummary | None = None
    matches: list[IncidentMatch]
    events: list[IncidentEventRef]


class IncidentStatusUpdate(BaseModel):
    status: Literal["acknowledged", "closed"]
