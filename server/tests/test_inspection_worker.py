"""Обработка задач воркером."""

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from barysguard.core.config import Settings, get_settings
from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import ArtifactScan, EventQueue, Incident, Verdict
from barysguard.db.session import create_engine_from_url, session_factory
from barysguard.services.inspection.queue import enqueue_for_artifact
from barysguard.services.inspection.rules import seed_rules
from barysguard.services.inspection.worker import run_once
from barysguard.storage.artifact_store import FileArtifactStore
from tests.helpers import enroll_agent

MASTER = b"k" * 32
IIN = "900101300017"
CARD = "4111 1111 1111 1111"
SENSITIVE = f"Строго конфиденциально. ИИН {IIN}, карта {CARD}".encode()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(MASTER).decode())
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def store(settings: Settings) -> FileArtifactStore:
    return FileArtifactStore(settings.artifact_path, MASTER)


async def _artifact(session, store: FileArtifactStore, tmp_path: Path, data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    source = tmp_path / f"{sha[:8]}.src"
    source.write_bytes(data)
    stored = store.put(sha, source)
    session.add(
        Artifact(
            sha256=sha,
            size=len(data),
            storage_path=stored.storage_path,
            key_wrapped=stored.key_wrapped,
            wrap_version=stored.wrap_version,
        )
    )
    await session.flush()
    return sha


async def _event(session, agent_id, sha: str, *, path: str = "E:\\salary.txt") -> Event:
    event = Event(
        occurred_at=datetime.now(UTC),
        event_id=uuid.uuid4(),
        agent_id=agent_id,
        schema_version=1,
        channel="file",
        action="copy",
        actor={"user_name": "PC\\ivanov"},
        subject={"dst_path": path, "volume": {"type": "removable"}},
        artifact_sha256=sha,
    )
    session.add(event)
    await session.flush()
    return event


@pytest.fixture
async def maker(migrated_database_url):
    engine = create_engine_from_url(migrated_database_url)
    yield session_factory(engine)
    await engine.dispose()


async def _prepare(
    session, store, tmp_path, app_client, data: bytes, name: str, *, events: int = 1
):
    await seed_rules(session)
    agent = await enroll_agent(app_client, session, name)
    sha = await _artifact(session, store, tmp_path, data)
    made = [await _event(session, agent.agent_id, sha) for _ in range(events)]
    await enqueue_for_artifact(session, sha)
    await session.commit()
    return agent, sha, made


async def test_sensitive_file_gets_a_flagged_verdict_and_an_incident(
    app_client, session, maker, store, settings, tmp_path
) -> None:
    _, sha, (event,) = await _prepare(session, store, tmp_path, app_client, SENSITIVE, "w-flag")

    processed = await run_once(maker, store, settings)

    assert processed == 1
    await session.refresh(event)
    verdict = (await session.scalars(select(Verdict))).one()
    assert event.verdict_id == verdict.id
    assert (verdict.status, verdict.score, verdict.severity) == ("flagged", 60, "high")
    assert {m["rule_key"] for m in verdict.matches} == {"iin_bin", "card", "markings"}
    incident = (await session.scalars(select(Incident))).one()
    assert incident.events_count == 1 and incident.severity == "high"
    assert (await session.scalars(select(EventQueue))).all() == []
    assert (
        await session.get(Artifact, (await session.scalars(select(Artifact.id))).one())
    ).scan_status == "scanned"


async def test_clean_file_gets_a_clean_verdict_and_no_incident(
    app_client, session, maker, store, settings, tmp_path
) -> None:
    await _prepare(session, store, tmp_path, app_client, b"just a lunch menu", "w-clean")

    await run_once(maker, store, settings)

    verdict = (await session.scalars(select(Verdict))).one()
    assert (verdict.status, verdict.score) == ("clean", 0)
    assert (await session.scalars(select(Incident))).all() == []


async def test_unsupported_file_is_not_inspected_and_not_retried(
    app_client, session, maker, store, settings, tmp_path
) -> None:
    agent = await enroll_agent(app_client, session, "w-unsup")
    await seed_rules(session)
    sha = await _artifact(session, store, tmp_path, b"\xff\xd8\xff photo")
    await _event(session, agent.agent_id, sha, path="E:\\photo.jpg")
    await enqueue_for_artifact(session, sha)
    await session.commit()

    await run_once(maker, store, settings)

    verdict = (await session.scalars(select(Verdict))).one()
    assert (verdict.status, verdict.reason, verdict.score) == ("not_inspected", "unsupported", 0)
    assert (await session.scalars(select(EventQueue))).all() == []
    assert (await session.scalars(select(Incident))).all() == []


async def test_two_events_of_one_document_share_a_single_scan(
    app_client, session, maker, store, settings, tmp_path
) -> None:
    await _prepare(session, store, tmp_path, app_client, SENSITIVE, "w-dedup", events=2)

    assert await run_once(maker, store, settings) == 2

    assert len((await session.scalars(select(ArtifactScan))).all()) == 1
    assert len((await session.scalars(select(Verdict))).all()) == 2
    incident = (await session.scalars(select(Incident))).one()
    assert incident.events_count == 2


async def test_changed_ruleset_scans_again(
    app_client, session, maker, store, settings, tmp_path
) -> None:
    agent, sha, _ = await _prepare(session, store, tmp_path, app_client, SENSITIVE, "w-rules")
    await run_once(maker, store, settings)

    from barysguard.db.models.inspection import Dictionary, DictionaryTerm

    dictionary = (await session.scalars(select(Dictionary))).one()
    session.add(DictionaryTerm(dictionary_id=dictionary.id, term="тайна"))
    await seed_rules(session)
    await _event(session, agent.agent_id, sha)
    await enqueue_for_artifact(session, sha)
    await session.commit()

    await run_once(maker, store, settings)

    assert len((await session.scalars(select(ArtifactScan))).all()) == 2


async def test_results_never_hold_the_full_values(
    app_client, session, maker, store, settings, tmp_path
) -> None:
    await _prepare(session, store, tmp_path, app_client, SENSITIVE, "w-private")
    await run_once(maker, store, settings)

    dump = json.dumps(
        [
            [s.findings for s in (await session.scalars(select(ArtifactScan))).all()],
            [v.matches for v in (await session.scalars(select(Verdict))).all()],
            [i.title for i in (await session.scalars(select(Incident))).all()],
        ],
        ensure_ascii=False,
    )
    assert IIN not in dump and "4111 1111" not in dump and "41111111" not in dump


async def test_a_failing_task_is_retried_then_marked_not_inspected(
    app_client, session, maker, store, settings, tmp_path, monkeypatch
) -> None:
    _, sha, (event,) = await _prepare(session, store, tmp_path, app_client, SENSITIVE, "w-fail")
    store.delete(sha)  # файл исчез из хранилища: чтение будет падать
    monkeypatch.setenv("BG_WORKER_LOCK_SECONDS", "1")
    get_settings.cache_clear()
    quick = get_settings()
    base = datetime.now(UTC)

    from datetime import timedelta

    for step in range(3):
        await run_once(maker, store, quick, now=base + timedelta(seconds=step * 10))
    assert (await session.scalars(select(Verdict))).all() == []

    await run_once(maker, store, quick, now=base + timedelta(seconds=60))

    verdict = (await session.scalars(select(Verdict))).one()
    assert (verdict.status, verdict.reason) == ("not_inspected", "error")
    assert [r.state for r in (await session.scalars(select(EventQueue))).all()] == ["failed"]
    await session.refresh(event)
    assert event.verdict_id == verdict.id


async def test_an_event_with_a_verdict_is_skipped(
    app_client, session, maker, store, settings, tmp_path
) -> None:
    _, _, (event,) = await _prepare(session, store, tmp_path, app_client, SENSITIVE, "w-skip")
    event.verdict_id = uuid.uuid4()
    await session.commit()

    await run_once(maker, store, settings)

    assert (await session.scalars(select(Verdict))).all() == []
    assert (await session.scalars(select(EventQueue))).all() == []
