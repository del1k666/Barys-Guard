import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.errors import (
    TokenExhausted,
    TokenExpired,
    TokenNotFound,
    TokenRevoked,
)
from barysguard.db.models.enrollment import EnrollmentToken

TOKEN_PREFIX = "BG-ENROLL-"  # noqa: S105 — метка формата, а не секрет
TOKEN_BODY_LENGTH = 32


def generate_token() -> str:
    """Открытый токен регистрации. Base32 без набивки — безопасен для командной строки и MSI."""
    raw = secrets.token_bytes(20)
    body = base64.b32encode(raw).decode("ascii").rstrip("=")
    return TOKEN_PREFIX + body[:TOKEN_BODY_LENGTH]


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_enrollment_token(
    session: AsyncSession,
    *,
    created_by: uuid.UUID | None,
    group_id: uuid.UUID | None,
    ttl_hours: int,
    max_uses: int,
) -> tuple[str, EnrollmentToken]:
    """Создаёт токен. Открытая форма возвращается один раз и нигде не сохраняется."""
    raw = generate_token()
    record = EnrollmentToken(
        token_sha256=hash_token(raw),
        created_by=created_by,
        group_id=group_id,
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
        max_uses=max_uses,
        used_count=0,
    )
    session.add(record)
    await session.flush()
    return raw, record


async def consume_enrollment_token(session: AsyncSession, raw: str) -> EnrollmentToken:
    """Расходует одно использование токена.

    Строка блокируется через SELECT ... FOR UPDATE: без этого два агента,
    подключившиеся одновременно с одним токеном на одно использование,
    оба пройдут проверку и оба зарегистрируются.
    """
    statement = (
        select(EnrollmentToken)
        .where(EnrollmentToken.token_sha256 == hash_token(raw))
        .with_for_update()
    )
    record = (await session.execute(statement)).scalar_one_or_none()

    if record is None:
        raise TokenNotFound("enrollment token not found")
    if record.revoked_at is not None:
        raise TokenRevoked("enrollment token revoked")
    if record.expires_at <= datetime.now(UTC):
        raise TokenExpired("enrollment token expired")
    if record.used_count >= record.max_uses:
        raise TokenExhausted("enrollment token exhausted")

    record.used_count += 1
    await session.flush()
    return record
