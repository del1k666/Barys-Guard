"""Операторы консоли: создание, права, сброс пароля.

Учётные записи не удаляются — только деактивируются. Журнал аудита ссылается
на них, и удаление оператора стёрло бы имя того, кто читал перехват.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import Principal, current_principal, require_admin
from barysguard.api.schemas import (
    ApiKeyResponse,
    CreateUserRequest,
    IssuedPasswordResponse,
    UpdateUserRequest,
    UserSummary,
)
from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.user import User, UserRole
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.auth import hash_password, revoke_user_sessions
from barysguard.services.users import (
    create_account,
    generate_api_key,
    generate_password,
    hash_api_key,
)

router = APIRouter(prefix="/api/v1/users", tags=["api"])


def _summary(user: User) -> UserSummary:
    return UserSummary(
        id=user.id,
        username=user.username,
        role=user.role.value,
        scope_group_id=user.scope_group_id,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        has_password=user.password_hash is not None,
        has_api_key=user.api_key_sha256 is not None,
        last_login_at=user.last_login_at,
        locked_until=user.locked_until,
        created_at=user.created_at,
    )


async def _require_group(session: AsyncSession, group_id: uuid.UUID | None) -> None:
    if group_id is None:
        return
    if await session.get(AgentGroup, group_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")


@router.get("", response_model=list[UserSummary])
async def list_users(
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[UserSummary]:
    users = (await session.execute(select(User).order_by(User.username))).scalars().all()
    return [_summary(user) for user in users]


@router.post("", response_model=IssuedPasswordResponse, status_code=status.HTTP_201_CREATED)
async def create_console_user(
    payload: CreateUserRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> IssuedPasswordResponse:
    taken = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "username already taken")

    await _require_group(session, payload.scope_group_id)

    user, password, _ = await create_account(
        session,
        username=payload.username,
        role=UserRole(payload.role),
        scope_group_id=payload.scope_group_id,
    )
    assert password is not None

    await record_audit(
        session,
        user_id=admin.id,
        action="user.create",
        target_type="user",
        target_id=user.id,
        payload={"username": user.username, "role": user.role.value},
    )

    # Пароль показывается ровно здесь и больше нигде: в базе только хеш.
    return IssuedPasswordResponse(user=_summary(user), password=password, must_change_password=True)


@router.patch("/{user_id}", response_model=UserSummary)
async def update_console_user(
    user_id: uuid.UUID,
    payload: UpdateUserRequest,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> UserSummary:
    admin = principal.user
    if admin.role is not UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    fields = payload.model_fields_set
    changes: dict[str, str | bool | None] = {}

    if "is_active" in fields and payload.is_active is not None:
        # Самодеактивация запрещена: единственный администратор, снявший
        # себе доступ, запирает систему, и вернуть её сможет только тот,
        # у кого есть доступ к серверу и CLI.
        if user.id == admin.id and not payload.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot deactivate yourself")

        user.is_active = payload.is_active
        changes["is_active"] = payload.is_active

    if "role" in fields and payload.role is not None:
        user.role = UserRole(payload.role)
        changes["role"] = payload.role

    if "scope_group_id" in fields:
        await _require_group(session, payload.scope_group_id)
        user.scope_group_id = payload.scope_group_id
        changes["scope_group_id"] = str(payload.scope_group_id) if payload.scope_group_id else None

    await session.flush()

    if changes.get("is_active") is False:
        # Деактивация обязана выбрасывать из уже открытых вкладок, иначе
        # уволенный работает до истечения срока сессии.
        await revoke_user_sessions(session, user_id=user.id)

    if changes:
        await record_audit(
            session,
            user_id=admin.id,
            action="user.update",
            target_type="user",
            target_id=user.id,
            payload=changes,
        )

    return _summary(user)


@router.post("/{user_id}/reset-password", response_model=IssuedPasswordResponse)
async def reset_password(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> IssuedPasswordResponse:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    password = generate_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None
    await session.flush()

    revoked = await revoke_user_sessions(session, user_id=user.id)

    await record_audit(
        session,
        user_id=admin.id,
        action="user.reset_password",
        target_type="user",
        target_id=user.id,
        payload={"revoked_sessions": revoked},
    )

    return IssuedPasswordResponse(user=_summary(user), password=password, must_change_password=True)


@router.post("/{user_id}/api-key", response_model=ApiKeyResponse)
async def issue_api_key(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyResponse:
    """Выдаёт ключ для автоматизации. Прежний ключ учётной записи перестаёт работать."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    raw_key = generate_api_key()
    user.api_key_sha256 = hash_api_key(raw_key)
    await session.flush()

    await record_audit(
        session,
        user_id=admin.id,
        action="user.issue_api_key",
        target_type="user",
        target_id=user.id,
        payload={},
    )

    return ApiKeyResponse(user=_summary(user), api_key=raw_key)
