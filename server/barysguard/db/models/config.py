import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class ConfigScope(enum.StrEnum):
    GLOBAL = "global"
    GROUP = "group"


class AgentConfig(Base):
    """Строка конфигурации: либо одна глобальная, либо переопределение группы."""

    __tablename__ = "agent_configs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope: Mapped[ConfigScope] = mapped_column(
        Enum(ConfigScope, name="config_scope", native_enum=False, length=32)
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="CASCADE")
    )
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Сравнение с именами членов в верхнем регистре, а не со значениями:
        # при native_enum=False SQLAlchemy хранит именно имя (GLOBAL), и такое
        # же соглашение действует в agent_status и user_role.
        CheckConstraint(
            "(scope = 'GLOBAL' AND group_id IS NULL) OR (scope = 'GROUP' AND group_id IS NOT NULL)",
            # Соглашение об именовании подставит префикс ck_agent_configs_ само.
            name="scope_group",
        ),
        # Две глобальные строки сделали бы эффективный конфиг зависящим от
        # порядка выборки. Такая ошибка проявляется не при записи, а спустя
        # месяцы и на одном агенте из тысячи.
        Index(
            "uq_agent_configs_global",
            "scope",
            unique=True,
            postgresql_where=text("scope = 'GLOBAL'"),
        ),
        Index(
            "uq_agent_configs_group",
            "group_id",
            unique=True,
            postgresql_where=text("scope = 'GROUP'"),
        ),
    )
