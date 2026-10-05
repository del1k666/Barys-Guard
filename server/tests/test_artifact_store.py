"""Шифрование артефактов и файловое хранилище."""

import base64
import os
from pathlib import Path

import pytest

from barysguard.core.config import Settings
from barysguard.storage.artifact_crypto import (
    BLOCK,
    CorruptArtifact,
    decrypt_stream,
    encrypt_file,
    new_key,
    unwrap_key,
    wrap_key,
)
from barysguard.storage.artifact_store import FileArtifactStore, build_store, load_master_key

MASTER = b"m" * 32
SHA = "ab" * 32


def _roundtrip(tmp_path: Path, data: bytes) -> bytes:
    source, target = tmp_path / "plain", tmp_path / "sealed"
    source.write_bytes(data)
    key = new_key()
    encrypt_file(source, target, key)
    return b"".join(decrypt_stream(target, key))


@pytest.mark.parametrize("size", [0, 1, BLOCK - 1, BLOCK, BLOCK + 1, 2 * BLOCK, 2 * BLOCK + 17])
def test_roundtrip_for_block_boundaries(tmp_path: Path, size: int) -> None:
    data = os.urandom(size)
    assert _roundtrip(tmp_path, data) == data


def test_sealed_file_does_not_contain_plaintext(tmp_path: Path) -> None:
    marker = b"4111 1111 1111 1111 SECRET-MARKER"
    source, target = tmp_path / "plain", tmp_path / "sealed"
    source.write_bytes(marker * 100)
    encrypt_file(source, target, new_key())

    assert marker not in target.read_bytes()


def test_wrong_key_is_rejected(tmp_path: Path) -> None:
    source, target = tmp_path / "plain", tmp_path / "sealed"
    source.write_bytes(b"hello")
    encrypt_file(source, target, new_key())

    with pytest.raises(CorruptArtifact):
        b"".join(decrypt_stream(target, new_key()))


def test_flipped_byte_is_detected(tmp_path: Path) -> None:
    source, target = tmp_path / "plain", tmp_path / "sealed"
    source.write_bytes(os.urandom(5000))
    key = new_key()
    encrypt_file(source, target, key)

    raw = bytearray(target.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    target.write_bytes(bytes(raw))

    with pytest.raises(CorruptArtifact):
        b"".join(decrypt_stream(target, key))


def test_truncation_at_a_block_boundary_is_detected(tmp_path: Path) -> None:
    source, target = tmp_path / "plain", tmp_path / "sealed"
    source.write_bytes(os.urandom(2 * BLOCK + 5))
    key = new_key()
    encrypt_file(source, target, key)

    raw = target.read_bytes()
    # Заголовок блока: 4 байта длины; обрезаем файл после первого блока.
    first = int.from_bytes(raw[4:8], "big")
    target.write_bytes(raw[: 4 + 4 + first])

    with pytest.raises(CorruptArtifact):
        b"".join(decrypt_stream(target, key))


def test_key_wrap_roundtrip_and_wrong_master() -> None:
    key = new_key()
    wrapped = wrap_key(MASTER, key)

    assert unwrap_key(MASTER, wrapped) == key
    with pytest.raises(CorruptArtifact):
        unwrap_key(b"x" * 32, wrapped)


def test_store_put_open_exists_delete(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "store", MASTER)
    source = tmp_path / "plain"
    source.write_bytes(("содержимое" * 1000).encode())

    stored = store.put(SHA, source)

    assert (tmp_path / "store" / stored.storage_path).exists()
    assert stored.storage_path.startswith("ab/ab/")
    assert store.exists(SHA)
    assert b"".join(store.open(SHA, stored.key_wrapped)) == source.read_bytes()

    store.delete(SHA)
    assert not store.exists(SHA)


def test_store_leaves_no_temporary_file(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "store", MASTER)
    source = tmp_path / "plain"
    source.write_bytes(b"x")
    store.put(SHA, source)

    leftovers = [p for p in (tmp_path / "store").rglob("*") if p.suffix == ".tmp"]
    assert leftovers == []


def test_master_key_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BG_ARTIFACT_MASTER_KEY", raising=False)
    assert load_master_key(Settings(artifact_master_key="")) is None

    good = base64.b64encode(MASTER).decode()
    assert load_master_key(Settings(artifact_master_key=good)) == MASTER

    with pytest.raises(ValueError):
        load_master_key(Settings(artifact_master_key="не base64!"))
    with pytest.raises(ValueError):
        load_master_key(Settings(artifact_master_key=base64.b64encode(b"short").decode()))


def test_master_key_can_come_from_a_file(tmp_path: Path) -> None:
    key_file = tmp_path / "master.key"
    key_file.write_text(base64.b64encode(MASTER).decode() + "\n", encoding="utf-8")

    assert load_master_key(Settings(artifact_master_key_file=key_file)) == MASTER


def test_build_store_requires_a_key(tmp_path: Path) -> None:
    assert build_store(Settings(artifact_path=tmp_path, artifact_master_key="")) is None
    key = base64.b64encode(MASTER).decode()
    assert build_store(Settings(artifact_path=tmp_path, artifact_master_key=key)) is not None
