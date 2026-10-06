"""Воркер инспекции: берёт задачи из очереди, сканирует содержимое, пишет вердикты."""

import asyncio
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from barysguard.core.config import Settings
from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import ArtifactScan
from barysguard.db.session import create_engine_from_url, session_factory
from barysguard.services.inspection.engine import limits_from_settings, scan_bytes
from barysguard.services.inspection.incidents import apply_verdict
from barysguard.services.inspection.queue import (
    QueueTask,
    claim_batch,
    enqueue_missed,
    fail_exhausted,
    finish_task,
)
from barysguard.services.inspection.rules import Ruleset, load_ruleset
from barysguard.services.inspection.scoring import evaluate
from barysguard.services.inspection.verdicts import write_verdict
from barysguard.storage.artifact_store import FileArtifactStore, build_store

logger = logging.getLogger("barysguard.worker")

SWEEP_INTERVAL = 60.0


class WorkerConfigError(Exception):
    """Воркер нельзя запустить: не задан мастер-ключ хранилища."""


async def _read(store: FileArtifactStore, artifact: Artifact) -> bytes:
    def read() -> bytes:
        return b"".join(store.open(artifact.sha256, artifact.key_wrapped))

    return await asyncio.to_thread(read)


async def _scan_for(
    session: AsyncSession,
    store: FileArtifactStore,
    ruleset: Ruleset,
    event: Event,
    settings: Settings,
) -> ArtifactScan:
    sha = event.artifact_sha256
    assert sha is not None
    scan = (
        await session.scalars(
            select(ArtifactScan).where(
                ArtifactScan.artifact_sha256 == sha, ArtifactScan.ruleset_hash == ruleset.hash
            )
        )
    ).one_or_none()
    if scan is not None:
        return scan

    artifact = (await session.scalars(select(Artifact).where(Artifact.sha256 == sha))).one()
    if artifact.size > settings.artifact_max_bytes:
        status, truncated, findings = "too_large", False, {}
    else:
        data = await _read(store, artifact)
        name = str((event.subject or {}).get("dst_path", ""))
        outcome = await asyncio.to_thread(
            scan_bytes, name, data, ruleset.detectors(), limits_from_settings(settings)
        )
        versions = ruleset.version_ids()
        status, truncated = outcome.status, outcome.truncated
        findings = {
            key: {**value, "rule_version_id": versions.get(key, "")}
            for key, value in outcome.findings.items()
        }

    await session.execute(
        pg_insert(ArtifactScan)
        .values(
            artifact_sha256=sha,
            ruleset_hash=ruleset.hash,
            status=status,
            truncated=truncated,
            findings=findings,
        )
        .on_conflict_do_nothing(index_elements=["artifact_sha256", "ruleset_hash"])
    )
    await session.execute(
        update(Artifact)
        .where(Artifact.sha256 == sha)
        .values(scan_status="scanned" if status == "ok" else status)
    )
    return (
        await session.scalars(
            select(ArtifactScan)
            .where(ArtifactScan.artifact_sha256 == sha, ArtifactScan.ruleset_hash == ruleset.hash)
            .execution_options(populate_existing=True)
        )
    ).one()


async def process_task(
    session: AsyncSession,
    store: FileArtifactStore,
    ruleset: Ruleset,
    task: QueueTask,
    settings: Settings,
) -> None:
    """Скан, вердикт и инцидент одной задачи — в одной транзакции (коммитит вызывающий)."""
    event = await session.get(Event, (task.occurred_at, task.event_id))
    if event is None or event.verdict_id is not None or event.artifact_sha256 is None:
        await finish_task(session, task.id)
        return

    scan = await _scan_for(session, store, ruleset, event, settings)
    if scan.status == "ok":
        result = evaluate(scan.findings, ruleset.weights())
        verdict = await write_verdict(
            session,
            occurred_at=event.occurred_at,
            event_id=event.event_id,
            scan_id=scan.id,
            status=result.status,
            reason=None,
            score=result.score,
            severity=result.severity,
            matches=result.matches,
        )
    else:
        verdict = await write_verdict(
            session,
            occurred_at=event.occurred_at,
            event_id=event.event_id,
            scan_id=scan.id,
            status="not_inspected",
            reason=scan.status,
            score=0,
            severity="info",
            matches=[],
        )

    if verdict.status == "flagged" and verdict.score >= settings.incident_min_score:
        await apply_verdict(session, event, verdict)
    await finish_task(session, task.id)


async def _record_failure(session: AsyncSession, task: QueueTask) -> None:
    event = await session.get(Event, (task.occurred_at, task.event_id))
    if event is None or event.verdict_id is not None:
        return
    await write_verdict(
        session,
        occurred_at=task.occurred_at,
        event_id=task.event_id,
        scan_id=None,
        status="not_inspected",
        reason="error",
        score=0,
        severity="info",
        matches=[],
    )


async def run_once(
    maker: async_sessionmaker[AsyncSession],
    store: FileArtifactStore,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """Один проход: закрыть исчерпавшие попытки, забрать пачку, обработать. Возвращает её размер."""
    moment = now or datetime.now(UTC)
    async with maker() as session:
        for exhausted in await fail_exhausted(
            session, max_attempts=settings.worker_max_attempts, now=moment
        ):
            await _record_failure(session, exhausted)
        tasks = await claim_batch(
            session,
            limit=settings.worker_batch,
            lock_seconds=settings.worker_lock_seconds,
            now=moment,
        )
        await session.commit()
    if not tasks:
        return 0

    async with maker() as session:
        ruleset = await load_ruleset(session)

    for task in tasks:
        async with maker() as session:
            try:
                await process_task(session, store, ruleset, task, settings)
                await session.commit()
            except Exception:
                # Задача остаётся в очереди с блокировкой; после её истечения
                # будет повтор, а после исчерпания попыток — вердикт «ошибка».
                await session.rollback()
                logger.exception(
                    "инспекция задачи не удалась",
                    extra={
                        "task_id": task.id,
                        "sha256": task.artifact_sha256,
                        "attempt": task.attempts,
                    },
                )
    return len(tasks)


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    store = build_store(settings)
    if store is None:
        raise WorkerConfigError(
            "Не задан BG_ARTIFACT_MASTER_KEY: воркер не может читать содержимое."
        )

    engine: AsyncEngine = create_engine_from_url(settings.database_url)
    maker = session_factory(engine)
    last_sweep = 0.0
    logger.info("воркер инспекции запущен")
    try:
        while not stop.is_set():
            if time.monotonic() - last_sweep >= SWEEP_INTERVAL:
                async with maker() as session:
                    queued = await enqueue_missed(session, datetime.now(UTC))
                    await session.commit()
                if queued:
                    logger.info("страховка поставила в очередь события", extra={"count": queued})
                last_sweep = time.monotonic()

            processed = await run_once(maker, store, settings)
            if processed == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()
        logger.info("воркер инспекции остановлен")
