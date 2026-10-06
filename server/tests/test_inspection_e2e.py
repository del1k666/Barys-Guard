"""Сквозной путь: событие и загрузка артефакта через шлюз → воркер → инцидент в API."""

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from barysguard.core.config import get_settings
from barysguard.db.models.inspection import ArtifactScan
from barysguard.db.models.user import UserRole
from barysguard.db.session import create_engine_from_url, session_factory
from barysguard.services.inspection.rules import seed_rules
from barysguard.services.inspection.worker import run_once
from barysguard.storage.artifact_store import build_store
from tests.helpers import enroll_agent, login_as

NDJSON = {"Content-Type": "application/x-ndjson"}
SENSITIVE = (
    "Зарплатная ведомость. Строго конфиденциально. ИИН 900101300017, карта 4111 1111 1111 1111"
).encode()
CLEAN = "Меню столовой на неделю".encode()


@pytest.fixture
def artifact_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    get_settings.cache_clear()


async def _event(client, agent, data: bytes, path: str) -> None:
    event = {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "channel": "file",
        "action": "copy",
        "actor": {"user_name": "PC\\ivanov"},
        "subject": {"dst_path": path, "volume": {"type": "removable"}},
        "artifact": {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "uploaded": False,
        },
    }
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


async def test_copy_to_usb_becomes_an_incident_and_clean_file_does_not(
    app_client, session, migrated_database_url, artifact_env
) -> None:
    await login_as(app_client, session, username="e2e-admin", role=UserRole.ADMIN)
    await seed_rules(session)
    await session.commit()
    agent = await enroll_agent(app_client, session, "e2e-agent")

    # Событие раньше содержимого — и для чувствительного, и для чистого файла.
    await _event(app_client, agent, SENSITIVE, "E:\\salary.txt")
    await _event(app_client, agent, CLEAN, "E:\\menu.txt")
    await _upload(app_client, agent, SENSITIVE)
    await _upload(app_client, agent, CLEAN)
    # Второе копирование того же документа, когда содержимое уже на сервере.
    await _event(app_client, agent, SENSITIVE, "E:\\salary-copy.txt")

    settings = get_settings()
    engine = create_engine_from_url(migrated_database_url)
    try:
        processed = await run_once(session_factory(engine), build_store(settings), settings)
    finally:
        await engine.dispose()
    assert processed == 3

    incidents = (await app_client.get("/api/v1/incidents")).json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "high" and incidents[0]["score"] == 60
    assert incidents[0]["events_count"] == 2
    assert incidents[0]["title"] == "Копирование на USB: ИИН/БИН ×1, карта ×1, гриф ×1"

    verdicts = {
        item["subject"]["dst_path"]: item["verdict"]
        for item in (await app_client.get("/api/v1/events", params={"channel": "file"})).json()[
            "items"
        ]
    }
    assert verdicts["E:\\salary.txt"]["status"] == "flagged"
    assert verdicts["E:\\salary-copy.txt"]["status"] == "flagged"
    assert verdicts["E:\\menu.txt"] == {"status": "clean", "score": 0, "severity": "info"}

    # Одно содержимое — один скан, сколько бы раз его ни копировали.
    scans = (await session.scalars(select(ArtifactScan))).all()
    assert len(scans) == 2
