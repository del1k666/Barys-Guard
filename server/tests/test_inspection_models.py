"""Таблицы инспекции: ограничения целостности."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import ArtifactScan, EventQueue, Incident
from tests.helpers import enroll_agent

SHA = "cd" * 32


async def test_queue_defaults_and_uniqueness(app_client, session) -> None:
    at, event_id = datetime.now(UTC), uuid.uuid4()
    session.add(EventQueue(event_occurred_at=at, event_id=event_id, artifact_sha256=SHA))
    await session.commit()

    row = (await session.scalars(select(EventQueue))).one()
    assert (row.attempts, row.state, row.locked_until) == (0, "pending", None)

    session.add(EventQueue(event_occurred_at=at, event_id=event_id, artifact_sha256=SHA))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_scan_is_unique_per_hash_and_ruleset(app_client, session) -> None:
    def make() -> ArtifactScan:
        return ArtifactScan(artifact_sha256=SHA, ruleset_hash="r1", status="ok", findings={})

    session.add(make())
    await session.commit()
    session.add(make())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_one_open_incident_per_group_key(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "inc-models")
    now = datetime.now(UTC)

    def make(status: str) -> Incident:
        return Incident(
            group_key="k1",
            agent_id=agent.agent_id,
            artifact_sha256=SHA,
            title="t",
            severity="high",
            score=60,
            status=status,
            first_event_at=now,
            last_event_at=now,
            events_count=1,
        )

    session.add(make("open"))
    await session.commit()

    session.add(make("acknowledged"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    first = (await session.scalars(select(Incident))).one()
    first.status = "closed"
    await session.commit()

    session.add(make("open"))
    await session.commit()
    assert len((await session.scalars(select(Incident))).all()) == 2


async def test_events_have_a_nullable_verdict_id(app_client, session) -> None:
    assert (await session.execute(select(Event.verdict_id).limit(1))).first() is None
