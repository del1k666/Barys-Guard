import uuid

import pytest

from barysguard.db.models.command import CommandStatus, CommandType
from barysguard.services.commands import queue_command
from tests.helpers import enroll_agent
from tests.test_heartbeat_endpoint import _body as heartbeat_body


@pytest.mark.asyncio
async def test_result_is_accepted_after_delivery(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-happy")
    command = await queue_command(
        session,
        agent_id=enrolled.agent_id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await session.commit()
    await app_client.post("/gateway/v1/heartbeat", headers=enrolled.headers, json=heartbeat_body())

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {"pong": True}},
    )

    assert response.status_code == 202
    await session.refresh(command)
    assert command.status == CommandStatus.DONE
    assert command.result == {"pong": True}


@pytest.mark.asyncio
async def test_repeated_result_returns_200_and_keeps_first(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-repeat")
    command = await queue_command(
        session,
        agent_id=enrolled.agent_id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await session.commit()
    await app_client.post("/gateway/v1/heartbeat", headers=enrolled.headers, json=heartbeat_body())
    await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {"first": True}},
    )

    repeat = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "failed", "result": {"second": True}},
    )

    assert repeat.status_code == 200
    await session.refresh(command)
    assert command.status == CommandStatus.DONE
    assert command.result == {"first": True}


@pytest.mark.asyncio
async def test_result_for_foreign_command_is_404(app_client, session):
    # 403 позволил бы перебором идентификаторов выяснять, какие команды есть.
    mine = await enroll_agent(app_client, session, "result-mine")
    theirs = await enroll_agent(app_client, session, "result-theirs")
    command = await queue_command(
        session,
        agent_id=theirs.agent_id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await session.commit()

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=mine.headers,
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_result_for_unknown_command_is_404(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-unknown")

    response = await app_client.post(
        f"/gateway/v1/commands/{uuid.uuid4()}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_result_before_delivery_is_409(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-undelivered")
    command = await queue_command(
        session,
        agent_id=enrolled.agent_id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await session.commit()

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_oversized_result_is_rejected(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-oversized")
    command = await queue_command(
        session,
        agent_id=enrolled.agent_id,
        command_type=CommandType.COLLECT_DIAGNOSTICS,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await session.commit()
    await app_client.post("/gateway/v1/heartbeat", headers=enrolled.headers, json=heartbeat_body())

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {"blob": "x" * 70000}},
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_result_requires_client_certificate(app_client):
    response = await app_client.post(
        f"/gateway/v1/commands/{uuid.uuid4()}/result",
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 403
