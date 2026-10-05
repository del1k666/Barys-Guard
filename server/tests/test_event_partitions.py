"""Менеджер партиций таблицы events."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import text

from barysguard.services.event_partitions import ensure_event_partitions
from tests.helpers import enroll_agent


async def test_ensure_creates_three_months_and_is_idempotent(session) -> None:
    # 2031 год не пересекается с разделами, которые миграция создала от текущей даты.
    created = await ensure_event_partitions(session, today=date(2031, 1, 15))

    assert created == ["events_2031_01", "events_2031_02", "events_2031_03"]
    assert await ensure_event_partitions(session, today=date(2031, 1, 15)) == []


async def test_ensure_handles_year_boundary(session) -> None:
    created = await ensure_event_partitions(session, months_ahead=2, today=date(2033, 11, 30))

    assert created == ["events_2033_11", "events_2033_12", "events_2034_01"]


async def test_event_outside_partitions_lands_in_default_and_moves_on_ensure(
    app_client, session
) -> None:
    agent = await enroll_agent(app_client, session, "partition-move")
    event_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO events (event_id, agent_id, schema_version, channel, action, "
            "occurred_at) VALUES (:id, :agent, 1, 'agent', 'start', :at)"
        ),
        {"id": event_id, "agent": agent.agent_id, "at": datetime(2036, 5, 10, tzinfo=UTC)},
    )

    where = text("SELECT tableoid::regclass::text FROM events WHERE event_id = :id")
    assert (await session.execute(where, {"id": event_id})).scalar_one() == "events_default"

    # Раздел на месяц события нельзя просто создать: база откажет, пока в
    # default лежат строки из его диапазона. Менеджер обязан перенести их.
    await ensure_event_partitions(session, today=date(2036, 5, 1))

    assert (await session.execute(where, {"id": event_id})).scalar_one() == "events_2036_05"
    total = (
        await session.execute(
            text("SELECT count(*) FROM events WHERE event_id = :id"), {"id": event_id}
        )
    ).scalar_one()
    assert total == 1


async def test_ensure_also_creates_partitions_for_past_months_found_in_default(
    app_client, session
) -> None:
    # Сервер проработал несколько месяцев без перезапуска: события прошлых
    # месяцев лежат в events_default, и текущий месяц вперёд их не покрывает.
    agent = await enroll_agent(app_client, session, "partition-past")
    event_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO events (event_id, agent_id, schema_version, channel, action, "
            "occurred_at) VALUES (:id, :agent, 1, 'agent', 'start', :at)"
        ),
        {"id": event_id, "agent": agent.agent_id, "at": datetime(2037, 2, 10, tzinfo=UTC)},
    )

    created = await ensure_event_partitions(session, today=date(2037, 5, 1))

    assert "events_2037_02" in created
    where = text("SELECT tableoid::regclass::text FROM events WHERE event_id = :id")
    assert (await session.execute(where, {"id": event_id})).scalar_one() == "events_2037_02"


async def test_rows_far_outside_the_window_stay_in_default(app_client, session) -> None:
    # Часы агента неверны на десятки лет: тысячи разделов под такие события
    # создавать нельзя, они остаются в страховочном.
    agent = await enroll_agent(app_client, session, "partition-far")
    event_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO events (event_id, agent_id, schema_version, channel, action, "
            "occurred_at) VALUES (:id, :agent, 1, 'agent', 'start', :at)"
        ),
        {"id": event_id, "agent": agent.agent_id, "at": datetime(2001, 1, 1, tzinfo=UTC)},
    )

    created = await ensure_event_partitions(session, today=date(2038, 5, 1))

    assert "events_2001_01" not in created
    where = text("SELECT tableoid::regclass::text FROM events WHERE event_id = :id")
    assert (await session.execute(where, {"id": event_id})).scalar_one() == "events_default"
