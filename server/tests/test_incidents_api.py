"""GET/PATCH /api/v1/incidents и поле verdict в /api/v1/events."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident
from barysguard.db.models.user import UserRole
from barysguard.services.inspection.incidents import apply_verdict
from barysguard.services.inspection.verdicts import write_verdict
from tests.helpers import enroll_agent, login_as

SHA = "ab" * 32
MATCHES = [
    {
        "rule_key": "iin_bin",
        "rule_version_id": "v1",
        "count": 2,
        "points": 40,
        "samples": ["**********17"],
        "weight": 20,
        "cap": 5,
        "fragments": [{"before": "ИИН ", "hit": "**********17", "after": " в списке"}],
    }
]


async def _flagged(
    session,
    agent_id,
    *,
    user="PC\\ivanov",
    at=None,
    score=75,
    severity="high",
    matches=MATCHES,
) -> Incident:
    event = Event(
        occurred_at=at or datetime.now(UTC),
        event_id=uuid.uuid4(),
        agent_id=agent_id,
        schema_version=1,
        channel="file",
        action="copy",
        actor={"user_name": user},
        subject={"dst_path": "E:\\salary.xlsx"},
        artifact_sha256=SHA,
    )
    session.add(event)
    await session.flush()
    verdict = await write_verdict(
        session,
        occurred_at=event.occurred_at,
        event_id=event.event_id,
        scan_id=None,
        status="flagged",
        reason=None,
        score=score,
        severity=severity,
        matches=matches,
    )
    incident = await apply_verdict(session, event, verdict)
    await session.commit()
    return incident


async def test_incidents_are_listed_newest_first_with_hostname(app_client, session) -> None:
    await login_as(app_client, session, username="inc-admin", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-list")
    now = datetime.now(UTC)
    older = await _flagged(session, agent.agent_id, user="PC\\a", at=now - timedelta(hours=2))
    newer = await _flagged(session, agent.agent_id, user="PC\\b", at=now - timedelta(hours=1))

    response = await app_client.get("/api/v1/incidents")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [i["id"] for i in items] == [str(newer.id), str(older.id)]
    assert items[0]["hostname"] == "ws-1"
    assert items[0]["status"] == "open" and items[0]["severity"] == "high"
    assert response.json()["next_cursor"] is None


async def test_list_does_not_expose_matches_or_fragments(app_client, session) -> None:
    await login_as(app_client, session, username="inc-nofrag", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-nofrag")
    secret = [
        {
            **MATCHES[0],
            "samples": ["*******SAMPLE-MARK"],
            "fragments": [{"before": "BEFORE-MARK ", "hit": "HIT-MARK", "after": " AFTER-MARK"}],
        }
    ]
    await _flagged(session, agent.agent_id, matches=secret)

    response = await app_client.get("/api/v1/incidents")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items
    for item in items:
        assert "matches" not in item and "fragments" not in item
    for mark in ("SAMPLE-MARK", "BEFORE-MARK", "HIT-MARK", "AFTER-MARK"):
        assert mark not in response.text


async def test_filters_and_pagination(app_client, session) -> None:
    await login_as(app_client, session, username="inc-filter", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-filter")
    now = datetime.now(UTC)
    for index in range(3):
        await _flagged(
            session,
            agent.agent_id,
            user=f"PC\\u{index}",
            at=now - timedelta(minutes=index),
            severity="critical" if index == 0 else "medium",
            score=90 if index == 0 else 30,
        )

    critical = await app_client.get("/api/v1/incidents", params={"severity": "critical"})
    page = await app_client.get("/api/v1/incidents", params={"limit": 2})
    rest = await app_client.get(
        "/api/v1/incidents", params={"limit": 2, "cursor": page.json()["next_cursor"]}
    )

    assert len(critical.json()["items"]) == 1
    assert len(page.json()["items"]) == 2 and page.json()["next_cursor"]
    assert len(rest.json()["items"]) == 1 and rest.json()["next_cursor"] is None
    other = await app_client.get("/api/v1/incidents", params={"status": "closed"})
    assert other.json()["items"] == []


async def test_detail_has_verdict_matches_and_events_without_full_values(
    app_client, session
) -> None:
    await login_as(app_client, session, username="inc-detail", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-detail")
    incident = await _flagged(session, agent.agent_id)

    response = await app_client.get(f"/api/v1/incidents/{incident.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == {"status": "flagged", "score": 75, "severity": "high"}
    assert body["matches"] == [
        {
            "rule_key": "iin_bin",
            "rule_title": "",
            "count": 2,
            "points": 40,
            "samples": ["**********17"],
            "weight": 20,
            "cap": 5,
            "fragments": [{"before": "ИИН ", "hit": "**********17", "after": " в списке"}],
        }
    ]
    assert len(body["events"]) == 1
    assert body["events"][0]["dst_path"] == "E:\\salary.xlsx"
    assert "900101300017" not in response.text


async def test_old_verdict_without_fragments_gives_empty_defaults(app_client, session) -> None:
    await login_as(app_client, session, username="inc-old", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-old")
    old = [
        {"rule_key": "iin_bin", "rule_version_id": "v1", "count": 1, "points": 20, "samples": []}
    ]
    incident = await _flagged(session, agent.agent_id, matches=old)

    body = (await app_client.get(f"/api/v1/incidents/{incident.id}")).json()

    assert body["matches"][0]["fragments"] == []
    assert body["matches"][0]["weight"] == 0 and body["matches"][0]["cap"] == 0


async def test_unknown_incident_is_404(app_client, session) -> None:
    await login_as(app_client, session, username="inc-404", role=UserRole.ADMIN)

    response = await app_client.get(f"/api/v1/incidents/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_operator_sees_only_incidents_of_his_scope(app_client, session) -> None:
    mine = AgentGroup(name="mine")
    foreign = AgentGroup(name="foreign")
    session.add_all([mine, foreign])
    await session.commit()
    await login_as(app_client, session, username="inc-scoped", scope_group_id=mine.id)
    visible = await enroll_agent(app_client, session, "api-vis", group_id=mine.id)
    hidden = await enroll_agent(app_client, session, "api-hid", group_id=foreign.id)
    own = await _flagged(session, visible.agent_id)
    other = await _flagged(session, hidden.agent_id)

    listing = await app_client.get("/api/v1/incidents")
    detail = await app_client.get(f"/api/v1/incidents/{other.id}")
    patch = await app_client.patch(f"/api/v1/incidents/{other.id}", json={"status": "closed"})

    assert [i["id"] for i in listing.json()["items"]] == [str(own.id)]
    assert detail.status_code == 404 and patch.status_code == 404


async def test_status_changes_are_validated_and_audited(app_client, session) -> None:
    await login_as(app_client, session, username="inc-patch", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-patch")
    incident = await _flagged(session, agent.agent_id)
    url = f"/api/v1/incidents/{incident.id}"

    ack = await app_client.patch(url, json={"status": "acknowledged"})
    again = await app_client.patch(url, json={"status": "acknowledged"})
    closed = await app_client.patch(url, json={"status": "closed"})
    reopen = await app_client.patch(url, json={"status": "acknowledged"})
    junk = await app_client.patch(url, json={"status": "open"})

    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
    assert again.status_code == 200
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert reopen.status_code == 409
    assert junk.status_code == 422
    actions = (await session.scalars(select(AuditLog.action))).all()
    assert list(actions).count("incident.update") == 2  # ack и close; повтор не пишется
    await session.refresh(incident)
    assert incident.closed_at is not None
    # Отказ 409 ничего не меняет: инцидент остаётся закрытым с прежним временем закрытия.
    closed_at = incident.closed_at
    assert (await app_client.patch(url, json={"status": "acknowledged"})).status_code == 409
    await session.refresh(incident)
    assert (incident.status, incident.closed_at) == ("closed", closed_at)


async def test_concurrent_close_is_not_overwritten_by_a_patch(app_client, session) -> None:
    import asyncio

    await login_as(app_client, session, username="inc-race", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-race")
    incident_id = (await _flagged(session, agent.agent_id)).id
    url = f"/api/v1/incidents/{incident_id}"

    # Другая транзакция закрывает инцидент и держит блокировку строки.
    locked = (
        await session.scalars(select(Incident).where(Incident.id == incident_id).with_for_update())
    ).one()
    locked.status = "closed"
    closed_at = datetime.now(UTC)
    locked.closed_at = closed_at
    await session.flush()

    patch = asyncio.create_task(app_client.patch(url, json={"status": "acknowledged"}))
    await asyncio.sleep(0.5)
    waited = not patch.done()
    await session.commit()
    response = await asyncio.wait_for(patch, timeout=10)

    assert waited  # PATCH ждёт блокировку, а не читает устаревший статус
    assert response.status_code == 409
    session.expire_all()
    current = await session.get(Incident, incident_id)
    assert current is not None
    assert (current.status, current.closed_at) == ("closed", closed_at)


async def test_events_carry_the_verdict(app_client, session) -> None:
    await login_as(app_client, session, username="inc-ev", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-ev")
    await _flagged(session, agent.agent_id)
    session.add(
        Event(
            occurred_at=datetime.now(UTC) - timedelta(days=1),
            event_id=uuid.uuid4(),
            agent_id=agent.agent_id,
            schema_version=1,
            channel="agent",
            action="start",
        )
    )
    await session.commit()

    items = (await app_client.get("/api/v1/events")).json()["items"]

    by_action = {item["action"]: item for item in items}
    assert by_action["copy"]["verdict"] == {"status": "flagged", "score": 75, "severity": "high"}
    assert by_action["start"]["verdict"] is None
