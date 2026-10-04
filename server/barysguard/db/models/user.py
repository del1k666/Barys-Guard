import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class User(Base):
    """Оператор системы.

    scope_group_id ограничивает видимость поддеревом групп агентов. В плане 1A
    поле заполняется, но применяется только в списке агентов; полноценное
    разграничение доступа появится в подпроекте 4. Заложено сразу потому,
    что офицер ИБ филиала не имеет права читать перехват другого филиала,
    и это требование чаще юридическое, чем техническое.

    Пароль и API-ключ независимы и оба необязательны: человек входит в консоль
    паролем, автоматизация ходит ключом, и навязывать учётной записи вторую
    форму доступа означает создавать секрет, которым никто не пользуется,
    но который можно украсть.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    api_key_sha256: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False, length=32)
    )
    # RESTRICT, а не SET NULL: обнуление области видимости при удалении группы
    # молча превратило бы офицера филиала в оператора, видящего весь флот.
    # Удаление группы, к которой привязан оператор, обязано быть отказом.
    scope_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Счётчик неудачных входов и срок блокировки. Живут на учётной записи,
    # а не в памяти процесса: подбор пароля не должен обнуляться перезапуском
    # сервера или попаданием на другую его копию.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
