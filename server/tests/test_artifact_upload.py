# server/tests/test_artifact_upload.py
"""POST/PUT /gateway/v1/artifacts."""

import asyncio
import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import get_settings
from barysguard.db.models.artifact import Artifact, UploadSession
from barysguard.services.artifacts import purge_expired_sessions
from barysguard.storage.artifact_store import FileArtifactStore
from tests.helpers import enroll_agent

MASTER = b"k" * 32
CHUNK = 1024


@pytest.fixture
def artifact_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Окружение хранилища. Сбрасывает кеш настроек сам: app_client мог закешировать их раньше."""
    root = tmp_path / "artifacts"
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(root))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(MASTER).decode())
    monkeypatch.setenv("BG_ARTIFACT_CHUNK_BYTES", str(CHUNK))
    monkeypatch.setenv("BG_ARTIFACT_MAX_BYTES", "8192")
    get_settings.cache_clear()
    return root


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _open(client, agent, data: bytes):
    return await client.post(
        "/gateway/v1/artifacts",
        headers=agent.headers,
        json={"sha256": _sha(data), "size": len(data)},
    )


async def _put(client, agent, upload_id: str, offset: int, data: bytes):
    return await client.put(
        f"/gateway/v1/artifacts/{upload_id}",
        headers={
            **agent.headers,
            "X-Offset": str(offset),
            "Content-Type": "application/octet-stream",
        },
        content=data,
    )


async def _send_chunks(client, agent, upload_id: str, data: bytes):
    offset = 0
    while True:
        last = await _put(client, agent, upload_id, offset, data[offset : offset + CHUNK])
        assert last.status_code in (201, 202), last.text
        offset += len(data[offset : offset + CHUNK])
        if last.status_code == 201:
            return last


async def _upload_all(client, agent, data: bytes):
    opened = await _open(client, agent, data)
    assert opened.status_code == 201, opened.text
    return await _send_chunks(client, agent, opened.json()["upload_id"], data)


async def test_upload_in_chunks_is_stored_encrypted(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-1")
    marker = b"4111 1111 1111 1111 "
    data = marker * 150  # 3000 байт: три чанка

    done = await _upload_all(app_client, agent, data)

    assert done.json() == {"received_bytes": len(data), "status": "complete"}
    row = (await session.scalars(select(Artifact))).one()
    assert row.sha256 == _sha(data) and row.size == len(data)
    assert row.first_agent_id == agent.agent_id

    sealed = (artifact_env / row.storage_path).read_bytes()
    assert marker not in sealed
    store = FileArtifactStore(artifact_env, MASTER)
    assert b"".join(store.open(row.sha256, row.key_wrapped)) == data


async def test_known_artifact_is_not_transferred_again(app_client, session, artifact_env) -> None:
    first = await enroll_agent(app_client, session, "up-a")
    second = await enroll_agent(app_client, session, "up-b")
    data = b"shared document " * 40
    await _upload_all(app_client, first, data)

    response = await _open(app_client, second, data)

    assert response.status_code == 200
    assert response.json()["status"] == "exists"
    row = (await session.scalars(select(Artifact))).one()
    await session.refresh(row)
    assert row.ref_count == 2


async def test_interrupted_upload_can_be_resumed(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-resume")
    data = bytes(range(256)) * 12  # 3072 байта
    opened = await _open(app_client, agent, data)
    upload_id = opened.json()["upload_id"]
    first = await _put(app_client, agent, upload_id, 0, data[:CHUNK])
    assert first.status_code == 202 and first.json()["received_bytes"] == CHUNK

    again = await _open(app_client, agent, data)

    assert again.status_code == 201
    assert again.json()["upload_id"] == upload_id
    assert again.json()["received_bytes"] == CHUNK
    assert again.json()["chunk_size"] == CHUNK


async def test_wrong_offset_returns_the_real_one(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-offset")
    data = b"a" * 2048
    upload_id = (await _open(app_client, agent, data)).json()["upload_id"]
    await _put(app_client, agent, upload_id, 0, data[:CHUNK])

    response = await _put(app_client, agent, upload_id, 0, data[:CHUNK])

    assert response.status_code == 409
    assert response.json() == {"received_bytes": CHUNK}


async def test_wrong_hash_resets_the_session(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-hash")
    claimed = b"b" * 100
    upload_id = (await _open(app_client, agent, claimed)).json()["upload_id"]

    response = await _put(app_client, agent, upload_id, 0, b"c" * 100)

    assert response.status_code == 422
    assert (await _put(app_client, agent, upload_id, 0, claimed)).status_code == 404
    assert (await session.scalars(select(Artifact))).all() == []


async def test_session_of_another_agent_is_invisible(app_client, session, artifact_env) -> None:
    owner = await enroll_agent(app_client, session, "up-own")
    stranger = await enroll_agent(app_client, session, "up-str")
    data = b"d" * 100
    upload_id = (await _open(app_client, owner, data)).json()["upload_id"]

    response = await _put(app_client, stranger, upload_id, 0, data)

    assert response.status_code == 404


async def test_oversized_artifact_is_refused(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-big")

    response = await app_client.post(
        "/gateway/v1/artifacts",
        headers=agent.headers,
        json={"sha256": "e" * 64, "size": 8193},
    )

    assert response.status_code == 413


async def test_chunk_larger_than_the_limit_is_refused(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-chunk")
    data = b"f" * 4096
    upload_id = (await _open(app_client, agent, data)).json()["upload_id"]

    response = await _put(app_client, agent, upload_id, 0, data[: CHUNK + 1])

    assert response.status_code == 413


async def test_data_beyond_the_declared_size_is_refused(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-over")
    data = b"g" * 100
    upload_id = (await _open(app_client, agent, data)).json()["upload_id"]

    response = await _put(app_client, agent, upload_id, 0, data + b"extra")

    assert response.status_code == 400


async def test_empty_file_completes_with_one_empty_chunk(app_client, session, artifact_env) -> None:
    agent = await enroll_agent(app_client, session, "up-empty")
    opened = await _open(app_client, agent, b"")
    assert opened.status_code == 201

    done = await _put(app_client, agent, opened.json()["upload_id"], 0, b"")

    assert done.status_code == 201
    row = (await session.scalars(select(Artifact))).one()
    assert row.size == 0
    store = FileArtifactStore(artifact_env, MASTER)
    assert b"".join(store.open(row.sha256, row.key_wrapped)) == b""


async def test_concurrent_uploads_of_one_file_keep_a_single_readable_artifact(
    app_client, session, artifact_env
) -> None:
    first = await enroll_agent(app_client, session, "up-c1")
    second = await enroll_agent(app_client, session, "up-c2")
    data = b"same bytes " * 200

    # Обе сессии открываем заранее: иначе второй POST мог бы прийти после
    # завершения первой загрузки и получить exists вместо сессии.
    ids = [(await _open(app_client, a, data)).json()["upload_id"] for a in (first, second)]
    results = await asyncio.gather(
        _send_chunks(app_client, first, ids[0], data),
        _send_chunks(app_client, second, ids[1], data),
    )

    assert all(r.status_code == 201 for r in results)
    rows = (await session.scalars(select(Artifact))).all()
    assert len(rows) == 1
    await session.refresh(rows[0])
    assert rows[0].ref_count == 2
    store = FileArtifactStore(artifact_env, MASTER)
    assert b"".join(store.open(rows[0].sha256, rows[0].key_wrapped)) == data


async def test_storage_without_master_key_answers_503(
    app_client, session, monkeypatch, tmp_path
) -> None:
    agent = await enroll_agent(app_client, session, "up-nokey")
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", "")
    get_settings.cache_clear()

    response = await _open(app_client, agent, b"x")

    assert response.status_code == 503


async def test_complete_is_answered_only_after_the_commit(
    app_client, session, artifact_env, monkeypatch
) -> None:
    """201 «complete» уходит только после фиксации строки артефакта.

    Иначе сбой commit после ответа терял бы содержимое: агент уже удалил
    свою копию, а ключ зашифрованного файла в БД так и не попал.
    """
    agent = await enroll_agent(app_client, session, "up-commit")
    data = bytes(range(250)) * 10  # 2500 байт: три чанка
    upload_id = (await _open(app_client, agent, data)).json()["upload_id"]
    last = 2 * CHUNK
    for offset in (0, CHUNK):
        part = await _put(app_client, agent, upload_id, offset, data[offset : offset + CHUNK])
        assert part.status_code == 202, part.text

    original_commit = AsyncSession.commit
    failures = {"left": 1}

    async def flaky_commit(self: AsyncSession) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise ConnectionError("база недоступна")
        await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
    # Клиент, который видит ответ приложения, а не исключение из него:
    # иначе нельзя отличить «сначала 201, потом сбой» от честной ошибки.
    transport = ASGITransport(app=app_client._transport.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw:
        failed = await _put(raw, agent, upload_id, last, data[last:])

    assert failed.status_code >= 500
    assert failures["left"] == 0
    assert (await session.scalars(select(Artifact))).all() == []

    # Агент повторяет тот же чанк: его копия цела, временный файл сервера тоже.
    retried = await _put(app_client, agent, upload_id, last, data[last:])

    assert retried.status_code == 201, retried.text
    row = (await session.scalars(select(Artifact))).one()
    store = FileArtifactStore(artifact_env, MASTER)
    assert b"".join(store.open(row.sha256, row.key_wrapped)) == data
    assert not list((artifact_env / "tmp").iterdir())


async def test_purge_removes_expired_sessions_and_their_temp_files(
    app_client, session, artifact_env
) -> None:
    agent = await enroll_agent(app_client, session, "up-purge")
    upload_id = (await _open(app_client, agent, b"h" * 100)).json()["upload_id"]
    row = await session.get(UploadSession, uuid.UUID(upload_id))
    assert row is not None
    temp = Path(row.temp_path)
    assert temp.exists()  # noqa: ASYNC240 - проверка файла в тесте

    row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()
    removed = await purge_expired_sessions(session, get_settings())
    await session.commit()

    assert removed == 1
    assert not temp.exists()  # noqa: ASYNC240 - проверка файла в тесте
