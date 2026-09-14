import pytest
from sqlalchemy import select

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.config import AgentConfigDocument, merge_documents
from barysguard.services.users import create_user
from tests.helpers import enroll_agent


def _document(**overrides) -> dict:
    """Полный документ с точечной правкой: PUT заменяет целиком."""
    return merge_documents(AgentConfigDocument().model_dump(mode="json"), overrides)


async def _key(session, username: str, role: UserRole, scope_group_id=None) -> str:
    raw_key, user = await create_user(session, username=username, role=role)
    if scope_group_id is not None:
        user.scope_group_id = scope_group_id
    await session.commit()
    return raw_key


@pytest.mark.asyncio
async def test_admin_replaces_global_config(app_client, session):
    key = await _key(session, "cfg-admin", UserRole.ADMIN)
    headers = {"X-Api-Key": key}

    response = await app_client.put(
        "/api/v1/config",
        headers=headers,
        json={"document": _document(transport={"heartbeat_interval_seconds": 45})},
    )

    assert response.status_code == 200
    assert response.json()["document"]["transport"]["heartbeat_interval_seconds"] == 45

    read_back = await app_client.get("/api/v1/config", headers=headers)
    assert read_back.json()["document"]["transport"]["heartbeat_interval_seconds"] == 45


@pytest.mark.asyncio
async def test_operator_cannot_change_config(app_client, session):
    # Интервал в 86400 секунд бесшумно ослепляет весь флот: это изменение
    # того же веса, что отзыв сертификата.
    key = await _key(session, "cfg-operator", UserRole.OPERATOR)

    response = await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": _document(transport={"heartbeat_interval_seconds": 45})},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unknown_key_is_rejected(app_client, session):
    key = await _key(session, "cfg-typo", UserRole.ADMIN)

    response = await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": {"transport": {"heartbeat_intervall": 45}}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_out_of_range_interval_is_rejected(app_client, session):
    key = await _key(session, "cfg-range", UserRole.ADMIN)

    response = await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": _document(transport={"heartbeat_interval_seconds": 86400})},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_group_override_is_stored_and_removed(app_client, session):
    group = AgentGroup(name="branch")
    session.add(group)
    await session.flush()
    key = await _key(session, "cfg-group", UserRole.ADMIN)
    headers = {"X-Api-Key": key}

    put = await app_client.put(
        f"/api/v1/groups/{group.id}/config",
        headers=headers,
        json={"document": _document(logging={"level": "debug"})},
    )
    assert put.status_code == 200

    removed = await app_client.delete(f"/api/v1/groups/{group.id}/config", headers=headers)
    assert removed.status_code == 204

    after = await app_client.get(f"/api/v1/groups/{group.id}/config", headers=headers)
    assert after.status_code == 404


@pytest.mark.asyncio
async def test_effective_config_for_agent_is_visible(app_client, session):
    agent = await enroll_agent(app_client, session, "cfg-effective")
    key = await _key(session, "cfg-reader", UserRole.OPERATOR)

    response = await app_client.get(
        f"/api/v1/agents/{agent.agent_id}/config", headers={"X-Api-Key": key}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] > 0
    assert body["document"]["transport"]["heartbeat_interval_seconds"] == 30


@pytest.mark.asyncio
async def test_config_change_is_audited(app_client, session):
    key = await _key(session, "cfg-audited", UserRole.ADMIN)

    await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": _document(logging={"level": "warn"})},
    )

    entries = (await session.execute(select(AuditLog))).scalars().all()
    assert any(entry.action == "config.update" for entry in entries)


@pytest.mark.asyncio
async def test_scoped_operator_cannot_read_foreign_agent_config(app_client, session):
    own_group = AgentGroup(name="own")
    other_group = AgentGroup(name="other")
    session.add_all([own_group, other_group])
    await session.flush()

    foreign = await enroll_agent(app_client, session, "cfg-foreign", group_id=other_group.id)
    key = await _key(session, "cfg-scoped", UserRole.OPERATOR, scope_group_id=own_group.id)

    response = await app_client.get(
        f"/api/v1/agents/{foreign.agent_id}/config", headers={"X-Api-Key": key}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_config_requires_api_key(app_client):
    response = await app_client.get("/api/v1/config")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_second_put_updates_existing_row(app_client, session):
    """Вторая правка идёт по ветке UPDATE, а не INSERT.

    В боевой базе глобальную строку создаёт миграция данных, поэтому первый
    же PUT попадает на UPDATE. Фикстура тестов эту строку вычищает, и без
    отдельной проверки ветка UPDATE остаётся непройденной.
    """
    key = await _key(session, "cfg-twice", UserRole.ADMIN)
    headers = {"X-Api-Key": key}

    first = await app_client.put(
        "/api/v1/config",
        headers=headers,
        json={"document": _document(logging={"level": "warn"})},
    )
    assert first.status_code == 200

    second = await app_client.put(
        "/api/v1/config",
        headers=headers,
        json={"document": _document(logging={"level": "debug"})},
    )

    assert second.status_code == 200
    assert second.json()["document"]["logging"]["level"] == "debug"
    assert second.json()["updated_at"] is not None


@pytest.mark.asyncio
async def test_second_put_updates_existing_group_row(app_client, session):
    group = AgentGroup(name="twice")
    session.add(group)
    await session.flush()
    key = await _key(session, "cfg-group-twice", UserRole.ADMIN)
    headers = {"X-Api-Key": key}

    await app_client.put(
        f"/api/v1/groups/{group.id}/config",
        headers=headers,
        json={"document": _document(logging={"level": "warn"})},
    )
    second = await app_client.put(
        f"/api/v1/groups/{group.id}/config",
        headers=headers,
        json={"document": _document(logging={"level": "debug"})},
    )

    assert second.status_code == 200
    assert second.json()["document"]["logging"]["level"] == "debug"
