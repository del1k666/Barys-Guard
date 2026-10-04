"""Список и отзыв токенов регистрации."""

from datetime import UTC, datetime, timedelta

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.enrollment import EnrollmentToken
from barysguard.db.models.user import UserRole
from barysguard.services.enrollment import create_enrollment_token
from tests.helpers import login_as


async def _group(session, name: str) -> AgentGroup:
    group = AgentGroup(name=name)
    session.add(group)
    await session.flush()
    return group


async def test_issued_tokens_are_listed_without_the_secret(app_client, session) -> None:
    await login_as(app_client, session, username="token-lister", role=UserRole.ADMIN)

    created = await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 3})
    secret = created.json()["token"]

    response = await app_client.get("/api/v1/enrollment-tokens")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["max_uses"] == 3
    assert rows[0]["state"] == "active"
    # Открытая форма токена показывается один раз при выдаче и больше
    # не существует нигде: в базе только хеш.
    assert secret not in str(rows[0])


async def test_expired_and_exhausted_tokens_are_marked(app_client, session) -> None:
    await login_as(app_client, session, username="token-states", role=UserRole.ADMIN)

    _, expired = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=1, max_uses=1
    )
    expired.expires_at = datetime.now(UTC) - timedelta(hours=2)

    _, exhausted = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=2
    )
    exhausted.used_count = 2
    await session.commit()

    rows = {row["id"]: row for row in (await app_client.get("/api/v1/enrollment-tokens")).json()}

    assert rows[str(expired.id)]["state"] == "expired"
    assert rows[str(exhausted.id)]["state"] == "exhausted"


async def test_token_is_revoked_before_it_is_used(app_client, session) -> None:
    await login_as(app_client, session, username="token-revoker", role=UserRole.ADMIN)

    created = await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 1})
    token_id = (await app_client.get("/api/v1/enrollment-tokens")).json()[0]["id"]

    response = await app_client.post(f"/api/v1/enrollment-tokens/{token_id}/revoke")
    assert response.status_code == 204, response.text

    # Отозванный токен обязан перестать регистрировать агентов немедленно:
    # утёкший токен — это чужой агент в контуре.
    enroll = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": created.json()["token"],
            "csr_pem": "не важно",
            "host": {
                "machine_id": "machine-revoked-token",
                "hostname": "ws",
                "os": "linux",
                "os_version": "6.8.0",
                "arch": "amd64",
                "agent_version": "0.1.0",
            },
        },
    )
    assert enroll.status_code in {400, 401, 403, 422}

    session.expire_all()
    record = await session.get(EnrollmentToken, __import__("uuid").UUID(token_id))
    assert record.revoked_at is not None


async def test_scoped_operator_sees_only_tokens_of_its_groups(app_client, session) -> None:
    branch = await _group(session, "Филиал Астана")
    other = await _group(session, "Филиал Алматы")
    await session.commit()

    await create_enrollment_token(
        session, created_by=None, group_id=branch.id, ttl_hours=24, max_uses=1
    )
    await create_enrollment_token(
        session, created_by=None, group_id=other.id, ttl_hours=24, max_uses=1
    )
    await create_enrollment_token(session, created_by=None, group_id=None, ttl_hours=24, max_uses=1)
    await session.commit()

    await login_as(app_client, session, username="astana-tokens", scope_group_id=branch.id)

    rows = (await app_client.get("/api/v1/enrollment-tokens")).json()

    # Ни чужой филиал, ни нераспределённый токен: последний регистрирует
    # агента вне всяких групп, то есть вне области видимости офицера.
    assert [row["group_id"] for row in rows] == [str(branch.id)]


async def test_scoped_operator_cannot_revoke_a_foreign_token(app_client, session) -> None:
    branch = await _group(session, "Филиал Астана")
    other = await _group(session, "Филиал Алматы")
    await session.commit()

    _, foreign = await create_enrollment_token(
        session, created_by=None, group_id=other.id, ttl_hours=24, max_uses=1
    )
    await session.commit()

    await login_as(app_client, session, username="astana-revoker", scope_group_id=branch.id)

    response = await app_client.post(f"/api/v1/enrollment-tokens/{foreign.id}/revoke")

    assert response.status_code == 404
