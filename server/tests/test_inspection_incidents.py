"""Запись вердиктов и группировка инцидентов."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident, IncidentEvent, Verdict
from barysguard.services.inspection.incidents import apply_verdict, build_title
from barysguard.services.inspection.verdicts import write_verdict
from tests.helpers import enroll_agent

SHA = "ab" * 32
MATCHES = [
    {
        "rule_key": "iin_bin",
        "rule_version_id": "v1",
        "count": 3,
        "points": 60,
        "samples": ["**********17"],
    },
    {
        "rule_key": "markings",
        "rule_version_id": "v3",
        "count": 1,
        "points": 15,
        "samples": ["конфиденциально"],
    },
]


async def _event(session, agent_id, *, user: str | None = "PC\\ivanov", sha: str = SHA, at=None):
    event = Event(
        occurred_at=at or datetime.now(UTC),
        event_id=uuid.uuid4(),
        agent_id=agent_id,
        schema_version=1,
        channel="file",
        action="copy",
        actor={"user_name": user} if user else {},
        artifact_sha256=sha,
    )
    session.add(event)
    await session.flush()
    return event


async def _verdict(session, event: Event, *, score: int = 75, severity: str = "high") -> Verdict:
    return await write_verdict(
        session,
        occurred_at=event.occurred_at,
        event_id=event.event_id,
        scan_id=None,
        status="flagged",
        reason=None,
        score=score,
        severity=severity,
        matches=MATCHES,
    )


def test_title_names_the_action_and_the_rules() -> None:
    assert build_title("copy", MATCHES) == "Копирование на USB: ИИН/БИН ×3, гриф ×1"
    assert build_title("scan", MATCHES).startswith("Файловое событие:")


def test_title_order_is_canonical_regardless_of_match_order() -> None:
    shuffled = [
        {"rule_key": "markings", "count": 1},
        {"rule_key": "card", "count": 2},
        {"rule_key": "iin_bin", "count": 3},
    ]
    assert build_title("copy", shuffled) == "Копирование на USB: ИИН/БИН ×3, карта ×2, гриф ×1"


async def test_write_verdict_links_the_event(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "v-link")
    event = await _event(session, agent.agent_id)

    verdict = await _verdict(session, event)
    await session.refresh(event)

    assert event.verdict_id == verdict.id
    assert (verdict.status, verdict.score, verdict.severity) == ("flagged", 75, "high")


async def test_first_flagged_event_opens_an_incident(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-open")
    event = await _event(session, agent.agent_id)
    verdict = await _verdict(session, event)

    incident = await apply_verdict(session, event, verdict)

    assert incident.status == "open" and incident.events_count == 1
    assert (incident.score, incident.severity) == (75, "high")
    assert incident.title == "Копирование на USB: ИИН/БИН ×3, гриф ×1"
    assert incident.group_key == f"{agent.agent_id}|PC\\ivanov|{SHA}"
    links = (await session.scalars(select(IncidentEvent))).all()
    assert [(link.incident_id, link.event_id) for link in links] == [(incident.id, event.event_id)]


async def test_repeat_copies_from_one_machine_share_an_incident(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-same")
    base = datetime.now(UTC)
    first = await _event(session, agent.agent_id, at=base)
    second = await _event(session, agent.agent_id, at=base + timedelta(minutes=5))

    one = await apply_verdict(
        session, first, await _verdict(session, first, score=40, severity="medium")
    )
    two = await apply_verdict(
        session, second, await _verdict(session, second, score=75, severity="high")
    )
    await session.flush()

    assert one.id == two.id
    assert len((await session.scalars(select(Incident))).all()) == 1
    assert two.events_count == 2
    assert (two.score, two.severity) == (75, "high")  # максимум
    assert two.first_event_at == base and two.last_event_at == base + timedelta(minutes=5)


async def test_a_lower_score_does_not_lower_the_incident(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-max")
    first = await _event(session, agent.agent_id)
    second = await _event(session, agent.agent_id)
    await apply_verdict(
        session, first, await _verdict(session, first, score=90, severity="critical")
    )

    incident = await apply_verdict(
        session, second, await _verdict(session, second, score=30, severity="medium")
    )

    assert (incident.score, incident.severity) == (90, "critical")


async def test_other_user_machine_or_document_gets_its_own_incident(app_client, session) -> None:
    one = await enroll_agent(app_client, session, "i-m1")
    two = await enroll_agent(app_client, session, "i-m2")
    cases = [
        await _event(session, one.agent_id),
        await _event(session, one.agent_id, user="PC\\petrov"),
        await _event(session, one.agent_id, sha="cd" * 32),
        await _event(session, one.agent_id, user=None),
        await _event(session, two.agent_id),
    ]

    for event in cases:
        await apply_verdict(session, event, await _verdict(session, event))
    await session.flush()

    assert len((await session.scalars(select(Incident))).all()) == 5
    unknown = (
        await session.scalars(select(Incident).where(Incident.group_key.like("%|unknown|%")))
    ).all()
    assert len(unknown) == 1


async def test_a_closed_incident_is_not_reopened(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-closed")
    first = await _event(session, agent.agent_id)
    second = await _event(session, agent.agent_id)
    incident = await apply_verdict(session, first, await _verdict(session, first))
    incident.status = "closed"
    await session.flush()

    fresh = await apply_verdict(session, second, await _verdict(session, second))

    assert fresh.id != incident.id and fresh.status == "open"


async def test_the_same_event_is_not_counted_twice(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-dup")
    event = await _event(session, agent.agent_id)
    verdict = await _verdict(session, event)

    await apply_verdict(session, event, verdict)
    incident = await apply_verdict(session, event, verdict)

    assert incident.events_count == 1


def test_title_uses_the_rule_title_for_custom_rules() -> None:
    matches = [
        {"rule_key": "iin_bin", "rule_title": "ИИН/БИН (Казахстан)", "count": 1},
        {"rule_key": "custom_aa11bb22", "rule_title": "Номер договора", "count": 2},
    ]

    assert build_title("copy", matches) == "Копирование на USB: ИИН/БИН ×1, Номер договора ×2"


def test_title_falls_back_to_the_key_without_a_title() -> None:
    assert (
        build_title("copy", [{"rule_key": "custom_zz", "count": 1}])
        == "Копирование на USB: custom_zz ×1"
    )
