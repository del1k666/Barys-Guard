"""Вход оператора в веб-консоль, смена пароля и собственные сессии."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import Principal, current_principal
from barysguard.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    SessionSummary,
    SessionUser,
)
from barysguard.core.config import Settings, get_settings
from barysguard.db.models.console_session import ConsoleSession
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.auth import (
    LoginOutcome,
    authenticate,
    create_session,
    hash_password,
    revoke_session,
    revoke_user_sessions,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=SessionUser)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SessionUser:
    outcome, user = await authenticate(
        session, username=payload.username, password=payload.password
    )

    if outcome is not LoginOutcome.SUCCESS or user is None:
        # Причина отказа остаётся в журнале, но не в ответе: различая
        # «нет такого оператора», «неверный пароль» и «учётка заблокирована»,
        # мы подарили бы подбирающему список действующих имён.
        await record_audit(
            session,
            user_id=None,
            action="auth.login.failure",
            target_type="user",
            target_id=None,
            payload={"username": payload.username, "outcome": outcome.value},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    raw_token, record = await create_session(
        session,
        user_id=user.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        ttl_minutes=settings.session_ttl_minutes,
    )

    await record_audit(
        session,
        user_id=user.id,
        action="auth.login.success",
        target_type="user",
        target_id=user.id,
        payload={"session_id": str(record.id)},
    )

    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=settings.session_ttl_minutes * 60,
    )
    return SessionUser.from_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if principal.session is not None:
        await revoke_session(session, principal.session)
        await record_audit(
            session,
            user_id=principal.user.id,
            action="auth.logout",
            target_type="user",
            target_id=principal.user.id,
            payload={"session_id": str(principal.session.id)},
        )

    response.delete_cookie(settings.session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=SessionUser)
async def me(principal: Principal = Depends(current_principal)) -> SessionUser:
    return SessionUser.from_user(principal.user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    user = principal.user

    if user.password_hash is None or not verify_password(
        payload.current_password, user.password_hash
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password does not match")

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(UTC)
    user.must_change_password = False
    await session.flush()

    # Текущая сессия остаётся: оператор только что подтвердил, что это он.
    # Все прочие входы прекращаются — смена пароля ради того и делается.
    revoked = await revoke_user_sessions(
        session,
        user_id=user.id,
        keep_session_id=principal.session.id if principal.session else None,
    )

    await record_audit(
        session,
        user_id=user.id,
        action="auth.password.change",
        target_type="user",
        target_id=user.id,
        payload={"revoked_sessions": revoked},
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> list[SessionSummary]:
    """Действующие входы оператора: где именно открыта консоль от его имени."""
    now = datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(ConsoleSession)
                .where(
                    ConsoleSession.user_id == principal.user.id,
                    ConsoleSession.revoked_at.is_(None),
                    ConsoleSession.expires_at > now,
                )
                .order_by(ConsoleSession.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )

    current_id = principal.session.id if principal.session else None
    return [
        SessionSummary(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            # asyncpg отдаёт INET объектом ipaddress, а не строкой.
            ip=str(row.ip) if row.ip is not None else None,
            user_agent=row.user_agent,
            current=row.id == current_id,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_own_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    record = (
        await session.execute(
            select(ConsoleSession).where(
                ConsoleSession.id == session_id,
                ConsoleSession.user_id == principal.user.id,
                ConsoleSession.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    # Чужая сессия отдаётся как несуществующая: знать о входах другого
    # оператора — не дело оператора.
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")

    await revoke_session(session, record)
    await record_audit(
        session,
        user_id=principal.user.id,
        action="auth.session.revoke",
        target_type="console_session",
        target_id=record.id,
        payload={},
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
