import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class CommandType(enum.StrEnum):
    PING = "ping"
    REFRESH_CONFIG = "refresh_config"
    COLLECT_DIAGNOSTICS = "collect_diagnostics"


class CommandStatus(enum.StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


class Command(Base):
    """Указание оператора отдельному агенту.

    Срок годности обязателен: команда, доставленная через три недели после
    закрытия инцидента, — это авария, а не запоздалая реакция.
    """

    __tablename__ = "commands"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[CommandType] = mapped_column(
        Enum(CommandType, name="command_type", native_enum=False, length=32)
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[CommandStatus] = mapped_column(
        Enum(CommandStatus, name="command_status", native_enum=False, length=32),
        default=CommandStatus.QUEUED,
        index=True,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Выборка на heartbeat идёт только по очереди конкретного агента.
        # Условие сравнивает с именем члена перечисления: при native_enum=False
        # в колонке лежит QUEUED, и сравнение со значением не покрыло бы ни
        # одной строки.
        Index(
            "ix_commands_queued",
            "agent_id",
            "created_at",
            postgresql_where=text("status = 'QUEUED'"),
        ),
    )
