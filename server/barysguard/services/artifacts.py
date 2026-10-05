"""Приём артефактов: открытие сессии, чанки, завершение.

Исключения не используются там, где обработчик обязан сохранить изменения в
БД: get_session откатывает транзакцию на исключении, а временный файл к тому
моменту уже удалён. Поэтому исходы «хеш не сошёлся» и «смещение не то»
возвращаются значением ChunkResult.
"""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings
from barysguard.db.models.agent import Agent
from barysguard.db.models.artifact import Artifact, UploadSession
from barysguard.storage.artifact_store import FileArtifactStore


class ArtifactTooLarge(Exception):  # noqa: N818 - имя зафиксировано интерфейсом
    """Заявленный размер больше серверного предела."""


class UploadNotFound(Exception):  # noqa: N818 - имя зафиксировано интерфейсом
    """Нет сессии, она чужая или истекла."""


class ChunkRejected(Exception):  # noqa: N818 - имя зафиксировано интерфейсом
    """Чанк больше предела либо выходит за заявленный размер."""


@dataclass(frozen=True)
class OpenResult:
    exists: bool
    upload_id: uuid.UUID | None = None
    received_bytes: int = 0


@dataclass(frozen=True)
class ChunkResult:
    received_bytes: int
    # partial | complete | hash_mismatch | offset_mismatch
    status: str


def _temp_path(settings: Settings, upload_id: uuid.UUID) -> Path:
    return settings.artifact_path / "tmp" / f"{upload_id}.part"


def _ttl(settings: Settings) -> timedelta:
    return timedelta(hours=settings.upload_session_ttl_hours)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _write_at(path: Path, offset: int, data: bytes) -> None:
    # Обрезка после записи: прежняя неудачная попытка могла оставить хвост,
    # а БД о нём не знает.
    with path.open("r+b") as handle:
        handle.seek(offset)
        handle.write(data)
        handle.truncate()
        handle.flush()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def _discard(session: AsyncSession, upload: UploadSession) -> None:
    temp = Path(upload.temp_path)
    await session.delete(upload)
    await asyncio.to_thread(temp.unlink, True)


async def open_upload(
    session: AsyncSession, agent: Agent, sha256: str, size: int, settings: Settings
) -> OpenResult:
    if size > settings.artifact_max_bytes:
        raise ArtifactTooLarge

    now = datetime.now(UTC)
    known = await session.scalar(
        select(Artifact).where(Artifact.sha256 == sha256).with_for_update()
    )
    if known is not None:
        known.ref_count += 1
        return OpenResult(exists=True)

    where = (UploadSession.agent_id == agent.id, UploadSession.artifact_sha256 == sha256)
    upload = (
        await session.execute(select(UploadSession).where(*where).with_for_update())
    ).scalar_one_or_none()

    if upload is not None and (upload.expires_at <= now or upload.expected_size != size):
        await _discard(session, upload)
        await session.flush()
        upload = None

    if upload is None:
        upload_id = uuid.uuid4()
        temp = _temp_path(settings, upload_id)
        await session.execute(
            pg_insert(UploadSession)
            .values(
                id=upload_id,
                agent_id=agent.id,
                artifact_sha256=sha256,
                expected_size=size,
                received_bytes=0,
                temp_path=str(temp),
                created_at=now,
                expires_at=now + _ttl(settings),
            )
            .on_conflict_do_nothing(index_elements=["agent_id", "artifact_sha256"])
        )
        upload = (await session.execute(select(UploadSession).where(*where))).scalar_one()
        await asyncio.to_thread(_touch, Path(upload.temp_path))
    else:
        upload.expires_at = now + _ttl(settings)
        if not await asyncio.to_thread(Path(upload.temp_path).exists):
            # Временный файл пропал (чистка каталога): докачивать нечего.
            await asyncio.to_thread(_touch, Path(upload.temp_path))
            upload.received_bytes = 0

    return OpenResult(exists=False, upload_id=upload.id, received_bytes=upload.received_bytes)


async def append_chunk(
    session: AsyncSession,
    agent: Agent,
    upload_id: uuid.UUID,
    offset: int,
    data: bytes,
    settings: Settings,
    store: FileArtifactStore,
) -> ChunkResult:
    now = datetime.now(UTC)
    upload = (
        await session.execute(
            select(UploadSession)
            .where(UploadSession.id == upload_id, UploadSession.agent_id == agent.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if upload is None or upload.expires_at <= now:
        raise UploadNotFound

    if offset != upload.received_bytes:
        return ChunkResult(upload.received_bytes, "offset_mismatch")
    if len(data) > settings.artifact_chunk_bytes:
        raise ChunkRejected
    if upload.received_bytes + len(data) > upload.expected_size:
        raise ChunkRejected

    path = Path(upload.temp_path)
    try:
        await asyncio.to_thread(_write_at, path, offset, data)
    except FileNotFoundError:
        await asyncio.to_thread(_touch, path)
        upload.received_bytes = 0
        return ChunkResult(0, "offset_mismatch")

    upload.received_bytes += len(data)
    upload.expires_at = now + _ttl(settings)
    if upload.received_bytes < upload.expected_size:
        return ChunkResult(upload.received_bytes, "partial")

    return await _finalize(session, agent, upload, store)


async def _finalize(
    session: AsyncSession, agent: Agent, upload: UploadSession, store: FileArtifactStore
) -> ChunkResult:
    path = Path(upload.temp_path)
    digest = await asyncio.to_thread(_sha256_file, path)
    if digest != upload.artifact_sha256:
        await _discard(session, upload)
        return ChunkResult(upload.received_bytes, "hash_mismatch")

    # Два агента могут довести до конца один и тот же файл одновременно. Без
    # блокировки каждый записал бы файл под своим ключом, и ключ строки в БД
    # не подошёл бы к файлу, оставшемуся после второй записи.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": digest}
    )
    known = await session.scalar(select(Artifact.sha256).where(Artifact.sha256 == digest))
    if known is None:
        stored = await asyncio.to_thread(store.put, digest, path)
        session.add(
            Artifact(
                sha256=digest,
                size=upload.expected_size,
                storage_path=stored.storage_path,
                key_wrapped=stored.key_wrapped,
                wrap_version=stored.wrap_version,
                first_agent_id=agent.id,
            )
        )
    else:
        # Атомарно в SQL: open_upload параллельно увеличивает счётчик под
        # блокировкой строки, а не под advisory-блокировкой.
        await session.execute(
            update(Artifact)
            .where(Artifact.sha256 == digest)
            .values(ref_count=Artifact.ref_count + 1)
        )

    size = upload.expected_size
    await _discard(session, upload)
    return ChunkResult(size, "complete")


async def purge_expired_sessions(session: AsyncSession, settings: Settings) -> int:
    now = datetime.now(UTC)
    expired = (
        await session.scalars(select(UploadSession).where(UploadSession.expires_at <= now))
    ).all()
    for upload in expired:
        await _discard(session, upload)
    return len(expired)
