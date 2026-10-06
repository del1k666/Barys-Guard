"""Демонстрационные артефакты для dev-стенда.

Агенты стенда работают на Linux и содержимого не присылают, поэтому воркеру
нечего инспектировать. Здесь в хранилище кладутся два файла (чувствительный и
чистый) и каждому агенту добавляются события «копирование на USB»; события
проходят тот же разбор и ту же постановку в очередь, что и настоящие.
"""

import hashlib
import json
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.services.events import _parse_line, store_events
from barysguard.storage.artifact_store import FileArtifactStore

_USER = {"user_name": "PC\\ivanov", "user_sid": "S-1-5-21-1-2-3-1001"}
_FLASH = {"type": "removable", "label": "KINGSTON", "serial": "0781-5583"}

DEMO_FILES: tuple[tuple[str, bytes], ...] = (
    (
        "E:\\Зарплаты_июнь.txt",
        "Зарплатная ведомость. Строго конфиденциально.\n"
        "Иванов И.И., ИИН 900101300017, карта 4111 1111 1111 1111\n".encode(),
    ),
    ("E:\\Меню_столовой.txt", "Меню столовой на неделю: суп, котлеты, компот.\n".encode()),
)


@dataclass(frozen=True)
class DemoArtifactsResult:
    agents: int
    inserted: int


async def _ensure_artifact(session: AsyncSession, store: FileArtifactStore, data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    if await session.scalar(select(Artifact.id).where(Artifact.sha256 == sha)) is not None:
        return sha
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "demo.src"
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


async def seed_demo_artifacts(
    session: AsyncSession, store: FileArtifactStore
) -> DemoArtifactsResult:
    """Добавляет каждому не отозванному агенту по событию на демо-файл. Повтор ничего не меняет."""
    shas = [(path, data, await _ensure_artifact(session, store, data)) for path, data in DEMO_FILES]
    agents = (await session.scalars(select(Agent).where(Agent.status != AgentStatus.REVOKED))).all()

    now = datetime.now(UTC)
    inserted = 0
    for agent in agents:
        already = await session.scalar(
            select(Event.event_id)
            .where(Event.agent_id == agent.id, Event.labels["demo_artifact"].astext == "true")
            .limit(1)
        )
        if already is not None:
            continue

        envelopes = []
        for index, (path, data, sha) in enumerate(shas):
            event: dict[str, Any] = {
                "event_id": str(uuid.uuid5(agent.id, f"demo-artifact-{index}")),
                "schema_version": 1,
                "occurred_at": (now - timedelta(minutes=2 + index)).isoformat(),
                "channel": "file",
                "action": "copy",
                "severity_hint": "high",
                "actor": _USER,
                "process": {},
                "subject": {"dst_path": path, "volume": _FLASH, "size_bytes": len(data)},
                "artifact": {"sha256": sha, "size": len(data), "uploaded": False},
                "labels": {"demo_artifact": "true"},
            }
            envelope, reason = _parse_line(json.dumps(event))
            if envelope is None:
                raise ValueError(f"демо-событие {index} не проходит разбор: {reason}")
            envelopes.append(envelope)
        inserted += await store_events(session, agent.id, envelopes, now)

    return DemoArtifactsResult(agents=len(agents), inserted=inserted)
