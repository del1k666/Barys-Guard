import pytest
from sqlalchemy import select

from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.users import create_user


@pytest.mark.asyncio
async def test_create_user_returns_plaintext_key_once(session):
    raw_key, user = await create_user(session, username="admin", role=UserRole.ADMIN)

    assert len(raw_key) >= 32
    assert user.api_key_sha256 != raw_key
    assert user.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_creating_token_requires_api_key(app_client):
    response = await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 1})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_is_rejected(app_client):
    response = await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 1},
        headers={"X-Api-Key": "wrong-key-value-that-does-not-exist"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_operator_creates_enrollment_token(app_client, session):
    raw_key, _ = await create_user(session, username="officer", role=UserRole.OPERATOR)
    await session.commit()

    response = await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 5, "ttl_hours": 12},
        headers={"X-Api-Key": raw_key},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("BG-ENROLL-")
    assert body["max_uses"] == 5


@pytest.mark.asyncio
async def test_token_creation_is_audited(app_client, session):
    raw_key, user = await create_user(session, username="audited", role=UserRole.OPERATOR)
    await session.commit()

    await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 1},
        headers={"X-Api-Key": raw_key},
    )

    found = await session.execute(
        select(AuditLog).where(AuditLog.action == "enrollment_token.create")
    )
    entries = found.scalars().all()
    assert any(entry.user_id == user.id for entry in entries)


@pytest.mark.asyncio
async def test_deactivated_user_is_rejected(app_client, session):
    raw_key, user = await create_user(session, username="fired", role=UserRole.OPERATOR)
    user.is_active = False
    await session.commit()

    response = await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 1},
        headers={"X-Api-Key": raw_key},
    )

    assert response.status_code == 401
