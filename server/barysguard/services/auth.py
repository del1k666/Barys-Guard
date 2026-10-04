"""Пароли операторов и сессии веб-консоли."""

import enum
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.console_session import ConsoleSession
from barysguard.db.models.user import User

# Параметры scrypt. n=2^15 при r=8 требует 32 МиБ памяти на проверку —
# это и есть защита: перебор на GPU упирается в память, а не в такты.
# maxmem задаётся явно, иначе OpenSSL отвергает вызов своим лимитом по умолчанию.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 96 * 1024 * 1024

ALGORITHM = "scrypt"


def hash_password(raw: str) -> str:
    """Хеш пароля вместе с параметрами: они понадобятся при их смене."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        raw.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return f"{ALGORITHM}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(raw: str, stored: str) -> bool:
    """Проверка пароля. Любая неразборчивая запись считается непройденной."""
    try:
        algorithm, n_text, r_text, p_text, salt_hex, expected_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        derived = hashlib.scrypt(
            raw.encode("utf-8"),
            salt=salt,
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(expected),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(derived, expected)


SESSION_TOKEN_BYTES = 32


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    ip: str | None,
    user_agent: str | None,
    ttl_minutes: int,
    now: datetime | None = None,
) -> tuple[str, ConsoleSession]:
    """Заводит сессию и возвращает токен. Токен существует только здесь и в cookie."""
    moment = now or datetime.now(UTC)
    raw = generate_session_token()

    record = ConsoleSession(
        user_id=user_id,
        token_sha256=hash_session_token(raw),
        expires_at=moment + timedelta(minutes=ttl_minutes),
        last_seen_at=moment,
        ip=ip,
        # Заголовок клиента обрезается, а не отвергается: длинный User-Agent
        # не повод отказать во входе, но и хранить его целиком незачем.
        user_agent=user_agent[:256] if user_agent else None,
    )
    db.add(record)
    await db.flush()
    return raw, record


async def find_valid_session(
    db: AsyncSession,
    raw_token: str,
    *,
    idle_minutes: int,
    now: datetime | None = None,
) -> ConsoleSession | None:
    """Действующая сессия по токену либо None.

    Просроченная, отозванная и простаивавшая дольше допустимого неотличимы
    от несуществующей: вызывающей стороне во всех случаях нужен один ответ —
    войдите заново.
    """
    moment = now or datetime.now(UTC)

    record = (
        await db.execute(
            select(ConsoleSession).where(
                ConsoleSession.token_sha256 == hash_session_token(raw_token)
            )
        )
    ).scalar_one_or_none()

    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at <= moment:
        return None
    if record.last_seen_at + timedelta(minutes=idle_minutes) <= moment:
        return None

    return record


class LoginOutcome(enum.StrEnum):
    SUCCESS = "success"
    INVALID = "invalid"
    LOCKED = "locked"
    DISABLED = "disabled"


# Десять промахов подряд — это уже не опечатка. Четверти часа достаточно,
# чтобы перебор стал бессмысленным, и мало, чтобы блокировка сама превратилась
# в отказ в обслуживании для того, кто просто забыл раскладку.
MAX_LOGIN_FAILURES = 10
LOCKOUT_MINUTES = 15


@lru_cache(maxsize=1)
def _timing_decoy() -> str:
    """Хеш случайного пароля для выравнивания времени ответа.

    Без него вход с несуществующим именем отвечает мгновенно, а с существующим —
    через время проверки scrypt, и это различие позволяет перебрать список
    операторов, не зная ни одного пароля.
    """
    return hash_password(secrets.token_urlsafe(16))


async def authenticate(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    now: datetime | None = None,
    max_failures: int = MAX_LOGIN_FAILURES,
    lockout_minutes: int = LOCKOUT_MINUTES,
) -> tuple[LoginOutcome, User | None]:
    """Проверяет пару имя-пароль и ведёт счёт неудачам.

    Исход возвращается разборчивым ради журнала аудита; наружу, в ответ
    HTTP, он не просачивается — там причина отказа всегда одна и та же.
    """
    moment = now or datetime.now(UTC)

    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()

    if user is None:
        verify_password(password, _timing_decoy())
        return LoginOutcome.INVALID, None

    if not user.is_active:
        return LoginOutcome.DISABLED, None

    if user.locked_until is not None and user.locked_until > moment:
        return LoginOutcome.LOCKED, None

    if user.password_hash is None:
        verify_password(password, _timing_decoy())
        return LoginOutcome.INVALID, None

    if not verify_password(password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= max_failures:
            user.locked_until = moment + timedelta(minutes=lockout_minutes)
        await db.flush()
        return LoginOutcome.INVALID, None

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = moment
    await db.flush()
    return LoginOutcome.SUCCESS, user


async def touch_session(
    db: AsyncSession, record: ConsoleSession, *, now: datetime | None = None
) -> None:
    """Отмечает сессию живой. Сдвигает окно простоя, но не абсолютный срок."""
    record.last_seen_at = now or datetime.now(UTC)
    await db.flush()


async def revoke_session(
    db: AsyncSession, record: ConsoleSession, *, now: datetime | None = None
) -> None:
    record.revoked_at = now or datetime.now(UTC)
    await db.flush()


async def revoke_user_sessions(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    keep_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Отзывает сессии оператора и возвращает их число.

    Применяется при смене пароля, сбросе пароля администратором и
    деактивации учётной записи: во всех трёх случаях прежние входы
    обязаны прекратиться немедленно, иначе смена пароля не защищает
    от того, кто уже внутри.
    """
    moment = now or datetime.now(UTC)

    statement = (
        update(ConsoleSession)
        .where(
            ConsoleSession.user_id == user_id,
            ConsoleSession.revoked_at.is_(None),
        )
        .values(revoked_at=moment)
    )
    if keep_session_id is not None:
        statement = statement.where(ConsoleSession.id != keep_session_id)

    # execute объявлен возвращающим Result, но UPDATE всегда даёт CursorResult,
    # у которого и живёт rowcount.
    result = cast(CursorResult[Any], await db.execute(statement))
    await db.flush()
    return int(result.rowcount or 0)
