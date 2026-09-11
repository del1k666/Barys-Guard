import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.user import User, UserRole


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    role: UserRole,
    scope_group_id: uuid.UUID | None = None,
) -> tuple[str, User]:
    """Создаёт оператора. Открытый ключ возвращается один раз и не сохраняется."""
    raw_key = secrets.token_urlsafe(32)
    user = User(
        username=username,
        api_key_sha256=hash_api_key(raw_key),
        role=role,
        scope_group_id=scope_group_id,
    )
    session.add(user)
    await session.flush()
    return raw_key, user


async def find_active_user_by_key(session: AsyncSession, raw_key: str) -> User | None:
    statement = select(User).where(
        User.api_key_sha256 == hash_api_key(raw_key),
        User.is_active.is_(True),
    )
    return (await session.execute(statement)).scalar_one_or_none()
