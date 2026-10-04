import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.user import User, UserRole
from barysguard.services.auth import hash_password

# Временный пароль печатается один раз и живёт до первой смены.
TEMPORARY_PASSWORD_BYTES = 12
API_KEY_BYTES = 32


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return secrets.token_urlsafe(API_KEY_BYTES)


def generate_password() -> str:
    return secrets.token_urlsafe(TEMPORARY_PASSWORD_BYTES)


async def username_taken(session: AsyncSession, username: str) -> bool:
    found = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    return found is not None


async def create_account(
    session: AsyncSession,
    *,
    username: str,
    role: UserRole,
    scope_group_id: uuid.UUID | None = None,
    with_password: bool = True,
    with_api_key: bool = False,
) -> tuple[User, str | None, str | None]:
    """Создаёт учётную запись и возвращает открытые секреты — один раз.

    Пароль и ключ независимы: человек входит в консоль паролем, автоматизация
    ходит ключом. Навязывать учётной записи обе формы значит создавать
    секрет, которым никто не пользуется, но который можно украсть.
    """
    password = generate_password() if with_password else None
    api_key = generate_api_key() if with_api_key else None

    user = User(
        username=username,
        role=role,
        scope_group_id=scope_group_id,
        password_hash=hash_password(password) if password else None,
        api_key_sha256=hash_api_key(api_key) if api_key else None,
        must_change_password=password is not None,
    )
    session.add(user)
    await session.flush()
    return user, password, api_key


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    role: UserRole,
    scope_group_id: uuid.UUID | None = None,
) -> tuple[str, User]:
    """Учётная запись для автоматизации: только API-ключ.

    Сохранена ради вызывающих, которым нужен именно ключ; новый код
    пользуется create_account.
    """
    user, _, api_key = await create_account(
        session,
        username=username,
        role=role,
        scope_group_id=scope_group_id,
        with_password=False,
        with_api_key=True,
    )
    assert api_key is not None
    return api_key, user


async def find_active_user_by_key(session: AsyncSession, raw_key: str) -> User | None:
    statement = select(User).where(
        User.api_key_sha256 == hash_api_key(raw_key),
        User.is_active.is_(True),
    )
    return (await session.execute(statement)).scalar_one_or_none()
