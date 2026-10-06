import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class Event(Base):
    """Событие, присланное агентом.

    Таблица партиционирована по месяцам (RANGE по occurred_at), поэтому первичный
    ключ обязан включать occurred_at, а уникальность одного event_id база не
    обеспечивает. Идемпотентность строится на паре (occurred_at, event_id).
    Сама таблица и разделы создаются миграцией: Alembic партиционирование
    по модели не генерирует.
    """

    __tablename__ = "events"

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    channel: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    process: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    subject: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    artifact_sha256: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, server_default="info")
    # Заполняет воркер инспекции. Внешнего ключа нет: ссылки с партиционированной
    # таблицы ограничены, целостность обеспечивает код.
    verdict_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
