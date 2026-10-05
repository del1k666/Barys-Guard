"""Потоковое шифрование артефактов: AES-256-GCM по блокам.

Формат файла: магия, затем блоки `[длина 4 байта][шифртекст с тегом]`.
Nonce блока — его номер; номер и признак последнего блока входят в
связанные данные, поэтому перестановка и усечение на границе блока
обнаруживаются. Ключ у каждого артефакта свой, потому счётчик в роли nonce
безопасен.
"""

import os
from collections.abc import Iterator
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import (
    InvalidUnwrap,
    aes_key_unwrap,
    aes_key_wrap,
)

MAGIC = b"BGA1"
BLOCK = 1024 * 1024
WRAP_VERSION = 1


class CorruptArtifact(Exception):  # noqa: N818 - имя зафиксировано интерфейсом
    """Файл повреждён, усечён либо ключ не подходит."""


def new_key() -> bytes:
    return os.urandom(32)


def wrap_key(master: bytes, key: bytes) -> bytes:
    return aes_key_wrap(master, key)


def unwrap_key(master: bytes, wrapped: bytes) -> bytes:
    try:
        return aes_key_unwrap(master, wrapped)
    except InvalidUnwrap as exc:
        raise CorruptArtifact("key unwrap failed") from exc


def _nonce(index: int) -> bytes:
    return index.to_bytes(12, "big")


def _aad(index: int, last: bool) -> bytes:
    return index.to_bytes(8, "big") + (b"\x01" if last else b"\x00")


def encrypt_file(source: Path, target: Path, key: bytes) -> None:
    aes = AESGCM(key)
    with source.open("rb") as reader, target.open("wb") as writer:
        writer.write(MAGIC)
        index = 0
        block = reader.read(BLOCK)
        while True:
            following = reader.read(BLOCK)
            last = not following
            sealed = aes.encrypt(_nonce(index), block, _aad(index, last))
            writer.write(len(sealed).to_bytes(4, "big"))
            writer.write(sealed)
            if last:
                break
            index += 1
            block = following
        writer.flush()
        os.fsync(writer.fileno())


def decrypt_stream(path: Path, key: bytes) -> Iterator[bytes]:
    aes = AESGCM(key)
    with path.open("rb") as reader:
        if reader.read(len(MAGIC)) != MAGIC:
            raise CorruptArtifact("bad magic")
        index = 0
        header = reader.read(4)
        if not header:
            raise CorruptArtifact("no blocks")
        while header:
            if len(header) != 4:
                raise CorruptArtifact("truncated block header")
            sealed = reader.read(int.from_bytes(header, "big"))
            following = reader.read(4)
            last = not following
            try:
                yield aes.decrypt(_nonce(index), sealed, _aad(index, last))
            except InvalidTag as exc:
                raise CorruptArtifact("block failed authentication") from exc
            index += 1
            header = following
