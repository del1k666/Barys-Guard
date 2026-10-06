import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 64 * 1024


class Channel(enum.StrEnum):
    FILE = "file"
    USB = "usb"
    CLIPBOARD = "clipboard"
    NETWORK = "network"
    PRINT = "print"
    PROCESS = "process"
    AGENT = "agent"


class Severity(enum.StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EventArtifact(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    uploaded: bool = False


class EventEnvelope(BaseModel):
    """Конверт события, раздел 9 основной спеки.

    Поля, которых здесь нет, игнорируются (extra="ignore"). Это сознательно
    касается agent_id: личность агента определяет сертификат, а присланное
    в теле значение позволило бы скомпрометированному агенту писать события
    от чужого имени.
    """

    model_config = ConfigDict(extra="ignore")

    event_id: uuid.UUID
    schema_version: int
    occurred_at: datetime
    channel: Channel
    action: str = Field(min_length=1, max_length=64)
    severity_hint: Severity = Severity.INFO
    actor: dict[str, Any] = Field(default_factory=dict)
    process: dict[str, Any] = Field(default_factory=dict)
    subject: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)
    artifact: EventArtifact | None = None

    @field_validator("occurred_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        # Время без пояса база трактует по поясу сессии, и событие уезжает
        # в чужой месяц и чужой раздел.
        if value.tzinfo is None:
            raise ValueError("occurred_at must carry a timezone")
        return value
