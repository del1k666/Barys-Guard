"""POST /gateway/v1/events."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from tests.helpers import enroll_agent, set_global_config

NDJSON = {"Content-Type": "application/x-ndjson"}


def _event(**overrides) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "channel": "agent",
        "action": "start",
        "subject": {"component": "agent", "detail": "0.1.0"},
    }
    event.update(overrides)
    return event


def _body(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode()


async def _post(app_client, agent, *events: dict, raw: bytes | None = None):
    return await app_client.post(
        "/gateway/v1/events",
        headers={**agent.headers, **NDJSON},
        content=raw if raw is not None else _body(*events),
    )


async def _count(session, agent_id) -> int:
    return (
        await session.execute(
            text("SELECT count(*) FROM events WHERE agent_id = :a"), {"a": agent_id}
        )
    ).scalar_one()


async def test_events_are_accepted_and_stored(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-basic")

    response = await _post(app_client, agent, _event(), _event(action="stop"))

    assert response.status_code == 202, response.text
    assert response.json() == {"accepted": 2, "duplicates": 0, "rejected": []}
    assert await _count(session, agent.agent_id) == 2


async def test_resending_the_same_batch_reports_duplicates(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-dup")
    batch = [_event(), _event()]

    await _post(app_client, agent, *batch)
    again = await _post(app_client, agent, *batch)

    assert again.status_code == 202
    assert again.json()["accepted"] == 0
    assert again.json()["duplicates"] == 2
    assert await _count(session, agent.agent_id) == 2


async def test_same_event_twice_in_one_batch_is_stored_once(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-dup-inline")
    event = _event()

    response = await _post(app_client, agent, event, event)

    assert response.json()["accepted"] == 1
    assert response.json()["duplicates"] == 1


async def test_bad_events_are_rejected_individually(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-reject")

    response = await _post(
        app_client,
        agent,
        _event(),
        _event(schema_version=7),
        _event(channel="telepathy"),
        _event(occurred_at="2026-10-05T10:00:00"),
        _event(labels={"blob": "x" * 70_000}),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 1
    assert [(r["line"], r["reason"]) for r in body["rejected"]] == [
        (2, "unsupported_schema"),
        (3, "invalid_event"),
        (4, "invalid_event"),
        (5, "too_large"),
    ]


async def test_crlf_and_blank_lines_are_accepted(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-crlf")
    raw = (json.dumps(_event()) + "\r\n\r\n" + json.dumps(_event()) + "\r\n").encode()

    response = await _post(app_client, agent, raw=raw)

    assert response.json()["accepted"] == 2


async def test_agent_id_comes_from_the_certificate_not_the_body(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-identity")
    other = await enroll_agent(app_client, session, "ingest-victim")

    await _post(app_client, agent, _event(agent_id=str(other.agent_id)))

    assert await _count(session, agent.agent_id) == 1
    assert await _count(session, other.agent_id) == 0


async def test_unauthenticated_request_is_refused(app_client) -> None:
    response = await app_client.post("/gateway/v1/events", headers=NDJSON, content=_body(_event()))

    assert response.status_code == 403


async def test_too_many_events_gives_413(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-413-count")
    await set_global_config(session, {"transport": {"event_batch_max": 1}})
    await session.commit()

    response = await _post(app_client, agent, _event(), _event())

    assert response.status_code == 413


async def test_oversized_body_gives_413(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-413-bytes")

    response = await _post(app_client, agent, raw=b"x" * (4 * 1024 * 1024 + 1))

    assert response.status_code == 413


async def test_unreadable_body_gives_400(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-400")

    assert (await _post(app_client, agent, raw=b"\xff\xfe\x00")).status_code == 400
    assert (await _post(app_client, agent, raw=b"\n\n")).status_code == 400


async def test_event_with_far_past_time_is_kept_in_default_partition(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-default")
    event = _event(occurred_at="2001-01-01T00:00:00+00:00")

    response = await _post(app_client, agent, event)

    assert response.json()["accepted"] == 1
    table = (
        await session.execute(
            text("SELECT tableoid::regclass::text FROM events WHERE event_id = :id"),
            {"id": uuid.UUID(event["event_id"])},
        )
    ).scalar_one()
    assert table == "events_default"


async def test_unstorable_event_does_not_fail_the_batch(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-unstorable")

    response = await _post(
        app_client,
        agent,
        _event(labels={"clip": "a\u0000b"}),
        _event(occurred_at="9999-12-31T23:59:59-05:00"),
        _event(),
    )

    assert response.status_code == 202, response.text
    assert response.json()["accepted"] == 1
    assert [r["reason"] for r in response.json()["rejected"]] == ["invalid_event"] * 2
