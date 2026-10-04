"""Сводка дашборда и сквозной список команд."""

from datetime import UTC, datetime, timedelta

from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.command import Command, CommandStatus, CommandType
from barysguard.db.models.user import UserRole
from tests.helpers import enroll_agent, login_as


async def _group(session, name: str) -> AgentGroup:
    group = AgentGroup(name=name)
    session.add(group)
    await session.flush()
    return group


async def test_overview_counts_the_fleet_by_status(app_client, session) -> None:
    await login_as(app_client, session, username="dash-admin", role=UserRole.ADMIN)
    silent = await enroll_agent(app_client, session, "machine-silent")
    await enroll_agent(app_client, session, "machine-pending")
    await session.commit()

    agent = await session.get(Agent, silent.agent_id)
    agent.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=200)
    await session.commit()

    response = await app_client.get("/api/v1/overview")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agents"]["total"] == 2
    assert body["agents"]["offline"] == 1
    assert body["agents"]["pending"] == 1


async def test_overview_counts_certificates_expiring_soon(app_client, session) -> None:
    await login_as(app_client, session, username="dash-pki", role=UserRole.ADMIN)
    await enroll_agent(app_client, session, "machine-cert")
    await session.commit()

    body = (await app_client.get("/api/v1/overview")).json()

    # Свежий сертификат выдан на 90 дней, до порога в 14 ему далеко.
    assert body["certificates_expiring"] == 0


async def test_overview_counts_active_tokens_and_commands(app_client, session) -> None:
    await login_as(app_client, session, username="dash-counts", role=UserRole.ADMIN)
    await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 1})

    enrolled = await enroll_agent(app_client, session, "machine-cmd")
    await session.commit()
    await app_client.post(f"/api/v1/agents/{enrolled.agent_id}/commands", json={"type": "ping"})

    body = (await app_client.get("/api/v1/overview")).json()

    assert body["tokens_active"] == 1
    assert body["commands"]["queued"] == 1


async def test_overview_is_limited_to_the_operator_scope(app_client, session) -> None:
    branch = await _group(session, "Филиал Астана")
    other = await _group(session, "Филиал Алматы")
    await session.commit()

    await login_as(app_client, session, username="dash-scoped", scope_group_id=branch.id)
    await enroll_agent(app_client, session, "machine-mine", group_id=branch.id)
    await enroll_agent(app_client, session, "machine-foreign", group_id=other.id)
    await session.commit()

    body = (await app_client.get("/api/v1/overview")).json()

    assert body["agents"]["total"] == 1


async def test_commands_are_listed_across_the_fleet(app_client, session) -> None:
    await login_as(app_client, session, username="cmd-sweeper", role=UserRole.ADMIN)
    first = await enroll_agent(app_client, session, "machine-cmd-1")
    second = await enroll_agent(app_client, session, "machine-cmd-2")
    await session.commit()

    await app_client.post(f"/api/v1/agents/{first.agent_id}/commands", json={"type": "ping"})
    await app_client.post(f"/api/v1/agents/{second.agent_id}/commands", json={"type": "ping"})

    response = await app_client.get("/api/v1/commands")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    # Без имени хоста сводный список команд нечитаем: оператор не держит
    # в голове соответствие идентификаторов машинам.
    assert all(item["hostname"] for item in body["items"])


async def test_commands_are_filtered_by_status(app_client, session) -> None:
    await login_as(app_client, session, username="cmd-status", role=UserRole.ADMIN)
    enrolled = await enroll_agent(app_client, session, "machine-cmd-status")
    await session.commit()

    await app_client.post(f"/api/v1/agents/{enrolled.agent_id}/commands", json={"type": "ping"})

    done = Command(
        agent_id=enrolled.agent_id,
        type=CommandType.PING,
        status=CommandStatus.DONE,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(done)
    await session.commit()

    body = (await app_client.get("/api/v1/commands?status=done")).json()

    assert body["total"] == 1
    assert body["items"][0]["status"] == "done"


async def test_commands_are_limited_to_the_operator_scope(app_client, session) -> None:
    branch = await _group(session, "Филиал Астана")
    other = await _group(session, "Филиал Алматы")
    await session.commit()

    admin_client_user = await login_as(
        app_client, session, username="cmd-scope-admin", role=UserRole.ADMIN
    )
    assert admin_client_user.role is UserRole.ADMIN

    mine = await enroll_agent(app_client, session, "machine-scope-mine", group_id=branch.id)
    foreign = await enroll_agent(app_client, session, "machine-scope-foreign", group_id=other.id)
    await session.commit()

    await app_client.post(f"/api/v1/agents/{mine.agent_id}/commands", json={"type": "ping"})
    await app_client.post(f"/api/v1/agents/{foreign.agent_id}/commands", json={"type": "ping"})

    await login_as(app_client, session, username="cmd-scope-officer", scope_group_id=branch.id)
    body = (await app_client.get("/api/v1/commands")).json()

    assert body["total"] == 1
    assert body["items"][0]["agent_id"] == str(mine.agent_id)
