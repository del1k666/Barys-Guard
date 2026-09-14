import uuid
from datetime import UTC, datetime, timedelta

import pytest

from barysguard.core.errors import CommandNotDelivered
from barysguard.db.models.agent import Agent
from barysguard.db.models.command import CommandStatus, CommandType
from barysguard.services.commands import (
    dequeue_commands,
    expire_stale_commands,
    queue_command,
    record_command_result,
)


async def _agent(session) -> Agent:
    agent = Agent(
        machine_id=f"machine-{uuid.uuid4().hex}",
        hostname="ws-1",
        os="linux",
        os_version="24.04",
        arch="amd64",
        agent_version="0.1.0",
    )
    session.add(agent)
    await session.flush()
    return agent


@pytest.mark.asyncio
async def test_queued_command_has_expiry(session):
    agent = await _agent(session)

    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )

    assert command.status == CommandStatus.QUEUED
    assert command.expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_dequeue_marks_commands_sent(session):
    agent = await _agent(session)
    await queue_command(
        session,
        agent_id=agent.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )

    first = await dequeue_commands(session, agent.id, 50)
    assert [command.status for command in first] == [CommandStatus.SENT]

    # Повторный heartbeat не должен выдать ту же команду второй раз.
    second = await dequeue_commands(session, agent.id, 50)
    assert second == []


@pytest.mark.asyncio
async def test_dequeue_never_crosses_agents(session):
    mine = await _agent(session)
    theirs = await _agent(session)
    await queue_command(
        session,
        agent_id=theirs.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )

    assert await dequeue_commands(session, mine.id, 50) == []


@pytest.mark.asyncio
async def test_expired_command_is_not_delivered(session):
    agent = await _agent(session)
    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    command.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    assert await expire_stale_commands(session, agent.id) == 1
    await session.refresh(command)
    assert command.status == CommandStatus.EXPIRED
    assert await dequeue_commands(session, agent.id, 50) == []


@pytest.mark.asyncio
async def test_dequeue_respects_limit_and_order(session):
    agent = await _agent(session)
    for _ in range(3):
        await queue_command(
            session,
            agent_id=agent.id,
            command_type=CommandType.PING,
            payload={},
            created_by=None,
            ttl_seconds=3600,
        )

    batch = await dequeue_commands(session, agent.id, 2)

    assert len(batch) == 2
    assert batch[0].created_at <= batch[1].created_at


@pytest.mark.asyncio
async def test_result_is_recorded(session):
    agent = await _agent(session)
    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await dequeue_commands(session, agent.id, 50)

    outcome = await record_command_result(
        session,
        agent_id=agent.id,
        command_id=command.id,
        status=CommandStatus.DONE,
        result={"pong": True},
    )

    assert outcome is not None
    updated, accepted = outcome
    assert accepted is True
    assert updated.status == CommandStatus.DONE
    assert updated.result == {"pong": True}
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_repeated_result_does_not_overwrite(session):
    agent = await _agent(session)
    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    await dequeue_commands(session, agent.id, 50)
    await record_command_result(
        session,
        agent_id=agent.id,
        command_id=command.id,
        status=CommandStatus.DONE,
        result={"first": True},
    )

    # Агент вправе повторить запрос после обрыва связи. Повтор не должен
    # затирать сохранённый результат.
    outcome = await record_command_result(
        session,
        agent_id=agent.id,
        command_id=command.id,
        status=CommandStatus.FAILED,
        result={"second": True},
    )

    assert outcome is not None
    again, accepted = outcome
    assert accepted is False
    assert again.status == CommandStatus.DONE
    assert again.result == {"first": True}


@pytest.mark.asyncio
async def test_result_for_foreign_command_is_not_found(session):
    mine = await _agent(session)
    theirs = await _agent(session)
    command = await queue_command(
        session,
        agent_id=theirs.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )

    outcome = await record_command_result(
        session,
        agent_id=mine.id,
        command_id=command.id,
        status=CommandStatus.DONE,
        result={},
    )

    assert outcome is None


@pytest.mark.asyncio
async def test_result_for_undelivered_command_is_rejected(session):
    agent = await _agent(session)
    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )

    with pytest.raises(CommandNotDelivered):
        await record_command_result(
            session,
            agent_id=agent.id,
            command_id=command.id,
            status=CommandStatus.DONE,
            result={},
        )


@pytest.mark.asyncio
async def test_concurrent_dequeue_delivers_each_command_once(migrated_database_url, session):
    """Два heartbeat подряд могут попасть в разные воркеры uvicorn.

    Без SKIP LOCKED второй запрос ждал бы первый, а без FOR UPDATE обе
    транзакции выдали бы агенту одну и ту же команду дважды.
    """
    import asyncio

    from barysguard.db.session import create_engine_from_url, session_factory

    agent = await _agent(session)
    for _ in range(4):
        await queue_command(
            session,
            agent_id=agent.id,
            command_type=CommandType.PING,
            payload={},
            created_by=None,
            ttl_seconds=3600,
        )
    await session.commit()

    engine = create_engine_from_url(migrated_database_url)
    maker = session_factory(engine)

    async def take() -> list[uuid.UUID]:
        async with maker() as other:
            commands = await dequeue_commands(other, agent.id, 4)
            identifiers = [command.id for command in commands]
            await other.commit()
            return identifiers

    try:
        first, second = await asyncio.gather(take(), take())
    finally:
        await engine.dispose()

    assert set(first) & set(second) == set()
    assert len(first) + len(second) == 4
