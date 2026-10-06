"""GET /api/v1/events."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.user import UserRole
from tests.helpers import enroll_agent, login_as

NDJSON = {"Content-Type": "application/x-ndjson"}


def _event(*, at: datetime, channel: str = "agent", action: str = "start", **extra) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": at.isoformat(),
        "channel": channel,
        "action": action,
        **extra,
    }


async def _send(app_client, agent, *events: dict) -> None:
    body = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
    response = await app_client.post(
        "/gateway/v1/events", headers={**agent.headers, **NDJSON}, content=body
    )
    assert response.status_code == 202, response.text


async def test_events_are_listed_newest_first_with_hostname(app_client, session) -> None:
    await login_as(app_client, session, username="ev-admin", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-list")
    now = datetime.now(UTC)
    await _send(
        app_client,
        agent,
        _event(at=now - timedelta(minutes=2), action="start"),
        _event(at=now - timedelta(minutes=1), action="stop"),
    )

    response = await app_client.get("/api/v1/events")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["action"] for item in items] == ["stop", "start"]
    assert items[0]["hostname"] == "ws-1"
    assert items[0]["agent_id"] == str(agent.agent_id)
    assert items[0]["severity"] == "info"
    assert response.json()["next_cursor"] is None


async def test_filters_by_channel_action_severity_and_time(app_client, session) -> None:
    await login_as(app_client, session, username="ev-filter", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-filter-agent")
    now = datetime.now(UTC)
    await _send(
        app_client,
        agent,
        _event(
            at=now - timedelta(hours=3),
            channel="file",
            action="copy",
            severity_hint="high",
            subject={
                "dst_path": "E:\a.docx",
                "src_path": "C:\a.docx",
                "volume": {"type": "removable"},
            },
        ),
        _event(at=now - timedelta(minutes=5), channel="agent", action="start"),
    )

    by_channel = (await app_client.get("/api/v1/events", params={"channel": "file"})).json()
    by_action = (await app_client.get("/api/v1/events", params={"action": "start"})).json()
    by_severity = (await app_client.get("/api/v1/events", params={"severity": "high"})).json()
    recent = (
        await app_client.get(
            "/api/v1/events", params={"since": (now - timedelta(hours=1)).isoformat()}
        )
    ).json()
    naive_until = (
        await app_client.get(
            "/api/v1/events",
            params={"until": (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()},
        )
    ).json()

    assert [i["channel"] for i in by_channel["items"]] == ["file"]
    assert [i["action"] for i in by_action["items"]] == ["start"]
    assert [i["severity"] for i in by_severity["items"]] == ["high"]
    assert [i["action"] for i in recent["items"]] == ["start"]
    assert [i["action"] for i in naive_until["items"]] == ["copy"]


async def test_filter_by_agent(app_client, session) -> None:
    await login_as(app_client, session, username="ev-agent-filter", role=UserRole.ADMIN)
    first = await enroll_agent(app_client, session, "ev-a1")
    second = await enroll_agent(app_client, session, "ev-a2")
    now = datetime.now(UTC)
    await _send(app_client, first, _event(at=now))
    await _send(app_client, second, _event(at=now))

    response = await app_client.get("/api/v1/events", params={"agent_id": str(second.agent_id)})

    assert [i["agent_id"] for i in response.json()["items"]] == [str(second.agent_id)]


async def test_cursor_pagination_walks_every_event_once(app_client, session) -> None:
    await login_as(app_client, session, username="ev-page", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-page-agent")
    now = datetime.now(UTC)
    await _send(app_client, agent, *[_event(at=now - timedelta(seconds=i)) for i in range(5)])

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await app_client.get("/api/v1/events", params=params)).json()
        seen.extend(item["event_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_bad_cursor_gives_400(app_client, session) -> None:
    await login_as(app_client, session, username="ev-badcursor", role=UserRole.ADMIN)

    response = await app_client.get("/api/v1/events", params={"cursor": "not-a-cursor"})

    assert response.status_code == 400


async def test_operator_sees_only_events_of_their_subtree(app_client, session) -> None:
    mine = AgentGroup(name="Филиал А")
    theirs = AgentGroup(name="Филиал Б")
    session.add_all([mine, theirs])
    await session.flush()
    await login_as(
        app_client, session, username="ev-scoped", role=UserRole.OPERATOR, scope_group_id=mine.id
    )
    near = await enroll_agent(app_client, session, "ev-near", group_id=mine.id)
    far = await enroll_agent(app_client, session, "ev-far", group_id=theirs.id)
    now = datetime.now(UTC)
    await _send(app_client, near, _event(at=now))
    await _send(app_client, far, _event(at=now))

    items = (await app_client.get("/api/v1/events")).json()["items"]

    assert [i["agent_id"] for i in items] == [str(near.agent_id)]


async def test_unauthenticated_request_is_refused(app_client) -> None:
    assert (await app_client.get("/api/v1/events")).status_code == 401


async def test_overview_counts_events_of_the_last_day(app_client, session) -> None:
    await login_as(app_client, session, username="dash-events", role=UserRole.ADMIN)
    enrolled = await enroll_agent(app_client, session, "machine-events")
    now = datetime.now(UTC)
    await _send(app_client, enrolled, _event(at=now), _event(at=now - timedelta(days=3)))

    assert (await app_client.get("/api/v1/overview")).json()["events_24h"] == 1


async def test_event_reports_whether_its_artifact_was_uploaded(app_client, session) -> None:
    from barysguard.db.models.artifact import Artifact

    await login_as(app_client, session, username="ev-art", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-art-agent")
    sha = "cd" * 32
    now = datetime.now(UTC)
    await _send(
        app_client,
        agent,
        _event(
            at=now,
            channel="file",
            action="create",
            subject={"dst_path": "E:\a.txt", "volume": {"type": "removable"}},
            artifact={"sha256": sha, "size": 5, "uploaded": False},
        ),
        _event(at=now - timedelta(minutes=1), action="start"),
    )

    before = (await app_client.get("/api/v1/events")).json()["items"]
    assert [item["artifact_uploaded"] for item in before] == [False, False]

    session.add(Artifact(sha256=sha, size=5, storage_path="x", key_wrapped=b"k" * 40))
    await session.commit()

    after = (await app_client.get("/api/v1/events")).json()["items"]
    assert [item["artifact_uploaded"] for item in after] == [True, False]
