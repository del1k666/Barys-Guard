import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class AgentCertificate(Base):
    """Сертификат агента.

    Вынесен в отдельную таблицу, а не в поле agents: за время жизни агента
    их накапливается десяток из-за продлений каждые 60 дней, и при
    расследовании требуется знать, каким именно сертификатом подписано
    событие полугодовой давности.
    """

    __tablename__ = "agent_certificates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )

    # Шестнадцатеричные цифры в нижнем регистре без ведущих нулей.
    # Именно в таком виде серийный номер приходит от nginx.
    serial: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), index=True)

    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(255))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_certificates.id", ondelete="SET NULL")
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
