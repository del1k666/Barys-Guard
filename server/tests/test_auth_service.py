import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.user import User, UserRole
from barysguard.services.auth import (
    LOCKOUT_MINUTES,
    MAX_LOGIN_FAILURES,
    LoginOutcome,
    authenticate,
    create_session,
    find_valid_session,
    hash_password,
    revoke_user_sessions,
    touch_session,
    verify_password,
)

PASSWORD = "correct horse battery staple"


async def _make_user(session: AsyncSession, role: UserRole = UserRole.OPERATOR) -> User:
    user = User(
        username=f"operator-{uuid.uuid4().hex[:8]}",
        password_hash=hash_password(PASSWORD),
        role=role,
    )
    session.add(user)
    await session.flush()
    return user


def test_password_verifies_against_its_own_hash() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password(PASSWORD, stored) is True
    assert verify_password(PASSWORD[:-1], stored) is False


async def test_session_is_found_by_its_raw_token(session) -> None:
    user = await _make_user(session)

    raw, record = await create_session(
        session, user_id=user.id, ip=None, user_agent="pytest", ttl_minutes=720
    )
    await session.flush()

    found = await find_valid_session(session, raw, idle_minutes=30)
    assert found is not None
    assert found.id == record.id

    assert await find_valid_session(session, "не тот токен", idle_minutes=30) is None


async def test_session_expires_at_its_absolute_deadline(session) -> None:
    user = await _make_user(session)
    started = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    raw, _ = await create_session(
        session, user_id=user.id, ip=None, user_agent=None, ttl_minutes=720, now=started
    )
    await session.flush()

    before = started + timedelta(minutes=719)
    after = started + timedelta(minutes=721)

    assert await find_valid_session(session, raw, idle_minutes=10_000, now=before) is not None
    assert await find_valid_session(session, raw, idle_minutes=10_000, now=after) is None


async def test_session_expires_after_idling(session) -> None:
    user = await _make_user(session)
    started = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    raw, _ = await create_session(
        session, user_id=user.id, ip=None, user_agent=None, ttl_minutes=720, now=started
    )
    await session.flush()

    assert (
        await find_valid_session(session, raw, idle_minutes=30, now=started + timedelta(minutes=31))
        is None
    )


async def test_revoked_session_is_not_valid(session) -> None:
    user = await _make_user(session)

    raw, record = await create_session(
        session, user_id=user.id, ip=None, user_agent=None, ttl_minutes=720
    )
    await session.flush()

    record.revoked_at = datetime.now(UTC)
    await session.flush()

    assert await find_valid_session(session, raw, idle_minutes=30) is None


async def test_correct_password_authenticates_and_clears_failures(session) -> None:
    user = await _make_user(session)
    user.failed_attempts = 3
    await session.flush()

    outcome, authenticated = await authenticate(session, username=user.username, password=PASSWORD)

    assert outcome is LoginOutcome.SUCCESS
    assert authenticated is not None and authenticated.id == user.id
    assert user.failed_attempts == 0


async def test_wrong_password_counts_towards_lockout(session) -> None:
    user = await _make_user(session)

    outcome, authenticated = await authenticate(
        session, username=user.username, password="не тот пароль"
    )

    assert outcome is LoginOutcome.INVALID
    assert authenticated is None
    assert user.failed_attempts == 1


async def test_account_locks_after_too_many_failures(session) -> None:
    user = await _make_user(session)
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    for _ in range(MAX_LOGIN_FAILURES):
        await authenticate(session, username=user.username, password="мимо", now=now)

    assert user.locked_until is not None

    # Верный пароль не открывает заблокированную учётку: иначе блокировка
    # не мешала бы подбору, она лишь задерживала бы удачную попытку.
    outcome, _ = await authenticate(session, username=user.username, password=PASSWORD, now=now)
    assert outcome is LoginOutcome.LOCKED

    later = now + timedelta(minutes=LOCKOUT_MINUTES, seconds=1)
    outcome, _ = await authenticate(session, username=user.username, password=PASSWORD, now=later)
    assert outcome is LoginOutcome.SUCCESS


async def test_deactivated_operator_cannot_log_in(session) -> None:
    user = await _make_user(session)
    user.is_active = False
    await session.flush()

    outcome, _ = await authenticate(session, username=user.username, password=PASSWORD)
    assert outcome is LoginOutcome.DISABLED


async def test_machine_account_without_password_cannot_log_in(session) -> None:
    user = User(username=f"robot-{uuid.uuid4().hex[:8]}", role=UserRole.OPERATOR)
    session.add(user)
    await session.flush()

    outcome, _ = await authenticate(session, username=user.username, password="")
    assert outcome is LoginOutcome.INVALID


async def test_unknown_username_is_indistinguishable_from_wrong_password(session) -> None:
    outcome, _ = await authenticate(session, username="нет такого", password="что угодно")
    assert outcome is LoginOutcome.INVALID


async def test_touching_session_extends_the_idle_window(session) -> None:
    user = await _make_user(session)
    started = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    raw, record = await create_session(
        session, user_id=user.id, ip=None, user_agent=None, ttl_minutes=720, now=started
    )
    await session.flush()

    await touch_session(session, record, now=started + timedelta(minutes=20))

    assert (
        await find_valid_session(session, raw, idle_minutes=30, now=started + timedelta(minutes=45))
        is not None
    )


async def test_revoking_other_sessions_keeps_the_current_one(session) -> None:
    user = await _make_user(session)

    stale, _ = await create_session(
        session, user_id=user.id, ip=None, user_agent=None, ttl_minutes=720
    )
    current_raw, current = await create_session(
        session, user_id=user.id, ip=None, user_agent=None, ttl_minutes=720
    )
    await session.flush()

    revoked = await revoke_user_sessions(session, user_id=user.id, keep_session_id=current.id)

    assert revoked == 1
    assert await find_valid_session(session, stale, idle_minutes=30) is None
    assert await find_valid_session(session, current_raw, idle_minutes=30) is not None
