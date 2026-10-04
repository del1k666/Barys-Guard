from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.users import create_user
from tests.helpers import enroll_agent


async def _key(session, username: str, role: UserRole, scope_group_id=None) -> str:
    raw_key, user = await create_user(session, username=username, role=role)
    if scope_group_id is not None:
        user.scope_group_id = scope_group_id
    await session.commit()
    return raw_key


@pytest.mark.asyncio
async def test_operator_queues_command(app_client, session):
    agent = await enroll_agent(app_client, session, "cmd-queue")
    key = await _key(session, "cmd-operator", UserRole.OPERATOR)

    response = await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["type"] == "ping"


@pytest.mark.asyncio
async def test_unknown_command_type_is_rejected(app_client, session):
    # Строгое перечисление: опечатка в очереди обнаружилась бы иначе только
    # по ошибке от агента через полминуты.
    agent = await enroll_agent(app_client, session, "cmd-typo")
    key = await _key(session, "cmd-typo-operator", UserRole.OPERATOR)

    response = await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "reboot", "payload": {}, "ttl_seconds": 3600},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_command_creation_is_audited(app_client, session):
    agent = await enroll_agent(app_client, session, "cmd-audit")
    key = await _key(session, "cmd-audit-operator", UserRole.OPERATOR)

    await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    entries = (await session.execute(select(AuditLog))).scalars().all()
    assert any(entry.action == "command.create" for entry in entries)


@pytest.mark.asyncio
async def test_scoped_operator_cannot_command_foreign_agent(app_client, session):
    own_group = AgentGroup(name="own")
    other_group = AgentGroup(name="other")
    session.add_all([own_group, other_group])
    await session.flush()

    foreign = await enroll_agent(app_client, session, "cmd-foreign", group_id=other_group.id)
    key = await _key(session, "cmd-scoped", UserRole.OPERATOR, scope_group_id=own_group.id)

    response = await app_client.post(
        f"/api/v1/agents/{foreign.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_command_history_is_listed(app_client, session):
    agent = await enroll_agent(app_client, session, "cmd-history")
    key = await _key(session, "cmd-history-operator", UserRole.OPERATOR)
    headers = {"X-Api-Key": key}

    await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers=headers,
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    response = await app_client.get(f"/api/v1/agents/{agent.agent_id}/commands", headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_silent_agent_is_listed_offline(app_client, session):
    enrolled = await enroll_agent(app_client, session, "cmd-silent")
    key = await _key(session, "cmd-silent-operator", UserRole.OPERATOR)

    agent = await session.get(Agent, enrolled.agent_id)
    # Три пропущенных интервала по 30 секунд.
    agent.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=200)
    await session.commit()

    response = await app_client.get("/api/v1/agents", headers={"X-Api-Key": key})

    entry = next(item for item in response.json()["items"] if item["id"] == str(enrolled.agent_id))
    assert entry["status"] == "offline"
