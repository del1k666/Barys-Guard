import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class Artifact(Base):
    """Содержимое файла, адресуемое по SHA-256.

    Файл лежит в хранилище зашифрованным собственным ключом; здесь только его
    обёртка мастер-ключом. Один документ, скопированный многими, — одна строка.
    """

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text)
    key_wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    wrap_version: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    first_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    # Сколько раз артефакт предъявляли серверу (ответ exists).
    ref_count: Mapped[int] = mapped_column(Integer, server_default="1")
    # Читает воркер инспекции (подпроект B2).
    scan_status: Mapped[str] = mapped_column(Text, server_default="pending")


class UploadSession(Base):
    """Открытая загрузка. Принадлежит агенту: чужой upload_id не подходит."""

    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint("agent_id", "artifact_sha256", name="uq_upload_sessions_agent_sha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    artifact_sha256: Mapped[str] = mapped_column(Text)
    expected_size: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    temp_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
