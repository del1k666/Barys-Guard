import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
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
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    api_key_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False, length=32)
    )
    scope_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
