import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class AgentStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    OFFLINE = "offline"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"


class AgentGroup(Base):
    __tablename__ = "agent_groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Устойчивый идентификатор железа: на Windows — UUID из SMBIOS,
    # на Linux — /etc/machine-id. Позволяет узнать хост при повторной регистрации.
    machine_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    hostname: Mapped[str] = mapped_column(String(255))
    os: Mapped[str] = mapped_column(String(32))
    os_version: Mapped[str] = mapped_column(String(128))
    arch: Mapped[str] = mapped_column(String(32))
    agent_version: Mapped[str] = mapped_column(String(32))

    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", native_enum=False, length=32),
        default=AgentStatus.PENDING,
        index=True,
    )

    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # Дрейф часов агента относительно сервера. Отрицательное значение —
    # часы агента отстают. Существенное расхождение само по себе является
    # поводом для события безопасности.
    clock_skew_ms: Mapped[int] = mapped_column(BigInteger, default=0)

    config_version: Mapped[int] = mapped_column(Integer, default=0)
    last_ip: Mapped[str | None] = mapped_column(INET)
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
