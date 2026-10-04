import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class ConsoleSession(Base):
    """Сессия оператора в веб-консоли.

    Хранится хеш токена, а не сам токен: дамп базы не должен давать
    возможности войти под оператором. То же правило, что у enrollment-токенов
    и API-ключей.

    Сессии серверные, а не JWT, ради отзыва: доступ к системе, читающей
    переписку сотрудников, обязан отниматься мгновенно, а не по истечении
    срока подписанного токена.
    """

    __tablename__ = "console_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(256))
