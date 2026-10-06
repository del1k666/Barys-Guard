"""Постановка в очередь и захват задач."""

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from barysguard.core.config import get_settings
from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import EventQueue
from barysguard.services.inspection.queue import (
    claim_batch,
    enqueue_for_artifact,
    enqueue_missed,
    enqueue_new_events,
    fail_exhausted,
    finish_task,
)
from tests.helpers import enroll_agent

SHA = "ef" * 32
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


async def _event(session, agent_id, *, sha: str | None = SHA, verdict: uuid.UUID | None = None):
    event = Event(
        occurred_at=datetime.now(UTC),
        event_id=uuid.uuid4(),
        agent_id=agent_id,
        schema_version=1,
        channel="file",
        action="copy",
        artifact_sha256=sha,
        verdict_id=verdict,
    )
    session.add(event)
    await session.flush()
    return event


def _artifact(sha: str = SHA, first_seen: datetime | None = None) -> Artifact:
    artifact = Artifact(sha256=sha, size=5, storage_path="x", key_wrapped=b"k" * 40)
    if first_seen is not None:
        artifact.first_seen_at = first_seen
    return artifact


async def _queued(session) -> list[EventQueue]:
    return list((await session.scalars(select(EventQueue).order_by(EventQueue.id))).all())


async def test_enqueue_for_artifact_takes_events_without_verdict(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-art")
    waiting = await _event(session, agent.agent_id)
    await _event(session, agent.agent_id, verdict=uuid.uuid4())
    await _event(session, agent.agent_id, sha="aa" * 32)
    await _event(session, agent.agent_id, sha=None)

    assert await enqueue_for_artifact(session, SHA) == 1
    assert await enqueue_for_artifact(session, SHA) == 0  # идемпотентно

    rows = await _queued(session)
    assert [(r.event_id, r.artifact_sha256) for r in rows] == [(waiting.event_id, SHA)]


async def test_new_events_are_queued_only_when_the_artifact_exists(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-new")
    known = await _event(session, agent.agent_id)
    unknown = await _event(session, agent.agent_id, sha="bb" * 32)
    keys = [(known.occurred_at, known.event_id), (unknown.occurred_at, unknown.event_id)]

    assert await enqueue_new_events(session, keys) == 0  # артефакта ещё нет

    session.add(_artifact())
    await session.flush()
    assert await enqueue_new_events(session, keys) == 1

    assert [r.event_id for r in await _queued(session)] == [known.event_id]
    assert await enqueue_new_events(session, []) == 0


async def test_missed_sweep_covers_only_recent_artifacts(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-miss")
    old_sha = "cc" * 32
    session.add_all(
        [
            _artifact(first_seen=NOW - timedelta(hours=1)),
            _artifact(old_sha, NOW - timedelta(days=3)),
        ]
    )
    fresh = await _event(session, agent.agent_id)
    await _event(session, agent.agent_id, sha=old_sha)
    await session.flush()

    assert await enqueue_missed(session, NOW) == 1
    assert [r.event_id for r in await _queued(session)] == [fresh.event_id]
    assert await enqueue_missed(session, NOW) == 0


async def test_claim_locks_tasks_and_skips_locked_ones(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-claim")
    session.add(_artifact())
    for _ in range(3):
        await _event(session, agent.agent_id)
    await enqueue_for_artifact(session, SHA)

    first = await claim_batch(session, limit=2, lock_seconds=300, now=NOW)
    second = await claim_batch(session, limit=5, lock_seconds=300, now=NOW)
    later = await claim_batch(session, limit=5, lock_seconds=300, now=NOW + timedelta(seconds=301))

    assert len(first) == 2 and all(t.attempts == 1 for t in first)
    assert len(second) == 1  # замкнутые не выдаются повторно
    assert len(later) == 3  # срок блокировки истёк
    assert sorted(t.attempts for t in later) == [2, 2, 2]


async def test_finish_removes_the_task(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-fin")
    await _event(session, agent.agent_id)
    await enqueue_for_artifact(session, SHA)
    (task,) = await claim_batch(session, limit=1, lock_seconds=60, now=NOW)

    await finish_task(session, task.id)

    assert await _queued(session) == []


async def test_exhausted_tasks_are_marked_failed_and_returned(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-fail")
    await _event(session, agent.agent_id)
    await enqueue_for_artifact(session, SHA)
    for step in range(3):
        await claim_batch(session, limit=1, lock_seconds=60, now=NOW + timedelta(minutes=step * 2))

    # Третья попытка ещё держит блокировку: не провал.
    assert (
        await fail_exhausted(session, max_attempts=3, now=NOW + timedelta(minutes=4, seconds=30))
        == []
    )

    failed = await fail_exhausted(session, max_attempts=3, now=NOW + timedelta(minutes=10))

    assert len(failed) == 1 and failed[0].attempts == 3
    assert [r.state for r in await _queued(session)] == ["failed"]
    assert await claim_batch(session, limit=5, lock_seconds=60, now=NOW + timedelta(hours=1)) == []


NDJSON = {"Content-Type": "application/x-ndjson"}


@pytest.fixture
def artifact_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(root))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    get_settings.cache_clear()
    return root


def _file_event(sha: str, size: int) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "channel": "file",
        "action": "copy",
        "subject": {"dst_path": "E:\\a.txt", "volume": {"type": "removable"}},
        "artifact": {"sha256": sha, "size": size, "uploaded": False},
    }


async def _send(client, agent, event: dict) -> None:
    response = await client.post(
        "/gateway/v1/events",
        headers={**agent.headers, **NDJSON},
        content=(json.dumps(event) + "\n").encode(),
    )
    assert response.status_code == 202, response.text


async def _upload(client, agent, data: bytes) -> None:
    sha = hashlib.sha256(data).hexdigest()
    opened = await client.post(
        "/gateway/v1/artifacts", headers=agent.headers, json={"sha256": sha, "size": len(data)}
    )
    assert opened.status_code in (200, 201), opened.text
    if opened.json()["status"] == "exists":
        return
    done = await client.put(
        f"/gateway/v1/artifacts/{opened.json()['upload_id']}",
        headers={**agent.headers, "X-Offset": "0", "Content-Type": "application/octet-stream"},
        content=data,
    )
    assert done.status_code == 201, done.text


async def test_event_before_the_artifact_is_queued_when_the_upload_completes(
    app_client, session, artifact_env
) -> None:
    agent = await enroll_agent(app_client, session, "q-order-1")
    data = b"first the event, then the bytes"
    sha = hashlib.sha256(data).hexdigest()

    await _send(app_client, agent, _file_event(sha, len(data)))
    assert await _queued(session) == []  # содержимого ещё нет

    await _upload(app_client, agent, data)

    assert [r.artifact_sha256 for r in await _queued(session)] == [sha]


async def test_event_after_the_artifact_is_queued_on_arrival(
    app_client, session, artifact_env
) -> None:
    agent = await enroll_agent(app_client, session, "q-order-2")
    data = b"first the bytes, then the event"
    sha = hashlib.sha256(data).hexdigest()

    await _upload(app_client, agent, data)
    assert await _queued(session) == []  # событий ещё нет

    await _send(app_client, agent, _file_event(sha, len(data)))
    await _send(app_client, agent, _file_event(sha, len(data)))

    assert len(await _queued(session)) == 2
