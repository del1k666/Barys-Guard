from datetime import UTC, datetime, timedelta

import pytest

from barysguard.core.errors import (
    TokenExhausted,
    TokenExpired,
    TokenNotFound,
    TokenRevoked,
)
from barysguard.services.enrollment import (
    consume_enrollment_token,
    create_enrollment_token,
    generate_token,
    hash_token,
)


def test_generated_token_has_expected_shape():
    token = generate_token()

    assert token.startswith("BG-ENROLL-")
    assert len(token) == len("BG-ENROLL-") + 32
    assert generate_token() != generate_token()


def test_hash_is_sha256_hex():
    digest = hash_token("BG-ENROLL-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    assert len(digest) == 64
    assert int(digest, 16) >= 0


@pytest.mark.asyncio
async def test_plaintext_token_is_never_stored(session):
    raw, record = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )

    assert record.token_sha256 == hash_token(raw)
    assert raw not in record.token_sha256


@pytest.mark.asyncio
async def test_valid_token_is_consumed(session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=2
    )

    consumed = await consume_enrollment_token(session, raw)

    assert consumed.used_count == 1


@pytest.mark.asyncio
async def test_unknown_token_is_rejected(session):
    with pytest.raises(TokenNotFound):
        await consume_enrollment_token(session, "BG-ENROLL-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ")


@pytest.mark.asyncio
async def test_expired_token_is_rejected(session):
    raw, record = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    with pytest.raises(TokenExpired):
        await consume_enrollment_token(session, raw)


@pytest.mark.asyncio
async def test_exhausted_token_is_rejected(session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await consume_enrollment_token(session, raw)

    with pytest.raises(TokenExhausted):
        await consume_enrollment_token(session, raw)


@pytest.mark.asyncio
async def test_revoked_token_is_rejected(session):
    raw, record = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=5
    )
    record.revoked_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(TokenRevoked):
        await consume_enrollment_token(session, raw)
