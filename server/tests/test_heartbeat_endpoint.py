from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.models.certificate import AgentCertificate
from barysguard.db.models.command import CommandType
from barysguard.services.commands import queue_command
from tests.helpers import enroll_agent


def _body(**overrides) -> dict:
    payload = {
        "agent_version": "0.1.0",
        "config_version": 0,
        "sent_at": datetime.now(UTC).isoformat(),
        "buffered_events": 0,
        "buffer_bytes": 0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_heartbeat_returns_server_time_and_version(app_client, session):
    agent = await enroll_agent(app_client, session, "hb-basic")

    response = await app_client.post("/gateway/v1/heartbeat", headers=agent.headers, json=_body())

    assert response.status_code == 200
    body = response.json()
    assert body["config_version"] > 0
    assert body["heartbeat_interval_seconds"] == 30
    assert body["commands"] == []


@pytest.mark.asyncio
async def test_heartbeat_updates_agent_record(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-record")

    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body(agent_version="0.2.0")
    )

    agent = await session.get(Agent, enrolled.agent_id)
    await session.refresh(agent)
    assert agent.last_heartbeat_at is not None
    assert agent.agent_version == "0.2.0"
    assert agent.status == AgentStatus.ACTIVE


@pytest.mark.asyncio
async def test_clock_skew_is_recorded_in_both_directions(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-skew")

    ahead = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body(sent_at=ahead)
    )
    agent = await session.get(Agent, enrolled.agent_id)
    await session.refresh(agent)
    assert agent.clock_skew_ms > 20_000

    behind = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body(sent_at=behind)
    )
    await session.refresh(agent)
    assert agent.clock_skew_ms < -20_000


@pytest.mark.asyncio
async def test_reported_config_version_does_not_affect_server_answer(app_client, session):
    # Версию считает сервер. Присланное агентом значение только записывается.
    agent = await enroll_agent(app_client, session, "hb-forged-version")

    honest = await app_client.post("/gateway/v1/heartbeat", headers=agent.headers, json=_body())
    forged = await app_client.post(
        "/gateway/v1/heartbeat", headers=agent.headers, json=_body(config_version=123456)
    )

    assert honest.json()["config_version"] == forged.json()["config_version"]


@pytest.mark.asyncio
async def test_queued_command_is_delivered_once(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-command-once")
    await queue_command(
        session,
        agent_id=enrolled.agent_id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await session.commit()

    first = await app_client.post("/gateway/v1/heartbeat", headers=enrolled.headers, json=_body())
    assert [command["type"] for command in first.json()["commands"]] == ["ping"]

    second = await app_client.post("/gateway/v1/heartbeat", headers=enrolled.headers, json=_body())
    assert second.json()["commands"] == []


@pytest.mark.asyncio
async def test_expired_command_is_not_delivered(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-command-expired")
    command = await queue_command(
        session,
        agent_id=enrolled.agent_id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    command.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body()
    )

    assert response.json()["commands"] == []


@pytest.mark.asyncio
async def test_foreign_command_is_not_delivered(app_client, session):
    mine = await enroll_agent(app_client, session, "hb-command-mine")
    theirs = await enroll_agent(app_client, session, "hb-command-theirs")
    await queue_command(
        session,
        agent_id=theirs.agent_id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await session.commit()

    response = await app_client.post("/gateway/v1/heartbeat", headers=mine.headers, json=_body())

    assert response.json()["commands"] == []


@pytest.mark.asyncio
async def test_revoked_certificate_cannot_heartbeat(app_client, session):
    # Отзыв обязан прекращать обслуживание немедленно, а не к следующему циклу.
    enrolled = await enroll_agent(app_client, session, "hb-revoked")
    await session.execute(
        update(AgentCertificate)
        .where(AgentCertificate.serial == enrolled.serial)
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body()
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_heartbeat_requires_client_certificate(app_client):
    response = await app_client.post("/gateway/v1/heartbeat", json=_body())

    assert response.status_code == 403
