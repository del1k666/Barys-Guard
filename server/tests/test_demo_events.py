"""Демонстрационные события для dev-стенда."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from barysguard.cli import main
from barysguard.db.models.event import Event
from barysguard.services.demo_events import build_demo_events, seed_demo_events
from barysguard.services.events import _parse_line
from tests.helpers import enroll_agent


def test_demo_events_are_valid_envelopes() -> None:
    events = build_demo_events(datetime.now(UTC))

    assert events, "набор не должен быть пустым"
    for event in events:
        envelope, reason = _parse_line(json.dumps(event))
        assert envelope is not None, f"{event['action']}: {reason}"


def test_demo_set_shows_every_severity_and_a_copy_with_source() -> None:
    events = build_demo_events(datetime.now(UTC))

    severities = {event["severity_hint"] for event in events}
    assert {"info", "medium", "high", "critical"} <= severities
    assert any(
        event["channel"] == "file"
        and event["action"] == "copy"
        and event["subject"]["volume"]["type"] == "removable"
        and event["subject"]["src_path"]
        for event in events
    )


async def test_seed_writes_events_for_every_agent_once(app_client, session) -> None:
    await enroll_agent(app_client, session, "demo-1")
    await enroll_agent(app_client, session, "demo-2")

    first = await seed_demo_events(session)
    await session.commit()
    second = await seed_demo_events(session)
    await session.commit()

    assert first.agents >= 2
    assert first.inserted == first.agents * len(build_demo_events(datetime.now(UTC)))
    assert second.inserted == 0

    stored = await session.scalar(
        select(func.count()).select_from(Event).where(Event.labels["demo"].astext == "true")
    )
    assert stored == first.inserted


async def test_seed_without_agents_inserts_nothing(session) -> None:
    result = await seed_demo_events(session)

    assert result.inserted == 0


async def test_cli_seed_refuses_outside_the_stand(
    migrated_database_url, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    monkeypatch.delenv("BG_STAND", raising=False)
    monkeypatch.setattr("sys.argv", ["barysguard-admin", "seed-demo-events"])

    from barysguard.core.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)

    assert exited.value.code == 1
    assert "BG_STAND" in capsys.readouterr().out
