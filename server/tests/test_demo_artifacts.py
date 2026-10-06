"""Демо-артефакты стенда: чувствительный и чистый файл на каждого агента."""

import base64
from pathlib import Path

import pytest
from sqlalchemy import select

from barysguard.core.config import get_settings
from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import EventQueue
from barysguard.services.demo_artifacts import seed_demo_artifacts
from barysguard.storage.artifact_store import build_store
from tests.helpers import enroll_agent


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    get_settings.cache_clear()
    return build_store(get_settings())


async def test_seed_stores_two_artifacts_and_queues_events_idempotently(
    app_client, session, store
) -> None:
    await enroll_agent(app_client, session, "demo-art-1")
    await enroll_agent(app_client, session, "demo-art-2")

    first = await seed_demo_artifacts(session, store)
    await session.commit()
    again = await seed_demo_artifacts(session, store)
    await session.commit()

    assert (first.agents, first.inserted) == (2, 4)
    assert again.inserted == 0
    assert len((await session.scalars(select(Artifact))).all()) == 2
    events = (
        await session.scalars(select(Event).where(Event.labels["demo_artifact"].astext == "true"))
    ).all()
    assert len(events) == 4
    assert len((await session.scalars(select(EventQueue))).all()) == 4
