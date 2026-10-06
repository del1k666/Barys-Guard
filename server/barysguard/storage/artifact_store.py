"""Хранилище артефактов: интерфейс и реализация на файловой системе."""

import base64
import binascii
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from barysguard.core.config import Settings
from barysguard.storage.artifact_crypto import (
    WRAP_VERSION,
    decrypt_stream,
    encrypt_file,
    new_key,
    unwrap_key,
    wrap_key,
)


@dataclass(frozen=True)
class StoredArtifact:
    storage_path: str
    key_wrapped: bytes
    wrap_version: int


class ArtifactStore(Protocol):
    """Шов под S3/MinIO. Методы синхронные: вызывать через asyncio.to_thread."""

    def put(self, sha256: str, source: Path) -> StoredArtifact: ...

    def open(self, sha256: str, key_wrapped: bytes) -> Iterator[bytes]: ...

    def exists(self, sha256: str) -> bool: ...

    def delete(self, sha256: str) -> None: ...


class FileArtifactStore:
    def __init__(self, root: Path, master_key: bytes) -> None:
        self.root = root
        self._master_key = master_key

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / f"{sha256}.enc"

    def put(self, sha256: str, source: Path) -> StoredArtifact:
        key = new_key()
        target = self._path(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        try:
            encrypt_file(source, temporary, key)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return StoredArtifact(
            storage_path=str(target.relative_to(self.root).as_posix()),
            key_wrapped=wrap_key(self._master_key, key),
            wrap_version=WRAP_VERSION,
        )

    def open(self, sha256: str, key_wrapped: bytes) -> Iterator[bytes]:
        return decrypt_stream(self._path(sha256), unwrap_key(self._master_key, key_wrapped))

    def exists(self, sha256: str) -> bool:
        return self._path(sha256).exists()

    def delete(self, sha256: str) -> None:
        self._path(sha256).unlink(missing_ok=True)


def load_master_key(settings: Settings) -> bytes | None:
    """Мастер-ключ из окружения или файла; None — хранилище не настроено."""
    raw = settings.artifact_master_key.strip()
    if not raw and settings.artifact_master_key_file is not None:
        raw = settings.artifact_master_key_file.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("BG_ARTIFACT_MASTER_KEY is not valid base64") from exc
    if len(key) != 32:
        raise ValueError("BG_ARTIFACT_MASTER_KEY must decode to 32 bytes")
    return key


def build_store(settings: Settings) -> FileArtifactStore | None:
    key = load_master_key(settings)
    if key is None:
        return None
    return FileArtifactStore(settings.artifact_path, key)
