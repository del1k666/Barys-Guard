# Загрузка и хранение артефактов (B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Агент на Windows загружает файлы, скопированные на съёмный носитель, на сервер (докачиваемая загрузка, шифрованная копия на время офлайна), сервер хранит их зашифрованными, а консоль показывает, загружено ли содержимое.

**Architecture:** Агент снимает копию в том же проходе, что и хеширование (tee), кладёт её в `<data>/staging/` зашифрованной и отдельным фоновым воркером отправляет `POST /gateway/v1/artifacts` + чанки `PUT`. Сервер ведёт сессии загрузки в PostgreSQL, проверяет SHA-256, шифрует артефакт собственным ключом (AES-256-GCM, ключ завёрнут мастер-ключом из окружения) и кладёт в файловое хранилище за интерфейсом `ArtifactStore`.

**Tech Stack:** Python 3.12 (FastAPI, SQLAlchemy 2, Alembic, `cryptography`), Go 1.23 (stdlib, `golang.org/x/sys` уже есть, новых зависимостей не добавлять), React 19 + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-artifact-upload-design.md`

## Global Constraints

- Размер файла для загрузки не больше `collectors.artifact.max_bytes` = **52 428 800** (50 МиБ); серверный предел `BG_ARTIFACT_MAX_BYTES` по умолчанию тот же.
- `staging_max_bytes` = **524 288 000**; `upload_bytes_per_second` = **2 097 152**; `stage_bytes_per_minute` = **209 715 200**; чанк = **1 МиБ** (1 048 576); срок жизни сессии загрузки **24 ч** (`BG_UPLOAD_SESSION_TTL_HOURS`).
- Шифрование: **AES-256-GCM** по блокам 1 МиБ, на сервере отдельный ключ на артефакт, завёрнутый мастер-ключом из `BG_ARTIFACT_MASTER_KEY` (base64, 32 байта) или файла `BG_ARTIFACT_MASTER_KEY_FILE`; не из БД. Без ключа эндпоинты загрузки отвечают `503`.
- Грузятся только `create`/`modify`/`copy` на томах типа `removable`; файлы наблюдаемых папок не грузятся.
- Файл читается за **один проход** (хеш и копия вместе); целиком в память не загружается; воркер загрузки один, один файл за раз, на Windows в фоновом режиме потока (`THREAD_MODE_BACKGROUND_BEGIN`).
- Сессия принадлежит агенту, уникальность `(agent_id, artifact_sha256)`; чужой `upload_id` даёт `404`.
- Ответы: `POST` — `200 {"status":"exists"}` либо `201 {"status":"upload","upload_id","received_bytes","chunk_size"}`, `413` при превышении размера; `PUT` — `202` (частично), `201` (готово), `409 {"received_bytes":N}`, `422` (хеш), `404` (нет сессии), `400` (чанк отвергнут), `413` (чанк больше предела).
- Python: `ruff check .`, `ruff format --check .` и `mypy barysguard` чистые; длина строки 100. Go: `go vet ./...` чист, новых зависимостей нет, комментарии на русском, в стиле окружающего кода.
- Каждый коммит заканчивается строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Серверные тесты с базой запускаются так (из `server/`): `BG_TEST_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard" .venv/Scripts/python -m pytest ...`; без этой переменной нужен Docker.

## Review Focus

1. **Пустой файл (0 байт) на флешке:** загрузка должна завершиться одним пустым `PUT`, а не зависнуть (Task 3 — серверный тест, Task 10 — тест воркера).
2. **Размер ровно кратен блоку (1 МиБ, 2 МиБ):** последний блок шифрования помечается как финальный и расшифровывается; усечение на границе блока обнаруживается (Task 2 — сервер, Task 7 — агент).
3. **Два агента одновременно загружают один и тот же файл:** ключ первого артефакта не затирается вторым, оба получают успех, файл расшифровывается (Task 3, тест `test_concurrent_uploads_of_one_file_keep_a_single_readable_artifact`).
4. **Файл вырос или изменился во время чтения:** хеширование не ломается, копия не превышает `max_bytes` и не портит хранилище (Task 8 — `TestWritePastTheLimitFails`, Task 11 — `TestGrowingFileAbortsStagingButNotTheHash`).
5. **Копия на агенте исчезла или повреждена (антивирус, чистка диска, смена ключа буфера):** воркер не зацикливается, запись снимается, потеря попадает в `agent/artifact_dropped` (Task 10 — `TestMissingStagedCopyIsDroppedNotRetriedForever`, `TestCorruptStagedCopyIsDropped`).

---

## Task 1: Хранилище в БД — миграция, модели, настройки

**Files:**
- Create: `server/alembic/versions/20261006_1000_artifacts.py`
- Create: `server/barysguard/db/models/artifact.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Modify: `server/barysguard/core/config.py` (блок «Хранилище артефактов»)
- Modify: `server/tests/conftest.py` (список `TRUNCATE`)
- Test: `server/tests/test_artifact_models.py`

**Interfaces:**
- Produces: `Artifact(id, sha256, size, storage_path, key_wrapped, wrap_version, first_seen_at, first_agent_id, ref_count, scan_status)`, `UploadSession(id, agent_id, artifact_sha256, expected_size, received_bytes, temp_path, created_at, expires_at)` в `barysguard.db.models.artifact`; настройки `Settings.artifact_master_key_file`, `artifact_max_bytes`, `artifact_chunk_bytes`, `upload_session_ttl_hours`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_artifact_models.py
"""Таблицы artifacts и upload_sessions."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from barysguard.db.models.artifact import Artifact, UploadSession
from tests.helpers import enroll_agent

SHA = "ab" * 32


def _artifact(**extra) -> Artifact:
    return Artifact(
        sha256=SHA, size=5, storage_path="ab/ab/x.enc", key_wrapped=b"k" * 40, **extra
    )


async def test_artifact_defaults(app_client, session) -> None:
    session.add(_artifact())
    await session.commit()

    stored = await session.get(Artifact, (await _only_id(session)))
    assert stored is not None
    assert stored.ref_count == 1
    assert stored.scan_status == "pending"
    assert stored.wrap_version == 1
    assert stored.first_seen_at is not None


async def _only_id(session) -> uuid.UUID:
    from sqlalchemy import select

    return (await session.scalars(select(Artifact.id))).one()


async def test_artifact_sha256_is_unique(app_client, session) -> None:
    session.add(_artifact())
    await session.commit()

    session.add(_artifact())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_one_open_session_per_agent_and_hash(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "models-1")
    now = datetime.now(UTC)

    def make() -> UploadSession:
        return UploadSession(
            agent_id=agent.agent_id,
            artifact_sha256=SHA,
            expected_size=10,
            temp_path="tmp/x.part",
            expires_at=now + timedelta(hours=1),
        )

    session.add(make())
    await session.commit()

    session.add(make())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run (из `server/`): `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest tests/test_artifact_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.db.models.artifact'`

- [ ] **Step 3: Write minimal implementation**

`server/barysguard/db/models/artifact.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class Artifact(Base):
    """Содержимое файла, адресуемое по SHA-256.

    Файл лежит в хранилище зашифрованным собственным ключом; здесь только его
    обёртка мастер-ключом. Один документ, скопированный многими, — одна строка.
    """

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text)
    key_wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    wrap_version: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    first_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    # Сколько раз артефакт предъявляли серверу (ответ exists).
    ref_count: Mapped[int] = mapped_column(Integer, server_default="1")
    # Читает воркер инспекции (подпроект B2).
    scan_status: Mapped[str] = mapped_column(Text, server_default="pending")


class UploadSession(Base):
    """Открытая загрузка. Принадлежит агенту: чужой upload_id не подходит."""

    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint("agent_id", "artifact_sha256", name="uq_upload_sessions_agent_sha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    artifact_sha256: Mapped[str] = mapped_column(Text)
    expected_size: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    temp_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

`server/barysguard/db/models/__init__.py`: добавить `from barysguard.db.models.artifact import Artifact, UploadSession` (после импорта `agent`) и `"Artifact"`, `"UploadSession"` в `__all__` (в алфавитном порядке списка).

`server/barysguard/core/config.py` — заменить блок

```python
    # Хранилище артефактов (используется в плане 1C)
    artifact_path: Path = Path("/var/lib/barysguard/artifacts")
    artifact_master_key: str = ""
```

на

```python
    # Хранилище артефактов. Мастер-ключ — 32 байта в base64, из окружения или
    # файла секретов, но не из базы: дамп БД не должен открывать содержимое.
    artifact_path: Path = Path("/var/lib/barysguard/artifacts")
    artifact_master_key: str = ""
    artifact_master_key_file: Path | None = None
    artifact_max_bytes: int = 50 * 1024 * 1024
    artifact_chunk_bytes: int = 1024 * 1024
    upload_session_ttl_hours: int = 24
```

Миграция `server/alembic/versions/20261006_1000_artifacts.py`:

```python
"""artifacts and upload sessions

Revision ID: a4d2f6c81b53
Revises: e7a1c3b94d20
Create Date: 2026-10-06 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a4d2f6c81b53'
down_revision: str | None = 'e7a1c3b94d20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("key_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("wrap_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "first_agent_id", sa.Uuid(),
            sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("ref_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scan_status", sa.Text(), nullable=False, server_default="pending"),
        sa.UniqueConstraint("sha256", name="uq_artifacts_sha256"),
    )
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("temp_path", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "artifact_sha256", name="uq_upload_sessions_agent_sha"),
    )
    op.create_index("ix_upload_sessions_expires_at", "upload_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("upload_sessions")
    op.drop_table("artifacts")
```

`server/tests/conftest.py`: в строке `TRUNCATE events, commands, ...` добавить `artifacts, upload_sessions,` сразу после `events,`.

- [ ] **Step 4: Run test to verify it passes**

Run: `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest tests/test_artifact_models.py tests/test_migrations.py -q`
Expected: PASS (миграции доходят до нового `head`).

- [ ] **Step 5: Commit**

```bash
git add server/alembic/versions/20261006_1000_artifacts.py server/barysguard/db/models server/barysguard/core/config.py server/tests/conftest.py server/tests/test_artifact_models.py
git commit -F - <<'EOF'
feat(server): artifacts and upload_sessions tables

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 2: Шифрование и `FileArtifactStore`

**Files:**
- Create: `server/barysguard/storage/__init__.py` (пустой)
- Create: `server/barysguard/storage/artifact_crypto.py`
- Create: `server/barysguard/storage/artifact_store.py`
- Test: `server/tests/test_artifact_store.py`

**Interfaces:**
- Produces:
  - `artifact_crypto`: `BLOCK = 1024 * 1024`, `WRAP_VERSION = 1`, `class CorruptArtifact(Exception)`, `new_key() -> bytes`, `wrap_key(master: bytes, key: bytes) -> bytes`, `unwrap_key(master: bytes, wrapped: bytes) -> bytes`, `encrypt_file(source: Path, target: Path, key: bytes) -> None`, `decrypt_stream(path: Path, key: bytes) -> Iterator[bytes]`.
  - `artifact_store`: `@dataclass(frozen=True) StoredArtifact(storage_path: str, key_wrapped: bytes, wrap_version: int)`, `class ArtifactStore(Protocol)` (`put`, `open`, `exists`, `delete` — синхронные; вызывающий коду оборачивает в `asyncio.to_thread`), `class FileArtifactStore(root: Path, master_key: bytes)`, `load_master_key(settings: Settings) -> bytes | None` (бросает `ValueError` при неверном ключе), `build_store(settings: Settings) -> FileArtifactStore | None`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_artifact_store.py
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


@pytest.mark.parametrize(
    "size", [0, 1, BLOCK - 1, BLOCK, BLOCK + 1, 2 * BLOCK, 2 * BLOCK + 17]
)
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
    source.write_bytes(b"содержимое" * 1000)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_artifact_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.storage'`

- [ ] **Step 3: Write minimal implementation**

`server/barysguard/storage/__init__.py` — пустой файл.

`server/barysguard/storage/artifact_crypto.py`:

```python
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


class CorruptArtifact(Exception):
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
```

`server/barysguard/storage/artifact_store.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_artifact_store.py -q && .venv/Scripts/python -m ruff check . && .venv/Scripts/python -m mypy barysguard`
Expected: PASS, lint чистый. (Если `ruff format --check` ругается на новые файлы — `ruff format` на них.)

- [ ] **Step 5: Commit**

```bash
git add server/barysguard/storage server/tests/test_artifact_store.py
git commit -F - <<'EOF'
feat(server): encrypted artifact store

Per-artifact AES-256-GCM key wrapped by a master key from the
environment; block-level authentication catches reordering and truncation.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 3: Сессии загрузки и эндпоинты шлюза

**Files:**
- Create: `server/barysguard/services/artifacts.py`
- Modify: `server/barysguard/gateway/schemas.py` (три модели в конец файла)
- Modify: `server/barysguard/gateway/router.py` (два эндпоинта, импорты)
- Test: `server/tests/test_artifact_upload.py`

**Interfaces:**
- Consumes: `FileArtifactStore`, `build_store` (Task 2); `Artifact`, `UploadSession` (Task 1).
- Produces (`barysguard.services.artifacts`):
  - исключения `ArtifactTooLarge`, `UploadNotFound`, `ChunkRejected`;
  - `@dataclass(frozen=True) OpenResult(exists: bool, upload_id: uuid.UUID | None = None, received_bytes: int = 0)`;
  - `@dataclass(frozen=True) ChunkResult(received_bytes: int, status: str)`, где `status` ∈ `"partial" | "complete" | "hash_mismatch" | "offset_mismatch"`;
  - `async open_upload(session, agent, sha256, size, settings) -> OpenResult`;
  - `async append_chunk(session, agent, upload_id, offset, data, settings, store) -> ChunkResult`;
  - `async purge_expired_sessions(session, settings) -> int`.
- Produces (`gateway/schemas.py`): `ArtifactOpenRequest(sha256, size)`, `ArtifactOpenResponse(status, upload_id, received_bytes, chunk_size)`, `ArtifactChunkResponse(received_bytes, status)`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_artifact_upload.py
"""POST/PUT /gateway/v1/artifacts."""

import asyncio
import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from barysguard.core.config import get_settings
from barysguard.db.models.artifact import Artifact, UploadSession
from barysguard.services.artifacts import purge_expired_sessions
from barysguard.storage.artifact_store import FileArtifactStore
from tests.helpers import enroll_agent

MASTER = b"k" * 32
CHUNK = 1024


@pytest.fixture
def artifact_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Окружение хранилища. Обязан идти раньше app_client: тот сбрасывает кеш настроек."""
    root = tmp_path / "artifacts"
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(root))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(MASTER).decode())
    monkeypatch.setenv("BG_ARTIFACT_CHUNK_BYTES", str(CHUNK))
    monkeypatch.setenv("BG_ARTIFACT_MAX_BYTES", "8192")
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


async def _upload_all(client, agent, data: bytes):
    opened = await _open(client, agent, data)
    assert opened.status_code == 201, opened.text
    upload_id = opened.json()["upload_id"]
    offset, last = 0, None
    while True:
        last = await _put(client, agent, upload_id, offset, data[offset : offset + CHUNK])
        assert last.status_code in (201, 202), last.text
        offset += len(data[offset : offset + CHUNK])
        if last.status_code == 201:
            return last


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


async def test_empty_file_completes_with_one_empty_chunk(
    app_client, session, artifact_env
) -> None:
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

    results = await asyncio.gather(
        _upload_all(app_client, first, data), _upload_all(app_client, second, data)
    )

    assert all(r.status_code == 201 for r in results)
    rows = (await session.scalars(select(Artifact))).all()
    assert len(rows) == 1
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


async def test_purge_removes_expired_sessions_and_their_temp_files(
    app_client, session, artifact_env
) -> None:
    agent = await enroll_agent(app_client, session, "up-purge")
    upload_id = (await _open(app_client, agent, b"h" * 100)).json()["upload_id"]
    row = await session.get(UploadSession, uuid.UUID(upload_id))
    assert row is not None
    temp = Path(row.temp_path)
    assert temp.exists()

    row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()
    removed = await purge_expired_sessions(session, get_settings())
    await session.commit()

    assert removed == 1
    assert not temp.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest tests/test_artifact_upload.py -q -x`
Expected: FAIL — `ModuleNotFoundError: barysguard.services.artifacts` (затем 404/405 на эндпоинтах).

- [ ] **Step 3: Write minimal implementation**

`server/barysguard/services/artifacts.py`:

```python
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

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings
from barysguard.db.models.agent import Agent
from barysguard.db.models.artifact import Artifact, UploadSession
from barysguard.storage.artifact_store import FileArtifactStore


class ArtifactTooLarge(Exception):
    """Заявленный размер больше серверного предела."""


class UploadNotFound(Exception):
    """Нет сессии, она чужая или истекла."""


class ChunkRejected(Exception):
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
    known = await session.scalar(select(Artifact).where(Artifact.sha256 == digest))
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
        known.ref_count += 1

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
```

`server/barysguard/gateway/schemas.py` — добавить в конец (проверить, что `uuid`, `BaseModel`, `Field` уже импортированы; если нет — добавить):

```python
class ArtifactOpenRequest(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class ArtifactOpenResponse(BaseModel):
    # exists — байты не нужны; upload — открыта (или продолжена) сессия.
    status: str
    upload_id: uuid.UUID | None = None
    received_bytes: int | None = None
    chunk_size: int | None = None


class ArtifactChunkResponse(BaseModel):
    received_bytes: int
    # partial | complete
    status: str
```

`server/barysguard/gateway/router.py` — импорты (`logging`, `Header`, `JSONResponse` уже есть):

```python
import logging
```
```python
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
```
```python
from barysguard.gateway.schemas import (
    AgentConfigResponse,
    ArtifactChunkResponse,
    ArtifactOpenRequest,
    ArtifactOpenResponse,
    CommandResultRequest,
    EnrollRequest,
    EnrollResponse,
    EventsResult,
    HeartbeatRequest,
    HeartbeatResponse,
    QueuedCommand,
    RejectedLine,
    RenewRequest,
    RenewResponse,
)
from barysguard.services.artifacts import (
    ArtifactTooLarge,
    ChunkRejected,
    UploadNotFound,
    append_chunk,
    open_upload,
)
from barysguard.storage.artifact_store import FileArtifactStore, build_store
```
и под `router = APIRouter(...)` добавить `logger = logging.getLogger(__name__)`. В конец файла:

```python
def _artifact_store(settings: Settings) -> FileArtifactStore:
    try:
        store = build_store(settings)
    except ValueError:
        logger.error("мастер-ключ артефактов задан неверно", exc_info=True)
        store = None
    if store is None:
        # Остальной шлюз работает; агент считает это временной ошибкой.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "artifact storage not configured")
    return store


@router.post("/artifacts", response_model=ArtifactOpenResponse)
async def open_artifact_upload(
    payload: ArtifactOpenRequest,
    response: Response,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ArtifactOpenResponse:
    """Открыть загрузку: сервер либо уже имеет артефакт, либо выдаёт сессию."""
    _artifact_store(settings)
    try:
        result = await open_upload(session, agent, payload.sha256, payload.size, settings)
    except ArtifactTooLarge as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "artifact too large") from exc

    if result.exists:
        response.status_code = status.HTTP_200_OK
        return ArtifactOpenResponse(status="exists")
    response.status_code = status.HTTP_201_CREATED
    return ArtifactOpenResponse(
        status="upload",
        upload_id=result.upload_id,
        received_bytes=result.received_bytes,
        chunk_size=settings.artifact_chunk_bytes,
    )


@router.put(
    "/artifacts/{upload_id}",
    response_model=ArtifactChunkResponse,
    # Тело — сырые байты, FastAPI его не описывает: контракт задаётся вручную.
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_artifact_chunk(
    upload_id: uuid.UUID,
    request: Request,
    response: Response,
    x_offset: int = Header(alias="X-Offset", ge=0),
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ArtifactChunkResponse | JSONResponse:
    """Очередной чанк. Смещение обязано совпасть с числом уже принятых байт."""
    store = _artifact_store(settings)
    limit = settings.artifact_chunk_bytes

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "chunk too large")

    parts: list[bytes] = []
    size = 0
    async for part in request.stream():
        size += len(part)
        if size > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "chunk too large")
        parts.append(part)

    try:
        result = await append_chunk(
            session, agent, upload_id, x_offset, b"".join(parts), settings, store
        )
    except UploadNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "upload not found") from exc
    except ChunkRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "chunk rejected") from exc

    if result.status == "offset_mismatch":
        return JSONResponse({"received_bytes": result.received_bytes}, status.HTTP_409_CONFLICT)
    if result.status == "hash_mismatch":
        return JSONResponse({"detail": "hash mismatch"}, status.HTTP_422_UNPROCESSABLE_CONTENT)

    complete = result.status == "complete"
    response.status_code = status.HTTP_201_CREATED if complete else status.HTTP_202_ACCEPTED
    return ArtifactChunkResponse(received_bytes=result.received_bytes, status=result.status)
```

Заметки исполнителю: (а) `status.HTTP_422_UNPROCESSABLE_CONTENT` есть в Starlette ≥ 0.48; если константы нет, использовать числовое `422`; (б) если `ruff` ругается на длину строки `openapi_extra`, разбить словарь на строки; (в) `uuid` в `router.py` уже импортирован.

- [ ] **Step 4: Run test to verify it passes**

Run: `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest tests/test_artifact_upload.py -q`
Expected: PASS (13 тестов). Затем `ruff check . && ruff format --check . && mypy barysguard`.

- [ ] **Step 5: Commit**

```bash
git add server/barysguard/services/artifacts.py server/barysguard/gateway server/tests/test_artifact_upload.py
git commit -F - <<'EOF'
feat(server): resumable artifact upload endpoints

POST /gateway/v1/artifacts opens or resumes a session (or answers exists);
PUT /gateway/v1/artifacts/{id} appends a chunk at the expected offset and
verifies SHA-256 at the end. Concurrent finalization of one hash is
serialized with an advisory lock so a wrapped key always matches its file.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 4: Конфигурация `collectors.artifact`, `artifact_uploaded` в событиях, контракт, очистка, стенд

**Files:**
- Modify: `server/barysguard/services/config.py` (класс `ArtifactCollectorConfig`, поле `CollectorsConfig.artifact`)
- Modify: `server/barysguard/api/schemas.py` (поле `EventSummary.artifact_uploaded`)
- Modify: `server/barysguard/api/events.py` (подзапрос `EXISTS`)
- Modify: `server/barysguard/main.py` (очистка сессий)
- Modify: `deploy/stand/docker-compose.yml`
- Regenerate: `api/gateway-v1.yaml`
- Test: `server/tests/test_collectors_config.py` (дополнить), `server/tests/test_console_events_api.py` (дополнить)

**Interfaces:**
- Consumes: `Artifact` (Task 1), `purge_expired_sessions` (Task 3).
- Produces: `EventSummary.artifact_uploaded: bool`; раздел документа `collectors.artifact` с ключами `enabled`, `max_bytes`, `staging_max_bytes`, `upload_bytes_per_second`, `stage_bytes_per_minute`.

- [ ] **Step 1: Write the failing tests**

Дополнить `server/tests/test_collectors_config.py` (в стиле файла; импорты `AgentConfigDocument`, `ValidationError`, `pytest` проверить в начале файла и добавить недостающие):

```python
def test_artifact_defaults_match_the_spec() -> None:
    artifact = AgentConfigDocument().collectors.artifact

    assert artifact.enabled is True
    assert artifact.max_bytes == 52_428_800
    assert artifact.staging_max_bytes == 524_288_000
    assert artifact.upload_bytes_per_second == 2_097_152
    assert artifact.stage_bytes_per_minute == 209_715_200


def test_artifact_rejects_unknown_keys_and_absurd_values() -> None:
    with pytest.raises(ValidationError):
        AgentConfigDocument.model_validate({"collectors": {"artifact": {"max_byte": 1}}})
    with pytest.raises(ValidationError):
        AgentConfigDocument.model_validate({"collectors": {"artifact": {"max_bytes": 0}}})
```

Дополнить `server/tests/test_console_events_api.py`:

```python
async def test_event_reports_whether_its_artifact_was_uploaded(app_client, session) -> None:
    from barysguard.db.models.artifact import Artifact

    await login_as(app_client, session, username="ev-art", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-art-agent")
    sha = "cd" * 32
    now = datetime.now(UTC)
    await _send(
        app_client,
        agent,
        _event(
            at=now,
            channel="file",
            action="create",
            subject={"dst_path": "E:\\a.txt", "volume": {"type": "removable"}},
            artifact={"sha256": sha, "size": 5, "uploaded": False},
        ),
        _event(at=now - timedelta(minutes=1), action="start"),
    )

    before = (await app_client.get("/api/v1/events")).json()["items"]
    assert [item["artifact_uploaded"] for item in before] == [False, False]

    session.add(Artifact(sha256=sha, size=5, storage_path="x", key_wrapped=b"k" * 40))
    await session.commit()

    after = (await app_client.get("/api/v1/events")).json()["items"]
    assert [item["artifact_uploaded"] for item in after] == [True, False]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest tests/test_collectors_config.py tests/test_console_events_api.py -q`
Expected: FAIL — `AttributeError ... 'artifact'` и `KeyError: 'artifact_uploaded'`.

- [ ] **Step 3: Write minimal implementation**

`server/barysguard/services/config.py` — перед `class CollectorsConfig`:

```python
class ArtifactCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    staging_max_bytes: int = Field(
        default=500 * 1024 * 1024, ge=1024 * 1024, le=50 * 1024 * 1024 * 1024
    )
    upload_bytes_per_second: int = Field(
        default=2 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024
    )
    stage_bytes_per_minute: int = Field(
        default=200 * 1024 * 1024, ge=1024 * 1024, le=100 * 1024 * 1024 * 1024
    )
```

и в `CollectorsConfig` добавить `artifact: ArtifactCollectorConfig = ArtifactCollectorConfig()`.

`server/barysguard/api/schemas.py` — в `EventSummary` после `artifact_sha256: str | None` добавить `artifact_uploaded: bool = False`.

`server/barysguard/api/events.py` — импорты: `from sqlalchemy import ColumnElement, and_, exists, or_, select` и `from barysguard.db.models.artifact import Artifact`. Заменить запрос и сборку ответа:

```python
    uploaded = (
        exists(select(Artifact.id).where(Artifact.sha256 == Event.artifact_sha256))
        .correlate(Event)
        .label("artifact_uploaded")
    )
    rows = (
        await session.execute(
            select(Event, Agent.hostname, uploaded)
            .join(Agent, Agent.id == Event.agent_id)
            .where(*conditions)
            .order_by(Event.occurred_at.desc(), Event.event_id.desc())
            .limit(limit + 1)
        )
    ).all()
```

Сборка ответа (курсор по-прежнему берёт `page[-1][0]`, то есть сам `Event`):

```python
    return EventPage(
        items=[
            EventSummary(
                event_id=event.event_id,
                agent_id=event.agent_id,
                hostname=hostname,
                occurred_at=event.occurred_at,
                received_at=event.received_at,
                channel=event.channel,
                action=event.action,
                severity=event.severity,
                actor=event.actor,
                process=event.process,
                subject=event.subject,
                labels=event.labels,
                artifact_sha256=event.artifact_sha256,
                artifact_uploaded=bool(is_uploaded),
            )
            for event, hostname, is_uploaded in page
        ],
        next_cursor=next_cursor,
    )
```

`server/barysguard/main.py` — добавить рядом с `_ensure_partitions_on_startup`:

```python
async def _purge_upload_sessions() -> None:
    """Удаляет просроченные сессии загрузки вместе с временными файлами."""
    from barysguard.db.session import _get_sessionmaker
    from barysguard.services.artifacts import purge_expired_sessions

    try:
        async with _get_sessionmaker()() as session:
            removed = await purge_expired_sessions(session, get_settings())
            await session.commit()
        if removed:
            logger.info("удалено просроченных сессий загрузки: %d", removed)
    except Exception:
        logger.warning("не удалось очистить сессии загрузки", exc_info=True)


async def _purge_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        await _purge_upload_sessions()
```
`import asyncio` и `from contextlib import asynccontextmanager, suppress` сверху; `_lifespan` становится:

```python
@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _ensure_partitions_on_startup()
    await _purge_upload_sessions()
    purger = asyncio.create_task(_purge_loop())
    try:
        yield
    finally:
        purger.cancel()
        with suppress(asyncio.CancelledError):
            await purger
```

`deploy/stand/docker-compose.yml`:
в `x-server-environment` добавить строки

```yaml
  BG_ARTIFACT_PATH: /var/lib/barysguard/artifacts
  # Известный ключ только для стенда: хранилище должно работать «из коробки».
  BG_ARTIFACT_MASTER_KEY: ${BG_STAND_ARTIFACT_KEY:-YmFyeXNndWFyZC1zdGFuZC1hcnRpZmFjdC1rZXktMzI=}
```
в сервисе `server` к `volumes` добавить `- artifacts:/var/lib/barysguard/artifacts`, а в секцию `volumes:` в конце файла — `artifacts:`. (Том создаётся из образа с владельцем `barysguard`: `Dockerfile` уже делает `mkdir -p /var/lib/barysguard/artifacts` и `chown`.)

Регенерация контракта и типов консоли (из `server/`):

```bash
.venv/Scripts/python -c "import yaml; from barysguard.main import create_app; open('../api/gateway-v1.yaml', 'w', encoding='utf-8', newline='\n').write(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True))"
cd ../web && npm run types
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest -q` (весь серверный набор) и `.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check . && .venv/Scripts/python -m mypy barysguard`.
Expected: PASS целиком; `test_checked_in_contract_matches_the_application` зелёный после регенерации.

- [ ] **Step 5: Commit**

```bash
git add server api web/src/api/schema.d.ts deploy/stand/docker-compose.yml
git commit -F - <<'EOF'
feat(server): collectors.artifact config, artifact_uploaded in events, session cleanup

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 5: Консоль показывает, загружено ли содержимое

**Files:**
- Modify: `web/src/i18n/ru.ts` (блок `events.detail`)
- Modify: `web/src/features/events/EventDetailPanel.tsx`
- Test: `web/src/features/events/EventsPage.test.tsx` (дополнить)

**Interfaces:**
- Consumes: `EventSummary.artifact_uploaded: boolean` (Task 4, типы сгенерированы).

- [ ] **Step 1: Write the failing test**

В `EventsPage.test.tsx`: добавить в фикстуру `COPY` поле `artifact_uploaded: true`, в `USB` — `artifact_uploaded: false`, и новые тесты:

```tsx
  it("в панели видно, что содержимое файла загружено на сервер", async () => {
    setup(eventsPage([COPY]));
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Содержимое на сервере")).toBeInTheDocument();
    expect(within(dialog).getByText("Загружено")).toBeInTheDocument();
  });

  it("если хеш есть, а файла нет на сервере, панель говорит «Не загружено»", async () => {
    setup(eventsPage([{ ...COPY, artifact_uploaded: false }]));
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));

    expect(within(await screen.findByRole("dialog")).getByText("Не загружено")).toBeInTheDocument();
  });

  it("у события без файла содержимое не упоминается как загруженное или нет", async () => {
    setup(eventsPage([USB]));
    renderPage(<EventsPage />, route());

    await userEvent.click(await screen.findByRole("button", { name: "Подробнее" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText("Загружено")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Не загружено")).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run (из `web/`): `npx vitest run src/features/events -t "содержимое"` и остальные новые.
Expected: FAIL — текст «Содержимое на сервере» не найден.

- [ ] **Step 3: Write minimal implementation**

`ru.ts`, блок `events.detail` — добавить после `sha256`:

```ts
      content: "Содержимое на сервере",
      stored: "Загружено",
      notStored: "Не загружено",
```

`EventDetailPanel.tsx` — после `Fact` с `sha256` добавить:

```tsx
        {event.artifact_sha256 ? (
          <Fact label={ru.events.detail.content}>
            {event.artifact_uploaded ? ru.events.detail.stored : ru.events.detail.notStored}
          </Fact>
        ) : null}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run && npm run typecheck && npm run build`
Expected: PASS, сборка проходит.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -F - <<'EOF'
feat(web): show whether the file content reached the server

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 6: Агент — раздел `collectors.artifact` и каталог копий

**Files:**
- Create: `agent/internal/artifacts/config.go`
- Modify: `agent/internal/config/layout.go` (метод `StagingDir`)
- Test: `agent/internal/artifacts/config_test.go`, `agent/internal/config/layout_test.go`

**Interfaces:**
- Produces: `artifacts.Config{Enabled bool; MaxBytes, StagingMaxBytes, UploadBytesPerSecond, StageBytesPerMinute int64}`, `artifacts.DefaultConfig() Config`, `artifacts.ConfigFromDocument(document map[string]any) Config`, `config.Layout.StagingDir() string`.

- [ ] **Step 1: Write the failing tests**

```go
// agent/internal/artifacts/config_test.go
package artifacts

import "testing"

func TestDefaultsMatchTheSpec(t *testing.T) {
	got := ConfigFromDocument(nil)
	want := Config{
		Enabled: true, MaxBytes: 52_428_800, StagingMaxBytes: 524_288_000,
		UploadBytesPerSecond: 2_097_152, StageBytesPerMinute: 209_715_200,
	}
	if got != want {
		t.Fatalf("умолчания: %+v, ожидалось %+v", got, want)
	}
}

func TestDocumentOverridesDefaults(t *testing.T) {
	document := map[string]any{"collectors": map[string]any{"artifact": map[string]any{
		"enabled": false, "max_bytes": float64(1000), "staging_max_bytes": float64(2000),
		"upload_bytes_per_second": float64(3000), "stage_bytes_per_minute": float64(4000),
	}}}
	got := ConfigFromDocument(document)
	want := Config{Enabled: false, MaxBytes: 1000, StagingMaxBytes: 2000, UploadBytesPerSecond: 3000, StageBytesPerMinute: 4000}
	if got != want {
		t.Fatalf("%+v, ожидалось %+v", got, want)
	}
}

// Плохой документ не должен оставлять агента без загрузки или с нулевым лимитом.
func TestGarbageValuesFallBackToDefaults(t *testing.T) {
	document := map[string]any{"collectors": map[string]any{"artifact": map[string]any{
		"enabled": "yes", "max_bytes": float64(-5), "staging_max_bytes": "много",
		"upload_bytes_per_second": float64(0), "stage_bytes_per_minute": nil,
	}}}
	if got := ConfigFromDocument(document); got != DefaultConfig() {
		t.Fatalf("%+v, ожидались умолчания", got)
	}
}
```

```go
// agent/internal/config/layout_test.go
package config

import (
	"path/filepath"
	"testing"
)

func TestStagingDirIsInsideTheDataDir(t *testing.T) {
	layout := NewLayout(filepath.Join("data", "agent"))
	if got, want := layout.StagingDir(), filepath.Join("data", "agent", "staging"); got != want {
		t.Fatalf("StagingDir = %q, ожидалось %q", got, want)
	}
}
```

- [ ] **Step 2: Run to verify they fail**

Run (из `agent/`): `go test ./internal/artifacts ./internal/config`
Expected: FAIL — пакет `artifacts` не существует; `layout.StagingDir undefined`.

- [ ] **Step 3: Write minimal implementation**

```go
// agent/internal/artifacts/config.go

// Package artifacts — копии файлов с внешних томов и их загрузка на сервер.
//
// Копия снимается в том же проходе, что и хеширование, хранится зашифрованной
// и уходит на сервер отдельным воркером с низким приоритетом.
package artifacts

// Умолчания совпадают с серверными (services/config.py): агент обязан работать
// и с документом, в котором раздела collectors.artifact нет.
const (
	defaultMaxBytes       = 50 * 1024 * 1024
	defaultStagingMax     = 500 * 1024 * 1024
	defaultUploadPerSec   = 2 * 1024 * 1024
	defaultStagePerMinute = 200 * 1024 * 1024
)

type Config struct {
	Enabled bool
	// MaxBytes — предельный размер файла для загрузки.
	MaxBytes int64
	// StagingMaxBytes — предел каталога копий; сверх него вытесняются старые.
	StagingMaxBytes int64
	// UploadBytesPerSecond ограничивает скорость отправки.
	UploadBytesPerSecond int64
	// StageBytesPerMinute — бюджет копирования: сверх него файл пропускается.
	StageBytesPerMinute int64
}

func DefaultConfig() Config {
	return Config{
		Enabled:              true,
		MaxBytes:             defaultMaxBytes,
		StagingMaxBytes:      defaultStagingMax,
		UploadBytesPerSecond: defaultUploadPerSec,
		StageBytesPerMinute:  defaultStagePerMinute,
	}
}

// ConfigFromDocument читает collectors.artifact. Отсутствующее или
// бессмысленное значение заменяется умолчанием.
func ConfigFromDocument(document map[string]any) Config {
	cfg := DefaultConfig()
	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors["artifact"].(map[string]any)

	if enabled, ok := section["enabled"].(bool); ok {
		cfg.Enabled = enabled
	}
	cfg.MaxBytes = positive(section, "max_bytes", cfg.MaxBytes)
	cfg.StagingMaxBytes = positive(section, "staging_max_bytes", cfg.StagingMaxBytes)
	cfg.UploadBytesPerSecond = positive(section, "upload_bytes_per_second", cfg.UploadBytesPerSecond)
	cfg.StageBytesPerMinute = positive(section, "stage_bytes_per_minute", cfg.StageBytesPerMinute)
	return cfg
}

func positive(section map[string]any, key string, fallback int64) int64 {
	if value, ok := section[key].(float64); ok && value >= 1 {
		return int64(value)
	}
	return fallback
}
```

В `agent/internal/config/layout.go` добавить рядом с `CAPath`:

```go
// StagingDir — зашифрованные копии файлов с внешних томов до загрузки на сервер.
func (l Layout) StagingDir() string { return filepath.Join(l.Dir, "staging") }
```

- [ ] **Step 4: Run to verify they pass**

Run: `go test ./internal/artifacts ./internal/config && go vet ./internal/artifacts ./internal/config`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/internal/artifacts agent/internal/config
git commit -F - <<'EOF'
feat(agent): artifact config section and staging directory

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 7: Агент — потоковое шифрование копий

**Files:**
- Create: `agent/internal/artifacts/crypto.go`
- Test: `agent/internal/artifacts/crypto_test.go`

**Interfaces:**
- Produces: `newEncryptWriter(dst io.Writer, master []byte) (*encryptWriter, error)` (методы `Write`, `Close() error`, поле `plain int64` — число принятых байт открытого текста), `newDecryptReader(src io.Reader, master []byte) (io.Reader, error)`, `errCorrupt`, константа `blockSize = 1 << 20`.

Формат: `"BGS1"` + соль 16 байт, затем блоки `[длина 4 байта][шифртекст с тегом]`. Ключ файла = `HMAC-SHA256(master, "barysguard-staging-v1" || salt)`, поэтому счётчик блока в роли nonce безопасен. Номер блока и признак последнего входят в связанные данные.

- [ ] **Step 1: Write the failing test**

```go
// agent/internal/artifacts/crypto_test.go
package artifacts

import (
	"bytes"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"io"
	"testing"
)

var testKey = bytes.Repeat([]byte{7}, 32)

func seal(t *testing.T, key, data []byte) []byte {
	t.Helper()
	var out bytes.Buffer
	writer, err := newEncryptWriter(&out, key)
	if err != nil {
		t.Fatal(err)
	}
	// Мелкими порциями, как это делает io.Copy поверх файла.
	for rest := data; len(rest) > 0; {
		n := min(len(rest), 4096)
		if _, err := writer.Write(rest[:n]); err != nil {
			t.Fatal(err)
		}
		rest = rest[n:]
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}

func unseal(key, raw []byte) ([]byte, error) {
	reader, err := newDecryptReader(bytes.NewReader(raw), key)
	if err != nil {
		return nil, err
	}
	return io.ReadAll(reader)
}

func TestRoundTripAtBlockBoundaries(t *testing.T) {
	for _, size := range []int{0, 1, blockSize - 1, blockSize, blockSize + 1, 2 * blockSize, 2*blockSize + 17} {
		data := make([]byte, size)
		rand.Read(data)

		got, err := unseal(testKey, seal(t, testKey, data))
		if err != nil {
			t.Fatalf("размер %d: %v", size, err)
		}
		if !bytes.Equal(got, data) {
			t.Fatalf("размер %d: данные не совпали", size)
		}
	}
}

func TestSealedDataHidesThePlaintext(t *testing.T) {
	marker := []byte("4111 1111 1111 1111 SECRET")
	raw := seal(t, testKey, bytes.Repeat(marker, 100))
	if bytes.Contains(raw, marker) {
		t.Fatal("открытый текст виден в зашифрованном файле")
	}
}

func TestEachCopyGetsItsOwnSalt(t *testing.T) {
	data := bytes.Repeat([]byte("a"), 1000)
	if bytes.Equal(seal(t, testKey, data), seal(t, testKey, data)) {
		t.Fatal("одинаковые данные дали одинаковый шифртекст: соль не случайна")
	}
}

func TestWrongKeyIsRejected(t *testing.T) {
	raw := seal(t, testKey, []byte("hello"))
	if _, err := unseal(bytes.Repeat([]byte{9}, 32), raw); !errors.Is(err, errCorrupt) {
		t.Fatalf("ошибка = %v, ожидалась errCorrupt", err)
	}
}

func TestFlippedByteIsDetected(t *testing.T) {
	raw := seal(t, testKey, bytes.Repeat([]byte("x"), 5000))
	raw[len(raw)/2] ^= 1
	if _, err := unseal(testKey, raw); !errors.Is(err, errCorrupt) {
		t.Fatalf("ошибка = %v, ожидалась errCorrupt", err)
	}
}

// Усечение ровно по границе блока: предыдущий блок шифровался как «не последний»,
// и признак последнего блока в связанных данных его выдаёт.
func TestTruncationAtABlockBoundaryIsDetected(t *testing.T) {
	data := make([]byte, 2*blockSize+5)
	rand.Read(data)
	raw := seal(t, testKey, data)

	const header = 4 + saltSize
	first := int(binary.BigEndian.Uint32(raw[header : header+4]))
	truncated := raw[:header+4+first]

	if _, err := unseal(testKey, truncated); !errors.Is(err, errCorrupt) {
		t.Fatalf("ошибка = %v, ожидалась errCorrupt", err)
	}
}

func TestWriterNeverHoldsMoreThanOneBlock(t *testing.T) {
	var out bytes.Buffer
	writer, err := newEncryptWriter(&out, testKey)
	if err != nil {
		t.Fatal(err)
	}
	chunk := make([]byte, 4096)
	for written := 0; written < 3*blockSize; written += len(chunk) {
		if _, err := writer.Write(chunk); err != nil {
			t.Fatal(err)
		}
		if cap(writer.buf) > blockSize {
			t.Fatalf("буфер вырос до %d байт", cap(writer.buf))
		}
	}
	writer.Close()
	if writer.plain != 3*blockSize {
		t.Fatalf("plain = %d, ожидалось %d", writer.plain, 3*blockSize)
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `go test ./internal/artifacts -run 'RoundTrip|Sealed|Salt|WrongKey|Flipped|Truncation|Writer'`
Expected: FAIL — `undefined: newEncryptWriter`.

- [ ] **Step 3: Write minimal implementation**

```go
// agent/internal/artifacts/crypto.go
package artifacts

import (
	"bufio"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"io"
)

const (
	magic     = "BGS1"
	saltSize  = 16
	blockSize = 1 << 20
	// Запас на тег GCM при проверке длины блока в чужом файле.
	tagSize = 16
)

var errCorrupt = errors.New("копия повреждена или ключ не подходит")

// deriveKey даёт каждой копии свой ключ: счётчик блока как nonce безопасен
// только пока ключ не повторяется между файлами.
func deriveKey(master, salt []byte) []byte {
	mac := hmac.New(sha256.New, master)
	mac.Write([]byte("barysguard-staging-v1"))
	mac.Write(salt)
	return mac.Sum(nil)
}

func newAEAD(master, salt []byte) (cipher.AEAD, error) {
	block, err := aes.NewCipher(deriveKey(master, salt))
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

func nonce(index uint64) []byte {
	out := make([]byte, 12)
	binary.BigEndian.PutUint64(out[4:], index)
	return out
}

func aad(index uint64, last bool) []byte {
	out := make([]byte, 9)
	binary.BigEndian.PutUint64(out, index)
	if last {
		out[8] = 1
	}
	return out
}

// encryptWriter шифрует поток блоками. Последний блок должен быть помечен как
// последний, поэтому полный блок отправляется, только когда пришли ещё данные.
type encryptWriter struct {
	dst   io.Writer
	aead  cipher.AEAD
	buf   []byte
	index uint64
	// plain — сколько байт открытого текста принято.
	plain int64
}

func newEncryptWriter(dst io.Writer, master []byte) (*encryptWriter, error) {
	salt := make([]byte, saltSize)
	if _, err := rand.Read(salt); err != nil {
		return nil, err
	}
	aead, err := newAEAD(master, salt)
	if err != nil {
		return nil, err
	}
	if _, err := dst.Write(append([]byte(magic), salt...)); err != nil {
		return nil, err
	}
	return &encryptWriter{dst: dst, aead: aead, buf: make([]byte, 0, blockSize)}, nil
}

func (w *encryptWriter) Write(p []byte) (int, error) {
	total := len(p)
	for len(p) > 0 {
		if len(w.buf) == blockSize {
			if err := w.flush(false); err != nil {
				return 0, err
			}
		}
		n := min(len(p), blockSize-len(w.buf))
		w.buf = append(w.buf, p[:n]...)
		p = p[n:]
	}
	w.plain += int64(total)
	return total, nil
}

func (w *encryptWriter) flush(last bool) error {
	sealed := w.aead.Seal(nil, nonce(w.index), w.buf, aad(w.index, last))
	var length [4]byte
	binary.BigEndian.PutUint32(length[:], uint32(len(sealed)))
	if _, err := w.dst.Write(length[:]); err != nil {
		return err
	}
	if _, err := w.dst.Write(sealed); err != nil {
		return err
	}
	w.index++
	w.buf = w.buf[:0]
	return nil
}

// Close записывает последний блок. Он может быть пустым: пустой файл — это
// один пустой блок, а не отсутствие блоков.
func (w *encryptWriter) Close() error { return w.flush(true) }

type decryptReader struct {
	src   *bufio.Reader
	aead  cipher.AEAD
	index uint64
	plain []byte
	done  bool
}

func newDecryptReader(src io.Reader, master []byte) (io.Reader, error) {
	reader := bufio.NewReader(src)
	header := make([]byte, len(magic)+saltSize)
	if _, err := io.ReadFull(reader, header); err != nil || string(header[:len(magic)]) != magic {
		return nil, errCorrupt
	}
	aead, err := newAEAD(master, header[len(magic):])
	if err != nil {
		return nil, err
	}
	return &decryptReader{src: reader, aead: aead}, nil
}

func (r *decryptReader) Read(p []byte) (int, error) {
	for len(r.plain) == 0 {
		if r.done {
			return 0, io.EOF
		}
		if err := r.next(); err != nil {
			return 0, err
		}
	}
	n := copy(p, r.plain)
	r.plain = r.plain[n:]
	return n, nil
}

func (r *decryptReader) next() error {
	var length [4]byte
	if _, err := io.ReadFull(r.src, length[:]); err != nil {
		return errCorrupt
	}
	size := binary.BigEndian.Uint32(length[:])
	if size < tagSize || size > blockSize+tagSize {
		return errCorrupt
	}
	sealed := make([]byte, size)
	if _, err := io.ReadFull(r.src, sealed); err != nil {
		return errCorrupt
	}
	// Блок последний, если за ним ничего нет.
	_, peek := r.src.Peek(1)
	last := peek == io.EOF

	plain, err := r.aead.Open(nil, nonce(r.index), sealed, aad(r.index, last))
	if err != nil {
		return errCorrupt
	}
	r.index++
	r.plain = plain
	r.done = last
	return nil
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `go test ./internal/artifacts && go vet ./internal/artifacts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/internal/artifacts/crypto.go agent/internal/artifacts/crypto_test.go
git commit -F - <<'EOF'
feat(agent): block-wise encryption for staged copies

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 8: Агент — каталог копий (`Store`) с бюджетом и вытеснением

**Files:**
- Create: `agent/internal/artifacts/store.go`
- Test: `agent/internal/artifacts/store_test.go`

**Interfaces:**
- Consumes: `newEncryptWriter`, `newDecryptReader`, `errCorrupt` (Task 7); `Config` (Task 6).
- Produces:
  - константы `SkipDisabled = "disabled"`, `SkipSize = "size"`, `SkipRate = "rate"`;
  - `type Sink interface { io.Writer; Commit(sha256 string) error; Abort() }`;
  - `type Stager interface { Begin(size int64) (Sink, string) }` — второй результат пуст, если копия делается, иначе одна из констант `Skip*`;
  - `type Entry struct { SHA256 string; Size int64; ModTime time.Time; name string }` (`Size` — размер открытого текста);
  - `NewStore(dir string, key []byte, now func() time.Time) (*Store, error)`; методы `SetConfig(Config)`, `Begin(size int64) (Sink, string)`, `Staged() <-chan struct{}`, `List() ([]Entry, error)` (старые первыми), `Open(Entry) (io.ReadCloser, error)`, `Remove(Entry)`, `TakeDropped() uint64`, а также неэкспортируемый `markDropped()`.
  - Имя файла копии: `<sha256>-<размер>.enc`.

- [ ] **Step 1: Write the failing test**

```go
// agent/internal/artifacts/store_test.go
package artifacts

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type fakeClock struct{ now time.Time }

func (c *fakeClock) Now() time.Time { return c.now }

func newTestStore(t *testing.T, mutate func(*Config)) (*Store, *fakeClock) {
	t.Helper()
	clock := &fakeClock{now: time.Date(2026, 10, 5, 12, 0, 0, 0, time.UTC)}
	store, err := NewStore(t.TempDir(), testKey, clock.Now)
	if err != nil {
		t.Fatal(err)
	}
	cfg := DefaultConfig()
	if mutate != nil {
		mutate(&cfg)
	}
	store.SetConfig(cfg)
	return store, clock
}

func shaOf(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// stage кладёт данные в каталог копий и возвращает хеш.
func stage(t *testing.T, store *Store, data []byte) string {
	t.Helper()
	sink, skip := store.Begin(int64(len(data)))
	if sink == nil {
		t.Fatalf("копия не начата: %q", skip)
	}
	if _, err := sink.Write(data); err != nil {
		t.Fatal(err)
	}
	sha := shaOf(data)
	if err := sink.Commit(sha); err != nil {
		t.Fatal(err)
	}
	return sha
}

func age(t *testing.T, store *Store, sha string, size int, at time.Time) {
	t.Helper()
	path := filepath.Join(store.dir, fmt.Sprintf("%s-%d.enc", sha, size))
	if err := os.Chtimes(path, at, at); err != nil {
		t.Fatal(err)
	}
}

func TestStagedCopyIsListedAndReadable(t *testing.T) {
	store, _ := newTestStore(t, nil)
	data := bytes.Repeat([]byte("отчёт "), 500)
	sha := stage(t, store, data)

	entries, err := store.List()
	if err != nil || len(entries) != 1 {
		t.Fatalf("List = %+v, %v", entries, err)
	}
	if entries[0].SHA256 != sha || entries[0].Size != int64(len(data)) {
		t.Fatalf("запись = %+v", entries[0])
	}

	reader, err := store.Open(entries[0])
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	got, err := io.ReadAll(reader)
	if err != nil || !bytes.Equal(got, data) {
		t.Fatalf("прочитано %d байт, ошибка %v", len(got), err)
	}

	select {
	case <-store.Staged():
	default:
		t.Fatal("воркер не получил сигнала о новой копии")
	}
}

func TestSecondCopyOfTheSameFileReplacesTheFirst(t *testing.T) {
	store, _ := newTestStore(t, nil)
	data := bytes.Repeat([]byte("x"), 2000)
	stage(t, store, data)
	stage(t, store, data)

	if entries, _ := store.List(); len(entries) != 1 {
		t.Fatalf("записей %d, ожидалась одна", len(entries))
	}
}

func TestAbortLeavesNothingBehind(t *testing.T) {
	store, _ := newTestStore(t, nil)
	sink, _ := store.Begin(10)
	sink.Write([]byte("0123456789"))
	sink.Abort()

	files, _ := os.ReadDir(store.dir)
	if len(files) != 0 {
		t.Fatalf("после Abort в каталоге %d файлов", len(files))
	}
}

func TestDisabledAndOversizedFilesAreNotStaged(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.MaxBytes = 100 })
	if sink, skip := store.Begin(101); sink != nil || skip != SkipSize {
		t.Fatalf("крупный файл: sink=%v skip=%q", sink, skip)
	}

	store.SetConfig(Config{Enabled: false, MaxBytes: 100, StagingMaxBytes: 1 << 20,
		UploadBytesPerSecond: 1, StageBytesPerMinute: 1 << 20})
	if sink, skip := store.Begin(1); sink != nil || skip != SkipDisabled {
		t.Fatalf("выключено: sink=%v skip=%q", sink, skip)
	}
}

func TestMinuteBudgetSkipsTheExcessAndCountsIt(t *testing.T) {
	store, clock := newTestStore(t, func(c *Config) { c.StageBytesPerMinute = 1500 })
	stage(t, store, bytes.Repeat([]byte("a"), 1000))

	if sink, skip := store.Begin(1000); sink != nil || skip != SkipRate {
		t.Fatalf("сверх бюджета: sink=%v skip=%q", sink, skip)
	}
	if got := store.TakeDropped(); got != 1 {
		t.Fatalf("TakeDropped = %d, ожидалось 1", got)
	}
	if got := store.TakeDropped(); got != 0 {
		t.Fatalf("повторный TakeDropped = %d, счётчик должен сбрасываться", got)
	}

	clock.now = clock.now.Add(61 * time.Second)
	if sink, skip := store.Begin(1000); sink == nil {
		t.Fatalf("новое окно: копия не начата (%q)", skip)
	} else {
		sink.Abort()
	}
}

func TestOldestCopiesAreEvictedWhenTheDirectoryIsFull(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.StagingMaxBytes = 2500 })
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

	a := bytes.Repeat([]byte("a"), 1000)
	b := bytes.Repeat([]byte("b"), 1000)
	c := bytes.Repeat([]byte("c"), 1000)
	stage(t, store, a)
	age(t, store, shaOf(a), len(a), base)
	stage(t, store, b)
	age(t, store, shaOf(b), len(b), base.Add(time.Hour))
	stage(t, store, c)

	entries, _ := store.List()
	if len(entries) != 2 {
		t.Fatalf("записей %d, ожидалось 2 после вытеснения", len(entries))
	}
	for _, e := range entries {
		if e.SHA256 == shaOf(a) {
			t.Fatal("самая старая копия не вытеснена")
		}
	}
	if got := store.TakeDropped(); got != 1 {
		t.Fatalf("вытеснено %d, ожидалось 1", got)
	}
}

// Файл вырос между stat и чтением: копия не должна превысить предел.
func TestWritePastTheLimitFails(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.MaxBytes = 1000 })
	sink, _ := store.Begin(10)
	defer sink.Abort()

	if _, err := sink.Write(make([]byte, 2000)); err == nil {
		t.Fatal("запись сверх предела должна завершиться ошибкой")
	}
}

func TestCommitRejectsAHashThatCouldEscapeTheDirectory(t *testing.T) {
	store, _ := newTestStore(t, nil)
	sink, _ := store.Begin(3)
	sink.Write([]byte("abc"))

	if err := sink.Commit("../../evil"); err == nil {
		t.Fatal("недопустимый хеш принят")
	}
	if files, _ := os.ReadDir(store.dir); len(files) != 0 {
		t.Fatalf("после отказа в каталоге %d файлов", len(files))
	}
}

func TestLeftoverTemporaryFilesAreRemovedAtStart(t *testing.T) {
	dir := t.TempDir()
	leftover := filepath.Join(dir, "123.tmp")
	os.WriteFile(leftover, []byte("обрыв"), 0o600)

	if _, err := NewStore(dir, testKey, time.Now); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(leftover); !os.IsNotExist(err) {
		t.Fatal("временный файл прошлого запуска остался")
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `go test ./internal/artifacts -run 'Staged|Abort|Disabled|Budget|Evicted|WritePast|Commit|Leftover|SecondCopy'`
Expected: FAIL — `undefined: NewStore`.

- [ ] **Step 3: Write minimal implementation**

```go
// agent/internal/artifacts/store.go
package artifacts

import (
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"sync"
	"time"
)

// Причины, по которым копию не делают (второй результат Begin).
const (
	SkipDisabled = "disabled"
	SkipSize     = "size"
	SkipRate     = "rate"
)

var (
	errTooLarge  = errors.New("файл больше предела копирования")
	shaPattern   = regexp.MustCompile(`^[0-9a-f]{64}$`)
	entryPattern = regexp.MustCompile(`^([0-9a-f]{64})-(\d+)\.enc$`)
)

// Sink принимает копию читаемого файла. Ошибка Write означает «копию не
// продолжать»: хеширование от неё не зависит.
type Sink interface {
	io.Writer
	// Commit завершает копию, называя её хешем содержимого.
	Commit(sha256 string) error
	Abort()
}

// Stager решает, нужна ли копия, и открывает приёмник. Второй результат пуст,
// если копия делается, иначе это одна из констант Skip*.
type Stager interface {
	Begin(size int64) (Sink, string)
}

// Entry — копия в каталоге. Size — размер открытого текста.
type Entry struct {
	SHA256  string
	Size    int64
	ModTime time.Time
	name    string
}

// Store — каталог зашифрованных копий со сроком жизни «до загрузки».
type Store struct {
	dir    string
	key    []byte
	now    func() time.Time
	staged chan struct{}

	mu          sync.Mutex
	cfg         Config
	windowStart time.Time
	windowBytes int64
	dropped     uint64
}

// NewStore открывает каталог копий. Права на него выставляет вызывающий
// (platform.Guard.SecureDir). Временные файлы прошлого запуска — обрывки
// незавершённых копий — удаляются.
func NewStore(dir string, key []byte, now func() time.Time) (*Store, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	leftovers, _ := filepath.Glob(filepath.Join(dir, "*.tmp"))
	for _, path := range leftovers {
		os.Remove(path)
	}
	return &Store{
		dir: dir, key: key, now: now, cfg: DefaultConfig(),
		staged: make(chan struct{}, 1),
	}, nil
}

func (s *Store) SetConfig(cfg Config) {
	s.mu.Lock()
	s.cfg = cfg
	s.mu.Unlock()
}

// Staged сообщает воркеру о новой копии. Канал с буфером в один сигнал:
// несколько копий подряд будят воркер один раз.
func (s *Store) Staged() <-chan struct{} { return s.staged }

// TakeDropped отдаёт, сколько копий потеряно с прошлого вызова (бюджет,
// вытеснение, отказ сервера), и обнуляет счётчик.
func (s *Store) TakeDropped() uint64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := s.dropped
	s.dropped = 0
	return n
}

func (s *Store) markDropped() {
	s.mu.Lock()
	s.dropped++
	s.mu.Unlock()
}

// Begin открывает приёмник копии. Размер известен из stat до чтения, поэтому
// крупный файл не копируется вовсе.
func (s *Store) Begin(size int64) (Sink, string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	cfg := s.cfg
	if !cfg.Enabled {
		return nil, SkipDisabled
	}
	if size > cfg.MaxBytes || size > cfg.StagingMaxBytes {
		return nil, SkipSize
	}

	now := s.now()
	if now.Sub(s.windowStart) >= time.Minute {
		s.windowStart, s.windowBytes = now, 0
	}
	if s.windowBytes+size > cfg.StageBytesPerMinute {
		s.dropped++
		return nil, SkipRate
	}

	s.makeRoomLocked(size, cfg.StagingMaxBytes)

	file, err := os.CreateTemp(s.dir, "*.tmp")
	if err != nil {
		slog.Warn("копия файла не начата", "error", err)
		return nil, SkipDisabled
	}
	enc, err := newEncryptWriter(file, s.key)
	if err != nil {
		file.Close()
		os.Remove(file.Name())
		slog.Warn("копия файла не начата", "error", err)
		return nil, SkipDisabled
	}
	s.windowBytes += size
	return &sink{store: s, file: file, enc: enc, limit: cfg.MaxBytes}, ""
}

// makeRoomLocked вытесняет самые старые копии, пока новая не поместится.
// Сумма считается по размеру открытого текста: шифрование добавляет доли процента.
func (s *Store) makeRoomLocked(size, limit int64) {
	entries, err := s.List()
	if err != nil {
		return
	}
	var total int64
	for _, e := range entries {
		total += e.Size
	}
	for len(entries) > 0 && total+size > limit {
		oldest := entries[0]
		entries = entries[1:]
		if os.Remove(filepath.Join(s.dir, oldest.name)) == nil {
			s.dropped++
		}
		total -= oldest.Size
	}
}

// List отдаёт копии, старые первыми.
func (s *Store) List() ([]Entry, error) {
	files, err := os.ReadDir(s.dir)
	if err != nil {
		return nil, err
	}
	var out []Entry
	for _, file := range files {
		match := entryPattern.FindStringSubmatch(file.Name())
		if match == nil {
			continue
		}
		info, err := file.Info()
		if err != nil {
			continue
		}
		size, _ := strconv.ParseInt(match[2], 10, 64)
		out = append(out, Entry{SHA256: match[1], Size: size, ModTime: info.ModTime(), name: file.Name()})
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].ModTime.Equal(out[j].ModTime) {
			return out[i].ModTime.Before(out[j].ModTime)
		}
		return out[i].name < out[j].name
	})
	return out, nil
}

type readCloser struct {
	io.Reader
	io.Closer
}

// Open отдаёт расшифрованное содержимое копии.
func (s *Store) Open(e Entry) (io.ReadCloser, error) {
	file, err := os.Open(filepath.Join(s.dir, e.name))
	if err != nil {
		return nil, err
	}
	reader, err := newDecryptReader(file, s.key)
	if err != nil {
		file.Close()
		return nil, err
	}
	return readCloser{reader, file}, nil
}

func (s *Store) Remove(e Entry) {
	os.Remove(filepath.Join(s.dir, e.name))
}

type sink struct {
	store *Store
	file  *os.File
	enc   *encryptWriter
	limit int64
	done  bool
}

func (k *sink) Write(p []byte) (int, error) {
	// Файл мог вырасти между stat и чтением: предел действует на фактические байты.
	if k.enc.plain+int64(len(p)) > k.limit {
		return 0, errTooLarge
	}
	return k.enc.Write(p)
}

func (k *sink) Commit(sha256 string) error {
	if k.done {
		return nil
	}
	k.done = true
	// Хеш становится именем файла: он обязан быть именно хешем.
	if !shaPattern.MatchString(sha256) {
		k.discard()
		return fmt.Errorf("недопустимый хеш копии %q", sha256)
	}
	if err := k.enc.Close(); err != nil {
		k.discard()
		return err
	}
	if err := k.file.Sync(); err != nil {
		k.discard()
		return err
	}
	temporary := k.file.Name()
	if err := k.file.Close(); err != nil {
		os.Remove(temporary)
		return err
	}
	target := filepath.Join(k.store.dir, fmt.Sprintf("%s-%d.enc", sha256, k.enc.plain))
	if err := os.Rename(temporary, target); err != nil {
		os.Remove(temporary)
		return err
	}
	select {
	case k.store.staged <- struct{}{}:
	default:
	}
	return nil
}

func (k *sink) Abort() {
	if k.done {
		return
	}
	k.done = true
	k.discard()
}

func (k *sink) discard() {
	k.file.Close()
	os.Remove(k.file.Name())
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `go test ./internal/artifacts && go vet ./internal/artifacts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/internal/artifacts/store.go agent/internal/artifacts/store_test.go
git commit -F - <<'EOF'
feat(agent): staging store with minute budget and eviction

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 9: Агент — методы транспорта для загрузки

**Files:**
- Create: `agent/internal/transport/artifacts.go`
- Test: `agent/internal/transport/artifacts_test.go`

**Interfaces:**
- Produces: типы `ArtifactOpenRequest`, `ArtifactOpenResponse{Status, UploadID string; ReceivedBytes, ChunkSize int64}`, `ArtifactChunkResponse{ReceivedBytes int64; Status string}`; `(*Client).OpenArtifact(ctx, sha256 string, size int64) (ArtifactOpenResponse, error)`, `(*Client).UploadChunk(ctx, uploadID string, offset int64, data []byte) (ArtifactChunkResponse, error)`, `OffsetMismatch(err error) (int64, bool)`.
- Ошибки HTTP возвращаются как `*StatusError` (его `Code`, `Body`, `RetryAfter` используют воркер и `OffsetMismatch`).

- [ ] **Step 1: Write the failing test**

```go
// agent/internal/transport/artifacts_test.go
package transport_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/barysguard/agent/internal/transport"
)

func TestOpenArtifactPostsHashAndSize(t *testing.T) {
	sha := strings.Repeat("ab", 32)
	var gotMethod, gotPath string
	var gotBody map[string]any
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod, gotPath = r.Method, r.URL.Path
		json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte(`{"status":"upload","upload_id":"u-1","received_bytes":5,"chunk_size":1048576}`))
	}))

	got, err := client.OpenArtifact(context.Background(), sha, 7)
	if err != nil {
		t.Fatalf("OpenArtifact: %v", err)
	}

	if gotMethod != http.MethodPost || gotPath != "/gateway/v1/artifacts" {
		t.Fatalf("запрос %s %s", gotMethod, gotPath)
	}
	if gotBody["sha256"] != sha || gotBody["size"] != float64(7) {
		t.Fatalf("тело = %v", gotBody)
	}
	want := transport.ArtifactOpenResponse{Status: "upload", UploadID: "u-1", ReceivedBytes: 5, ChunkSize: 1048576}
	if got != want {
		t.Fatalf("ответ = %+v, ожидалось %+v", got, want)
	}
}

func TestUploadChunkSendsOffsetAndRawBytes(t *testing.T) {
	var gotMethod, gotPath, gotOffset, gotType string
	var gotBody []byte
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod, gotPath = r.Method, r.URL.Path
		gotOffset, gotType = r.Header.Get("X-Offset"), r.Header.Get("Content-Type")
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"received_bytes":13,"status":"partial"}`))
	}))

	got, err := client.UploadChunk(context.Background(), "u-1", 10, []byte("abc"))
	if err != nil {
		t.Fatalf("UploadChunk: %v", err)
	}

	if gotMethod != http.MethodPut || gotPath != "/gateway/v1/artifacts/u-1" {
		t.Fatalf("запрос %s %s", gotMethod, gotPath)
	}
	if gotOffset != "10" || gotType != "application/octet-stream" || string(gotBody) != "abc" {
		t.Fatalf("offset=%q type=%q body=%q", gotOffset, gotType, gotBody)
	}
	if got.ReceivedBytes != 13 || got.Status != "partial" {
		t.Fatalf("ответ = %+v", got)
	}
}

func TestOffsetMismatchIsReadFromA409(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
		w.Write([]byte(`{"received_bytes":1024}`))
	}))

	_, err := client.UploadChunk(context.Background(), "u-1", 0, []byte("x"))
	received, ok := transport.OffsetMismatch(err)
	if !ok || received != 1024 {
		t.Fatalf("OffsetMismatch = %d, %v", received, ok)
	}
}

func TestOffsetMismatchIgnoresOtherErrors(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "сбой", http.StatusServiceUnavailable)
	}))

	_, err := client.UploadChunk(context.Background(), "u-1", 0, []byte("x"))
	if _, ok := transport.OffsetMismatch(err); ok {
		t.Fatal("503 принят за расхождение смещения")
	}
	if _, ok := transport.OffsetMismatch(nil); ok {
		t.Fatal("nil принят за расхождение смещения")
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `go test ./internal/transport -run 'Artifact|Chunk|OffsetMismatch'`
Expected: FAIL — `client.OpenArtifact undefined`.

- [ ] **Step 3: Write minimal implementation**

```go
// agent/internal/transport/artifacts.go
package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
)

type ArtifactOpenRequest struct {
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

// ArtifactOpenResponse: Status "exists" — сервер уже имеет содержимое,
// "upload" — выдана (или продолжена) сессия загрузки.
type ArtifactOpenResponse struct {
	Status        string `json:"status"`
	UploadID      string `json:"upload_id"`
	ReceivedBytes int64  `json:"received_bytes"`
	ChunkSize     int64  `json:"chunk_size"`
}

// ArtifactChunkResponse: Status "partial" или "complete".
type ArtifactChunkResponse struct {
	ReceivedBytes int64  `json:"received_bytes"`
	Status        string `json:"status"`
}

// OpenArtifact открывает загрузку или узнаёт, что сервер уже имеет файл.
func (c *Client) OpenArtifact(ctx context.Context, sha256 string, size int64) (ArtifactOpenResponse, error) {
	var out ArtifactOpenResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "artifacts"},
		ArtifactOpenRequest{SHA256: sha256, Size: size}, &out)
	return out, err
}

// UploadChunk отправляет чанк. Тело — сырые байты, смещение в заголовке:
// сервер принимает только чанк, начинающийся ровно там, где кончился прошлый.
func (c *Client) UploadChunk(ctx context.Context, uploadID string, offset int64, data []byte) (ArtifactChunkResponse, error) {
	req, err := http.NewRequest(
		http.MethodPut,
		c.base.JoinPath("gateway", "v1", "artifacts", uploadID).String(),
		bytes.NewReader(data),
	)
	if err != nil {
		return ArtifactChunkResponse{}, err
	}
	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("X-Offset", strconv.FormatInt(offset, 10))

	resp, err := c.do(ctx, req)
	if err != nil {
		return ArtifactChunkResponse{}, err
	}
	defer resp.Body.Close()

	var out ArtifactChunkResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return ArtifactChunkResponse{}, fmt.Errorf("разбор ответа загрузки чанка: %w", err)
	}
	return out, nil
}

// OffsetMismatch извлекает смещение, которое ждёт сервер, из ответа 409.
func OffsetMismatch(err error) (int64, bool) {
	var status *StatusError
	if !errors.As(err, &status) || status.Code != http.StatusConflict {
		return 0, false
	}
	var body struct {
		ReceivedBytes int64 `json:"received_bytes"`
	}
	if json.Unmarshal([]byte(status.Body), &body) != nil {
		return 0, false
	}
	return body.ReceivedBytes, true
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `go test ./internal/transport && go vet ./internal/transport`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/internal/transport/artifacts.go agent/internal/transport/artifacts_test.go
git commit -F - <<'EOF'
feat(agent): transport methods for resumable artifact upload

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 10: Агент — воркер загрузки

**Files:**
- Create: `agent/internal/artifacts/worker.go`, `agent/internal/artifacts/priority_windows.go`, `agent/internal/artifacts/priority_other.go`
- Test: `agent/internal/artifacts/worker_test.go`

**Interfaces:**
- Consumes: `Store`, `Entry`, `Config`, `errCorrupt` (Tasks 6–8); `transport.ArtifactOpenResponse`, `transport.ArtifactChunkResponse`, `transport.StatusError`, `transport.OffsetMismatch` (Task 9); `events.NewEnvelope`, `events.ChannelAgent`, `events.SeverityLow`, `events.Envelope`.
- Produces: `type Transport interface { OpenArtifact(...); UploadChunk(...) }` (подмножество `*transport.Client`), `NewWorker(store *Store, api Transport) *Worker`; методы `SetConfig(Config)`, `Run(ctx context.Context, emit func(events.Envelope))`, `Pass(ctx context.Context)`; неэкспортируемые `upload`, `settle`, `reportDropped`, поля `now func() time.Time`, `sleep func(ctx context.Context, d time.Duration)` (подменяются в тестах).

- [ ] **Step 1: Write the failing test**

```go
// agent/internal/artifacts/worker_test.go
package artifacts

import (
	"bytes"
	"context"
	"crypto/rand"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/transport"
)

// fakeServer — сервер загрузки в памяти.
type fakeServer struct {
	chunkSize int64
	size      int64
	received  []byte
	known     bool
	staleOpen bool // Open сообщает 0 принятых байт, хотя они есть

	failOpen  error
	failChunk func(offset int64) error

	opens, chunks int
	offsets       []int64
	maxChunk      int
}

func (f *fakeServer) OpenArtifact(_ context.Context, _ string, size int64) (transport.ArtifactOpenResponse, error) {
	f.opens++
	if f.failOpen != nil {
		return transport.ArtifactOpenResponse{}, f.failOpen
	}
	if f.known {
		return transport.ArtifactOpenResponse{Status: "exists"}, nil
	}
	f.size = size
	received := int64(len(f.received))
	if f.staleOpen {
		received = 0
	}
	return transport.ArtifactOpenResponse{
		Status: "upload", UploadID: "u-1", ReceivedBytes: received, ChunkSize: f.chunkSize,
	}, nil
}

func (f *fakeServer) UploadChunk(_ context.Context, _ string, offset int64, data []byte) (transport.ArtifactChunkResponse, error) {
	f.chunks++
	f.offsets = append(f.offsets, offset)
	f.maxChunk = max(f.maxChunk, len(data))
	if f.failChunk != nil {
		if err := f.failChunk(offset); err != nil {
			return transport.ArtifactChunkResponse{}, err
		}
	}
	if offset != int64(len(f.received)) {
		return transport.ArtifactChunkResponse{}, &transport.StatusError{
			Code: 409, Body: fmt.Sprintf(`{"received_bytes":%d}`, len(f.received)),
		}
	}
	f.received = append(f.received, data...)
	status := "partial"
	if int64(len(f.received)) >= f.size {
		status = "complete"
	}
	return transport.ArtifactChunkResponse{ReceivedBytes: int64(len(f.received)), Status: status}, nil
}

type workerHarness struct {
	store  *Store
	clock  *fakeClock
	api    *fakeServer
	worker *Worker
	slept  []time.Duration
}

func newWorkerHarness(t *testing.T) *workerHarness {
	t.Helper()
	store, clock := newTestStore(t, nil)
	api := &fakeServer{chunkSize: 1 << 20}
	h := &workerHarness{store: store, clock: clock, api: api, worker: NewWorker(store, api)}
	h.worker.now = clock.Now
	h.worker.sleep = func(_ context.Context, d time.Duration) { h.slept = append(h.slept, d) }
	// Скорость не мешает тестам, кроме теста пейсинга.
	cfg := DefaultConfig()
	cfg.UploadBytesPerSecond = 1 << 40
	h.worker.SetConfig(cfg)
	return h
}

func randomBytes(n int) []byte {
	out := make([]byte, n)
	rand.Read(out)
	return out
}

func (h *workerHarness) pending(t *testing.T) int {
	t.Helper()
	entries, err := h.store.List()
	if err != nil {
		t.Fatal(err)
	}
	return len(entries)
}

func TestFileIsUploadedInOrderedChunksAndTheCopyIsRemoved(t *testing.T) {
	h := newWorkerHarness(t)
	data := randomBytes(2*(1<<20) + 512*1024) // 2,5 МиБ
	stage(t, h.store, data)

	h.worker.Pass(context.Background())

	if !bytes.Equal(h.api.received, data) {
		t.Fatalf("сервер получил %d байт, ожидалось %d", len(h.api.received), len(data))
	}
	if fmt.Sprint(h.api.offsets) != fmt.Sprint([]int64{0, 1 << 20, 2 << 20}) {
		t.Fatalf("смещения = %v", h.api.offsets)
	}
	if h.api.maxChunk > 1<<20 {
		t.Fatalf("чанк в %d байт: больше предела", h.api.maxChunk)
	}
	if h.pending(t) != 0 {
		t.Fatal("копия не удалена после успешной загрузки")
	}
}

func TestKnownArtifactIsNotTransferred(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.known = true
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())

	if h.api.chunks != 0 {
		t.Fatalf("отправлено %d чанков для известного артефакта", h.api.chunks)
	}
	if h.pending(t) != 0 {
		t.Fatal("копия известного артефакта осталась")
	}
}

func TestUploadResumesFromTheServersOffset(t *testing.T) {
	h := newWorkerHarness(t)
	data := randomBytes(2*(1<<20) + 100)
	h.api.received = append([]byte(nil), data[:1<<20]...) // первая часть уже на сервере
	stage(t, h.store, data)

	h.worker.Pass(context.Background())

	if len(h.api.offsets) == 0 || h.api.offsets[0] != 1<<20 {
		t.Fatalf("первый чанк со смещением %v, ожидалось 1048576", h.api.offsets)
	}
	if !bytes.Equal(h.api.received, data) {
		t.Fatal("итоговое содержимое не совпало")
	}
}

func TestOffsetMismatchRepositionsInsteadOfFailing(t *testing.T) {
	h := newWorkerHarness(t)
	data := randomBytes(2*(1<<20) + 100)
	h.api.received = append([]byte(nil), data[:1<<20]...)
	h.api.staleOpen = true // сессия говорит «0», хотя сервер ждёт 1 МиБ
	stage(t, h.store, data)

	h.worker.Pass(context.Background())

	if !bytes.Equal(h.api.received, data) {
		t.Fatal("после 409 загрузка не дошла до конца")
	}
}

// Сервер без конца отвечает 409: воркер обязан выйти из прохода, а не крутиться.
func TestEndlessOffsetMismatchStopsThePass(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failChunk = func(int64) error {
		return &transport.StatusError{Code: 409, Body: `{"received_bytes":0}`}
	}
	stage(t, h.store, randomBytes(3000))

	h.worker.Pass(context.Background())

	if h.pending(t) != 1 {
		t.Fatal("копия должна остаться для следующей попытки")
	}
}

func TestServerErrorKeepsTheCopyAndBacksOff(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failOpen = &transport.StatusError{Code: 503}
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())
	h.worker.Pass(context.Background()) // пауза ещё не прошла
	if h.api.opens != 1 {
		t.Fatalf("за время паузы сделано %d обращений, ожидалось 1", h.api.opens)
	}
	if h.pending(t) != 1 {
		t.Fatal("копия потеряна при временной ошибке")
	}

	h.clock.now = h.clock.now.Add(6 * time.Second)
	h.worker.Pass(context.Background())
	if h.api.opens != 2 {
		t.Fatalf("после паузы обращений %d, ожидалось 2", h.api.opens)
	}
}

func TestRetryAfterIsHonoured(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failOpen = &transport.StatusError{Code: 429, RetryAfter: 2 * time.Minute}
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())
	h.clock.now = h.clock.now.Add(time.Minute)
	h.worker.Pass(context.Background())

	if h.api.opens != 1 {
		t.Fatalf("обращений %d: Retry-After проигнорирован", h.api.opens)
	}
}

func TestClientErrorDropsTheCopyAndIsReported(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failChunk = func(int64) error { return &transport.StatusError{Code: 413} }
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())

	if h.pending(t) != 0 {
		t.Fatal("копия, которую сервер отверг навсегда, осталась")
	}
	if got := h.store.TakeDropped(); got != 1 {
		t.Fatalf("TakeDropped = %d, ожидалось 1", got)
	}
}

func TestHashMismatchIsRetriedOnceThenDropped(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failChunk = func(int64) error { return &transport.StatusError{Code: 422} }
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())
	if h.pending(t) != 1 {
		t.Fatal("первая 422 должна оставить копию для одного повтора")
	}

	h.clock.now = h.clock.now.Add(6 * time.Second)
	h.worker.Pass(context.Background())
	if h.pending(t) != 0 || h.store.TakeDropped() != 1 {
		t.Fatal("после второй 422 копия должна быть снята и учтена как потеря")
	}
}

func TestEmptyFileIsUploadedWithOneEmptyChunk(t *testing.T) {
	h := newWorkerHarness(t)
	stage(t, h.store, nil)

	h.worker.Pass(context.Background())

	if h.api.chunks != 1 || len(h.api.received) != 0 {
		t.Fatalf("чанков %d, байт %d: ожидался один пустой чанк", h.api.chunks, len(h.api.received))
	}
	if h.pending(t) != 0 {
		t.Fatal("копия пустого файла не удалена")
	}
}

func TestUploadSpeedIsPaced(t *testing.T) {
	h := newWorkerHarness(t)
	cfg := DefaultConfig()
	cfg.UploadBytesPerSecond = 1 << 20 // 1 МиБ/с
	h.worker.SetConfig(cfg)
	stage(t, h.store, randomBytes(1<<20))

	h.worker.Pass(context.Background())

	var total time.Duration
	for _, d := range h.slept {
		total += d
	}
	if total < 900*time.Millisecond || total > 1100*time.Millisecond {
		t.Fatalf("пауза %v: 1 МиБ при 1 МиБ/с должен занимать около секунды", total)
	}
}

func TestMissingStagedCopyIsDroppedNotRetriedForever(t *testing.T) {
	h := newWorkerHarness(t)
	stage(t, h.store, randomBytes(3000))
	entries, _ := h.store.List()
	// Файл исчез между перечислением и чтением (антивирус, чистка диска).
	os.Remove(filepath.Join(h.store.dir, entries[0].name))

	err := h.worker.upload(context.Background(), entries[0])
	h.worker.settle(context.Background(), entries[0], err)

	if got := h.store.TakeDropped(); got != 1 {
		t.Fatalf("TakeDropped = %d, потеря не учтена", got)
	}
	h.clock.now = h.clock.now.Add(time.Hour)
	h.worker.Pass(context.Background())
	if h.api.opens != 1 {
		t.Fatalf("обращений %d: исчезнувшая копия не должна повторяться", h.api.opens)
	}
}

func TestCorruptStagedCopyIsDropped(t *testing.T) {
	h := newWorkerHarness(t)
	stage(t, h.store, randomBytes(3000))
	entries, _ := h.store.List()
	path := filepath.Join(h.store.dir, entries[0].name)
	raw, _ := os.ReadFile(path)
	raw[len(raw)/2] ^= 1
	os.WriteFile(path, raw, 0o600)

	h.worker.Pass(context.Background())

	if h.pending(t) != 0 || h.store.TakeDropped() != 1 {
		t.Fatal("повреждённая копия должна быть снята и учтена как потеря")
	}
}

func TestDroppedCopiesAreReportedOnceAsAnAgentEvent(t *testing.T) {
	h := newWorkerHarness(t)
	h.store.markDropped()
	h.store.markDropped()

	var got []events.Envelope
	h.worker.reportDropped(func(e events.Envelope) { got = append(got, e) })
	h.worker.reportDropped(func(e events.Envelope) { got = append(got, e) })

	if len(got) != 1 {
		t.Fatalf("событий %d, ожидалось 1", len(got))
	}
	if got[0].Channel != events.ChannelAgent || got[0].Action != "artifact_dropped" {
		t.Fatalf("событие = %+v", got[0])
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `go test ./internal/artifacts -run 'Worker|Uploaded|Known|Resumes|Offset|ServerError|RetryAfter|ClientError|HashMismatch|EmptyFile|Paced|Missing|Corrupt|Dropped'`
Expected: FAIL — `undefined: NewWorker`.

- [ ] **Step 3: Write minimal implementation**

```go
// agent/internal/artifacts/worker.go
package artifacts

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/transport"
)

const (
	passInterval = 15 * time.Second
	backoffBase  = 5 * time.Second
	backoffMax   = 5 * time.Minute
	maxChunk     = 1 << 20
	// Сколько раз за одну загрузку принять смещение сервера, прежде чем
	// отложить файл: сервер, отвечающий 409 вечно, не должен держать воркер.
	maxRepositions = 5
)

var errIncomplete = errors.New("сервер не завершил загрузку после последнего чанка")

// Transport — то, что воркеру нужно от клиента шлюза.
type Transport interface {
	OpenArtifact(ctx context.Context, sha256 string, size int64) (transport.ArtifactOpenResponse, error)
	UploadChunk(ctx context.Context, uploadID string, offset int64, data []byte) (transport.ArtifactChunkResponse, error)
}

type retryState struct {
	attempts int
	notBefore time.Time
}

// Worker отправляет копии на сервер: один файл за раз, в фоновом режиме потока,
// с ограничением скорости. Pass вызывается только из одной горутины.
type Worker struct {
	store *Store
	api   Transport
	now   func() time.Time
	sleep func(ctx context.Context, d time.Duration)

	mu  sync.Mutex
	cfg Config

	retry  map[string]*retryState
	resets map[string]int
}

func NewWorker(store *Store, api Transport) *Worker {
	return &Worker{
		store: store, api: api, now: time.Now, sleep: sleepContext, cfg: DefaultConfig(),
		retry: map[string]*retryState{}, resets: map[string]int{},
	}
}

func sleepContext(ctx context.Context, d time.Duration) {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}

// SetConfig применяет новый раздел конфигурации и к воркеру, и к каталогу копий.
func (w *Worker) SetConfig(cfg Config) {
	w.mu.Lock()
	w.cfg = cfg
	w.mu.Unlock()
	w.store.SetConfig(cfg)
}

func (w *Worker) config() Config {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.cfg
}

// Run крутит проходы, пока не отменён ctx. Копии, оставшиеся от прошлого
// запуска, подхватываются первым же проходом.
func (w *Worker) Run(ctx context.Context, emit func(events.Envelope)) {
	defer enterBackground()()

	ticker := time.NewTicker(passInterval)
	defer ticker.Stop()
	for {
		w.Pass(ctx)
		w.reportDropped(emit)
		select {
		case <-ctx.Done():
			return
		case <-w.store.Staged():
		case <-ticker.C:
		}
	}
}

// Pass загружает по одной все копии, которым пришёл срок.
func (w *Worker) Pass(ctx context.Context) {
	entries, err := w.store.List()
	if err != nil {
		slog.Warn("каталог копий не прочитан", "error", err)
		return
	}
	for _, entry := range entries {
		if ctx.Err() != nil {
			return
		}
		if state := w.retry[entry.SHA256]; state != nil && w.now().Before(state.notBefore) {
			continue
		}
		w.settle(ctx, entry, w.upload(ctx, entry))
	}
}

func (w *Worker) upload(ctx context.Context, entry Entry) error {
	open, err := w.api.OpenArtifact(ctx, entry.SHA256, entry.Size)
	if err != nil {
		return err
	}
	if open.Status == "exists" {
		w.store.Remove(entry)
		return nil
	}

	chunk := open.ChunkSize
	if chunk <= 0 || chunk > maxChunk {
		chunk = maxChunk
	}
	source := &chunkSource{store: w.store, entry: entry}
	defer source.close()

	offset := open.ReceivedBytes
	repositions := 0
	for {
		data, err := source.read(offset, chunk)
		if err != nil {
			return err
		}
		// Пустой файл — единственный случай, когда пустой чанк допустим.
		if len(data) == 0 && entry.Size > 0 {
			return errIncomplete
		}

		started := w.now()
		resp, err := w.api.UploadChunk(ctx, open.UploadID, offset, data)
		if received, mismatch := transport.OffsetMismatch(err); mismatch {
			repositions++
			if repositions > maxRepositions {
				return fmt.Errorf("сервер %d раз подряд не принял смещение", repositions)
			}
			offset = received
			continue
		}
		if err != nil {
			return err
		}
		w.pace(ctx, len(data), started)

		if resp.Status == "complete" {
			w.store.Remove(entry)
			return nil
		}
		offset = resp.ReceivedBytes
	}
}

// pace растягивает отправку до upload_bytes_per_second: фоновая загрузка не
// должна занимать канал рабочей станции.
func (w *Worker) pace(ctx context.Context, sent int, started time.Time) {
	bps := w.config().UploadBytesPerSecond
	if bps <= 0 {
		return
	}
	want := time.Duration(float64(sent) / float64(bps) * float64(time.Second))
	if remaining := want - w.now().Sub(started); remaining > 0 {
		w.sleep(ctx, remaining)
	}
}

// settle разбирает итог попытки: успех, повтор позже либо отказ навсегда.
func (w *Worker) settle(ctx context.Context, entry Entry, err error) {
	if err == nil {
		delete(w.retry, entry.SHA256)
		delete(w.resets, entry.SHA256)
		return
	}
	if ctx.Err() != nil {
		return
	}
	if errors.Is(err, fs.ErrNotExist) || errors.Is(err, errCorrupt) {
		slog.Warn("копия файла недоступна, загрузка отменена", "sha256", entry.SHA256, "error", err)
		w.drop(entry)
		return
	}

	var status *transport.StatusError
	if errors.As(err, &status) {
		switch {
		case status.Code == http.StatusUnprocessableEntity:
			// Хеш не сошёлся: один повтор с начала, затем отказ.
			w.resets[entry.SHA256]++
			if w.resets[entry.SHA256] > 1 {
				slog.Warn("сервер не принял содержимое дважды", "sha256", entry.SHA256)
				w.drop(entry)
				return
			}
			w.backoff(entry, 0)
		case status.Code == http.StatusNotFound:
			// Сессия истекла: следующий проход откроет новую.
			w.backoff(entry, 0)
		case status.Code == http.StatusTooManyRequests || status.Code >= 500:
			w.backoff(entry, status.RetryAfter)
		default:
			slog.Warn("сервер отверг файл", "sha256", entry.SHA256, "status", status.Code)
			w.drop(entry)
		}
		return
	}

	slog.Warn("загрузка файла не удалась, повтор позже", "sha256", entry.SHA256, "error", err)
	w.backoff(entry, 0)
}

func (w *Worker) backoff(entry Entry, retryAfter time.Duration) {
	state := w.retry[entry.SHA256]
	if state == nil {
		state = &retryState{}
		w.retry[entry.SHA256] = state
	}
	state.attempts++
	delay := min(backoffBase<<min(state.attempts-1, 6), backoffMax)
	if retryAfter > delay {
		delay = retryAfter
	}
	state.notBefore = w.now().Add(delay)
}

func (w *Worker) drop(entry Entry) {
	w.store.Remove(entry)
	w.store.markDropped()
	delete(w.retry, entry.SHA256)
	delete(w.resets, entry.SHA256)
}

// reportDropped сообщает серверу о потерянных копиях одним событием.
func (w *Worker) reportDropped(emit func(events.Envelope)) {
	n := w.store.TakeDropped()
	if n == 0 || emit == nil {
		return
	}
	env, err := events.NewEnvelope(events.ChannelAgent, "artifact_dropped", events.SeverityLow, map[string]any{
		"component": "artifacts",
		"detail":    fmt.Sprintf("содержимое не сохранено или не загружено: %d файл(ов); превышен бюджет, место на диске или сервер отказал", n),
	})
	if err != nil {
		slog.Error("событие о потере копий не создано", "error", err)
		return
	}
	emit(env)
}

// chunkSource читает копию последовательно и переоткрывает её только при
// смене смещения: расшифровывать файл с начала на каждый чанк значило бы
// квадратичную нагрузку на процессор.
type chunkSource struct {
	store  *Store
	entry  Entry
	reader io.ReadCloser
	pos    int64
	buf    []byte
}

func (c *chunkSource) read(offset, size int64) ([]byte, error) {
	if c.reader == nil || offset != c.pos {
		c.close()
		reader, err := c.store.Open(c.entry)
		if err != nil {
			return nil, err
		}
		if _, err := io.CopyN(io.Discard, reader, offset); err != nil {
			reader.Close()
			return nil, err
		}
		c.reader, c.pos = reader, offset
	}
	if int64(cap(c.buf)) < size {
		c.buf = make([]byte, size)
	}
	buf := c.buf[:size]
	n, err := io.ReadFull(c.reader, buf)
	if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) {
		return nil, err
	}
	c.pos += int64(n)
	return buf[:n], nil
}

func (c *chunkSource) close() {
	if c.reader != nil {
		c.reader.Close()
		c.reader = nil
	}
}
```

```go
// agent/internal/artifacts/priority_windows.go
//go:build windows

package artifacts

import (
	"runtime"
	"syscall"
)

var (
	kernel32              = syscall.NewLazyDLL("kernel32.dll")
	procGetCurrentThread  = kernel32.NewProc("GetCurrentThread")
	procSetThreadPriority = kernel32.NewProc("SetThreadPriority")
)

const (
	threadModeBackgroundBegin = 0x00010000
	threadModeBackgroundEnd   = 0x00020000
)

// enterBackground переводит поток воркера в фоновый режим Windows: ниже
// приоритет процессора, ввода-вывода и памяти, чем у программ пользователя.
// Горутина закрепляется за потоком; возвращённая функция возвращает всё назад.
func enterBackground() func() {
	runtime.LockOSThread()
	handle, _, _ := procGetCurrentThread.Call()
	ok, _, _ := procSetThreadPriority.Call(handle, threadModeBackgroundBegin)
	if ok == 0 {
		runtime.UnlockOSThread()
		return func() {}
	}
	return func() {
		procSetThreadPriority.Call(handle, threadModeBackgroundEnd)
		runtime.UnlockOSThread()
	}
}
```

```go
// agent/internal/artifacts/priority_other.go
//go:build !windows

package artifacts

// enterBackground вне Windows ничего не меняет: агент на Linux файлы
// съёмных носителей не собирает.
func enterBackground() func() { return func() {} }
```

- [ ] **Step 4: Run to verify it passes**

Run: `go test ./internal/artifacts && go vet ./internal/artifacts && GOOS=windows go vet ./internal/artifacts`
Expected: PASS; vet под Windows тоже чист (проверяет `priority_windows.go`).

- [ ] **Step 5: Commit**

```bash
git add agent/internal/artifacts
git commit -F - <<'EOF'
feat(agent): background upload worker

One file at a time, resumable, paced, with backoff; vanished or corrupt
copies are dropped and reported once as agent/artifact_dropped.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 11: Агент — копия в том же проходе, что и хеширование (filewatch)

**Files:**
- Modify: `agent/internal/collectors/filewatch/hasher.go`
- Modify: `agent/internal/collectors/filewatch/pipeline.go` (`PipelineDeps.Stager`, `emit`)
- Modify: `agent/internal/collectors/filewatch/event.go` (метка `artifact_skipped`)
- Modify: `agent/internal/collectors/filewatch/collector.go` (`Deps.Stager`, передача в конвейер)
- Test: `agent/internal/collectors/filewatch/hasher_stage_test.go`, `agent/internal/collectors/filewatch/pipeline_stage_test.go`

**Interfaces:**
- Consumes: `artifacts.Stager`, `artifacts.Sink`, `artifacts.SkipRate`, `artifacts.NewStore`, `artifacts.Config` (Tasks 6–8).
- Produces: `HashResult.Staged bool`, `HashResult.StageSkip string`; `(Hasher).HashStaged(path string, maxBytes int64, deadline time.Time, stager artifacts.Stager) HashResult`; `PipelineDeps.Stager artifacts.Stager`; `filewatch.Deps.Stager artifacts.Stager`; метка события `labels["artifact_skipped"] = "rate"`.
- Копия делается, только если том `removable` и действие `create`/`modify` (`copy` возникает из них после хеширования).

- [ ] **Step 1: Write the failing tests**

```go
// agent/internal/collectors/filewatch/hasher_stage_test.go
package filewatch

import (
	"bytes"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
)

type fakeSink struct {
	buf       bytes.Buffer
	committed string
	aborted   bool
	failWrite bool
}

func (s *fakeSink) Write(p []byte) (int, error) {
	if s.failWrite {
		return 0, errors.New("диск полон")
	}
	return s.buf.Write(p)
}
func (s *fakeSink) Commit(sha string) error { s.committed = sha; return nil }
func (s *fakeSink) Abort()                  { s.aborted = true }

type fakeStager struct {
	sink  *fakeSink
	skip  string
	calls []int64
}

func (f *fakeStager) Begin(size int64) (artifacts.Sink, string) {
	f.calls = append(f.calls, size)
	if f.skip != "" {
		return nil, f.skip
	}
	return f.sink, ""
}

func hasherOver(open func() (io.ReadCloser, int64, error)) Hasher {
	now := time.Date(2026, 10, 5, 12, 0, 0, 0, time.UTC)
	return Hasher{
		Open:  func(string) (io.ReadCloser, int64, error) { return open() },
		Sleep: func(time.Duration) {},
		Now:   func() time.Time { return now },
	}
}

func contentOf(s string) func() (io.ReadCloser, int64, error) {
	return func() (io.ReadCloser, int64, error) {
		return io.NopCloser(strings.NewReader(s)), int64(len(s)), nil
	}
}

func TestHashStagedFeedsTheSinkAndCommitsTheHash(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := hasherOver(contentOf("содержимое документа"))

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashOK || !got.Staged || got.StageSkip != "" {
		t.Fatalf("результат = %+v", got)
	}
	if stager.sink.committed != got.SHA256 {
		t.Fatalf("Commit(%q), хеш %q", stager.sink.committed, got.SHA256)
	}
	if stager.sink.buf.String() != "содержимое документа" {
		t.Fatal("в копию попали не те байты")
	}
}

func TestHashWithoutAStagerBehavesAsBefore(t *testing.T) {
	h := hasherOver(contentOf("abc"))

	got := h.Hash("E:\\a.txt", 1<<20, time.Time{})

	if got.Status != HashOK || got.Staged || got.StageSkip != "" {
		t.Fatalf("результат = %+v", got)
	}
}

func TestSkippedStagingIsReportedWithItsReason(t *testing.T) {
	stager := &fakeStager{skip: artifacts.SkipRate}
	h := hasherOver(contentOf("abc"))

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashOK || got.Staged || got.StageSkip != artifacts.SkipRate {
		t.Fatalf("результат = %+v", got)
	}
}

func TestSinkFailureAbortsStagingButNotTheHash(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{failWrite: true}}
	h := hasherOver(contentOf("abc"))

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashOK || got.SHA256 == "" {
		t.Fatalf("хеш не посчитан из-за сбоя копии: %+v", got)
	}
	if got.Staged || !stager.sink.aborted || stager.sink.committed != "" {
		t.Fatalf("копия не отменена: %+v, aborted=%v", got, stager.sink.aborted)
	}
}

type failingReader struct {
	data []byte
	err  error
}

func (r *failingReader) Read(p []byte) (int, error) {
	if len(r.data) == 0 {
		return 0, r.err
	}
	n := copy(p, r.data)
	r.data = r.data[n:]
	return n, nil
}
func (r *failingReader) Close() error { return nil }

func TestReadErrorAbortsTheSink(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := hasherOver(func() (io.ReadCloser, int64, error) {
		return &failingReader{data: []byte("начало"), err: errors.New("устройство извлечено")}, 20, nil
	})

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashUnavailable {
		t.Fatalf("статус = %q, ожидался %q", got.Status, HashUnavailable)
	}
	if !stager.sink.aborted || stager.sink.committed != "" {
		t.Fatal("копия оборванного чтения не отменена")
	}
}

// Файл вырос между stat и чтением (размер 100, байт 5000): реальный Store не
// даёт копии превысить MaxBytes, а хеш остаётся верным для прочитанного.
func TestGrowingFileAbortsStagingButNotTheHash(t *testing.T) {
	store, err := artifacts.NewStore(t.TempDir(), bytes.Repeat([]byte{3}, 32), time.Now)
	if err != nil {
		t.Fatal(err)
	}
	cfg := artifacts.DefaultConfig()
	cfg.MaxBytes = 1000
	store.SetConfig(cfg)

	grown := strings.Repeat("g", 5000)
	h := hasherOver(func() (io.ReadCloser, int64, error) {
		return io.NopCloser(strings.NewReader(grown)), 100, nil
	})

	got := h.HashStaged("E:\\a.txt", 1<<30, time.Time{}, store)

	if got.Status != HashOK || got.Size != 5000 {
		t.Fatalf("результат = %+v", got)
	}
	if got.Staged {
		t.Fatal("копия вышла за предел MaxBytes")
	}
	if entries, _ := store.List(); len(entries) != 0 {
		t.Fatalf("в каталоге копий %d записей после отмены", len(entries))
	}
}
```

```go
// agent/internal/collectors/filewatch/pipeline_stage_test.go
package filewatch

import (
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
)

func TestCreateOnAFlashDriveIsStaged(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[dst] = strings.Repeat("секретные данные ", 200)

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(stager.calls) != 1 {
		t.Fatalf("Begin вызван %d раз, ожидалось 1", len(stager.calls))
	}
	if stager.sink.committed == "" {
		t.Fatal("копия не завершена")
	}
}

func TestFileOutsideRemovableVolumesIsNeverStaged(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[src] = strings.Repeat("данные ", 300)

	h.pipeline.Handle(Raw{Kind: Modified, Path: src})
	h.advance(2 * time.Second)

	if len(stager.calls) != 0 {
		t.Fatalf("файл наблюдаемой папки скопирован для загрузки (%d вызовов)", len(stager.calls))
	}
}

func TestDeleteIsNeverStaged(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })

	h.pipeline.Handle(Raw{Kind: Deleted, Path: dst})
	h.advance(2 * time.Second)

	if len(stager.calls) != 0 {
		t.Fatal("удаление породило копию")
	}
}

func TestRateSkipIsMarkedOnTheEvent(t *testing.T) {
	stager := &fakeStager{skip: artifacts.SkipRate}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[dst] = strings.Repeat("данные ", 300)

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 {
		t.Fatalf("событий %d", len(h.emitted))
	}
	if h.emitted[0].Labels["artifact_skipped"] != "rate" {
		t.Fatalf("метки = %+v: нет artifact_skipped=rate", h.emitted[0].Labels)
	}
	if h.emitted[0].Artifact == nil {
		t.Fatal("событие потеряло artifact: пропуск копии не должен лишать его хеша")
	}
}

func TestOtherSkipReasonsLeaveNoLabel(t *testing.T) {
	stager := &fakeStager{skip: artifacts.SkipSize}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[dst] = strings.Repeat("данные ", 300)

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if _, has := h.emitted[0].Labels["artifact_skipped"]; has {
		t.Fatal("метка artifact_skipped нужна только для rate")
	}
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `go test ./internal/collectors/filewatch -run 'Stag|Skip|HashWithout|Growing|ReadError|Sink'`
Expected: FAIL — `h.HashStaged undefined`, `d.Stager undefined`.

- [ ] **Step 3: Write minimal implementation**

`hasher.go` — импорты добавить `"log/slog"` и `"github.com/barysguard/agent/internal/artifacts"`; `HashResult` дополнить:

```go
type HashResult struct {
	SHA256 string
	Size   int64
	Status string
	// Staged — копия содержимого снята для загрузки на сервер.
	Staged bool
	// StageSkip — почему копию не сняли (artifacts.Skip*); пусто, если сняли
	// или не пытались.
	StageSkip string
}
```

Метод `Hash` становится обёрткой, прежнее тело переезжает в `HashStaged`:

```go
// Hash открывает файл и считает хеш без копии содержимого.
func (h Hasher) Hash(path string, maxBytes int64, deadline time.Time) HashResult {
	return h.HashStaged(path, maxBytes, deadline, nil)
}

// HashStaged считает хеш и в том же проходе чтения отдаёт байты stager'у.
// Файл, который копируют прямо сейчас, часто занят: попытки повторяются с
// нарастающей паузой до deadline, затем событие уходит без хеша, а не
// откладывается навсегда. Файл больше maxBytes не читается. Копия не должна
// ломать хеширование: сбой записи отменяет копию, хеш остаётся.
func (h Hasher) HashStaged(path string, maxBytes int64, deadline time.Time, stager artifacts.Stager) HashResult {
	delay := 50 * time.Millisecond
	for {
		reader, size, err := h.Open(path)
		if err == nil {
			result, readErr := hashReader(reader, size, maxBytes, stager)
			reader.Close()
			if readErr == nil {
				return result
			}
			err = readErr
		}
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, errIsDirectory) {
			return HashResult{Status: HashGone}
		}
		// Отказ в доступе не пройдёт от ожидания: повторы до конца окна только
		// держали бы цикл сборщика.
		if errors.Is(err, fs.ErrPermission) {
			return HashResult{Status: HashUnavailable}
		}
		if !h.Now().Add(delay).Before(deadline) {
			return HashResult{Status: HashUnavailable}
		}
		h.Sleep(delay)
		if delay < time.Second {
			delay *= 2
		}
	}
}
```

`hashReader` и вспомогательный тип:

```go
func hashReader(reader io.Reader, size, maxBytes int64, stager artifacts.Stager) (HashResult, error) {
	if size > maxBytes {
		return HashResult{Size: size, Status: HashSkippedSize}, nil
	}
	sum := sha256.New()
	var dst io.Writer = sum
	var tee *stageTee
	skip := ""
	if stager != nil {
		if sink, why := stager.Begin(size); sink != nil {
			tee = &stageTee{sink: sink}
			dst = io.MultiWriter(sum, tee)
		} else {
			skip = why
		}
	}

	read, err := io.Copy(dst, reader)
	if err != nil {
		if tee != nil {
			tee.sink.Abort()
		}
		return HashResult{}, err
	}
	result := HashResult{SHA256: hex.EncodeToString(sum.Sum(nil)), Size: read, Status: HashOK, StageSkip: skip}
	if tee != nil {
		result.Staged = tee.commit(result.SHA256)
	}
	return result, nil
}

// stageTee передаёт байты копии и никогда не возвращает ошибку: сбой копии
// не должен срывать подсчёт хеша.
type stageTee struct {
	sink   artifacts.Sink
	failed bool
}

func (t *stageTee) Write(p []byte) (int, error) {
	if !t.failed {
		if _, err := t.sink.Write(p); err != nil {
			t.failed = true
			t.sink.Abort()
		}
	}
	return len(p), nil
}

func (t *stageTee) commit(sha string) bool {
	if t.failed {
		return false
	}
	if err := t.sink.Commit(sha); err != nil {
		slog.Warn("копия файла не сохранена", "error", err)
		return false
	}
	return true
}
```

`pipeline.go` — импорт `"github.com/barysguard/agent/internal/artifacts"`; в `PipelineDeps` добавить поле

```go
	// Stager снимает копию файлов внешних томов для загрузки. nil — не снимает.
	Stager artifacts.Stager
```

в `emit` заменить вызов хеширования:

```go
	if settled.Action != ActionDelete {
		// Копия нужна только для содержимого, которое уходит на внешний том:
		// copy возникает из create/modify уже после хеширования.
		var stager artifacts.Stager
		if p.deps.Stager != nil && removable &&
			(settled.Action == ActionCreate || settled.Action == ActionModify) {
			stager = p.deps.Stager
		}
		in.Hash = p.deps.Hasher.HashStaged(settled.Path, cfg.MaxHashBytes,
			now.Add(min(cfg.MaxWait, hashRetryBudget)), stager)
		if in.Hash.Status == HashGone {
			return // временный файл или каталог
		}
	}
```

`event.go` — импорт `artifacts`; в `BuildEvent` после блока `switch in.Hash.Status` добавить:

```go
	if in.Hash.StageSkip == artifacts.SkipRate {
		// Бюджет копирования исчерпан: оператор видит, что содержимое не взято.
		labels["artifact_skipped"] = "rate"
	}
```

`collector.go` — в `Deps` добавить `Stager artifacts.Stager` (импорт `artifacts`), в `NewPipeline(PipelineDeps{...})` внутри `Run` — `Stager: c.deps.Stager,`.

- [ ] **Step 4: Run to verify they pass**

Run: `go test ./internal/collectors/... && go vet ./internal/collectors/... && GOOS=windows go vet ./internal/collectors/...`
Expected: PASS, включая все прежние тесты `filewatch` (сигнатура `Hash` не менялась).

- [ ] **Step 5: Commit**

```bash
git add agent/internal/collectors/filewatch
git commit -F - <<'EOF'
feat(agent): stage removable-volume files while hashing

The copy is fed from the same read pass as the hash; a failing or
overflowing copy is aborted without affecting the hash. Budget skips are
marked on the event as labels.artifact_skipped=rate.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 12: Агент — подключение (`collectors`, `runner`, `main`)

**Files:**
- Modify: `agent/internal/collectors/factory.go` (`Platform.Stager`, передача в `filewatch.Deps`)
- Modify: `agent/internal/runner/agent.go` (`Options.Artifacts`, `ArtifactWorker`, `applyArtifactConfig` на обоих местах применения конфигурации)
- Modify: `agent/internal/runner/events.go` (`StartEvents`)
- Modify: `agent/cmd/barysguard-agent/main.go` (`loadAgent`)
- Test: `agent/internal/runner/artifacts_test.go`

**Interfaces:**
- Consumes: `artifacts.Worker`, `artifacts.Store`, `artifacts.Config`, `artifacts.ConfigFromDocument` (Tasks 6–10), `filewatch.Deps.Stager` (Task 11), `config.Layout.StagingDir` (Task 6), `buffer.LoadOrCreateKey`.
- Produces: `runner.ArtifactWorker interface { Run(ctx context.Context, emit func(events.Envelope)); SetConfig(artifacts.Config) }`, `runner.Options.Artifacts ArtifactWorker`, `collectors.Platform.Stager artifacts.Stager`.

- [ ] **Step 1: Write the failing test**

```go
// agent/internal/runner/artifacts_test.go
package runner_test

import (
	"bytes"
	"context"
	"math/rand"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

type fakeWorker struct {
	mu          sync.Mutex
	configs     []artifacts.Config
	runs, stops atomic.Int32
}

func (f *fakeWorker) SetConfig(cfg artifacts.Config) {
	f.mu.Lock()
	f.configs = append(f.configs, cfg)
	f.mu.Unlock()
}

func (f *fakeWorker) last() (artifacts.Config, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.configs) == 0 {
		return artifacts.Config{}, false
	}
	return f.configs[len(f.configs)-1], true
}

func (f *fakeWorker) Run(ctx context.Context, _ func(events.Envelope)) {
	f.runs.Add(1)
	<-ctx.Done()
	f.stops.Add(1)
}

func newWorkerAgent(t *testing.T, gateway *fakeGateway, state config.State, worker runner.ArtifactWorker) *runner.Agent {
	t.Helper()
	gateway.ids = map[string]int{}
	ca := newRunnerCA(t)
	server := newRunnerTLSServer(t, ca, gateway)
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if err := config.SaveState(layout, state, guard); err != nil {
		t.Fatal(err)
	}
	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatal(err)
	}
	buf, err := buffer.Open(buffer.Options{
		Path: filepath.Join(t.TempDir(), "events.db"), Key: bytes.Repeat([]byte{7}, 32),
		MaxBytes: 1 << 30, MaxAge: 24 * time.Hour,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { buf.Close() })

	agent, err := runner.New(runner.Options{
		ServerURL: server.URL, AgentVersion: "0.1.0", Layout: layout, Guard: guard, Client: client,
		Random: rand.NewSource(1), Buffer: buf, Artifacts: worker,
	})
	if err != nil {
		t.Fatal(err)
	}
	return agent
}

func TestArtifactWorkerStartsWithTheSavedConfigAndStopsWithTheAgent(t *testing.T) {
	worker := &fakeWorker{}
	state := config.State{ConfigVersion: 5, Document: map[string]any{
		"collectors": map[string]any{"artifact": map[string]any{"max_bytes": float64(4321)}},
	}}
	agent := newWorkerAgent(t, &fakeGateway{configVersion: 5}, state, worker)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "воркер запущен", func() bool { return worker.runs.Load() == 1 })
	cfg, ok := worker.last()
	if !ok || cfg.MaxBytes != 4321 {
		t.Fatalf("воркер получил конфигурацию %+v", cfg)
	}
	stop()

	if worker.stops.Load() != 1 {
		t.Fatal("воркер не остановлен вместе с агентом")
	}
}

func TestNewConfigIsAppliedToTheArtifactWorker(t *testing.T) {
	worker := &fakeWorker{}
	gateway := &fakeGateway{configVersion: 99, configDocument: map[string]any{
		"collectors": map[string]any{"artifact": map[string]any{"max_bytes": float64(1234)}},
	}}
	agent := newWorkerAgent(t, gateway, config.State{ConfigVersion: 1}, worker)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "воркер запущен", func() bool { return worker.runs.Load() == 1 })
	agent.RunOnce(context.Background())
	waitFor(t, "новая конфигурация применена", func() bool {
		cfg, ok := worker.last()
		return ok && cfg.MaxBytes == 1234
	})
	stop()
}

func TestAgentWithoutAnArtifactWorkerStillStarts(t *testing.T) {
	agent := newWorkerAgent(t, &fakeGateway{configVersion: 5}, config.State{ConfigVersion: 5}, nil)

	stop := agent.StartEvents(context.Background())
	stop()
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `go test ./internal/runner -run 'ArtifactWorker|NewConfigIsApplied|WithoutAnArtifact'`
Expected: FAIL — `runner.ArtifactWorker undefined`, `unknown field Artifacts`.

- [ ] **Step 3: Write minimal implementation**

`agent/internal/runner/agent.go` — импорт `"github.com/barysguard/agent/internal/artifacts"`; перед `type Options struct` добавить:

```go
// ArtifactWorker — загрузка на сервер копий файлов с внешних томов.
type ArtifactWorker interface {
	Run(ctx context.Context, emit func(events.Envelope))
	SetConfig(artifacts.Config)
}
```

в `Options` после `CollectorFactory`:

```go
	// Artifacts отправляет копии файлов на сервер. nil — загрузки нет.
	// Запускается вместе со сборщиками и применяет раздел collectors.artifact.
	Artifacts ArtifactWorker
```

в обоих местах, где вызывается `a.notifyCollectorsReload()` (два вызова: в функции применения конфигурации и в `syncConfig`), добавить строкой выше `a.applyArtifactConfig()`.

`agent/internal/runner/events.go` — импорт `artifacts`; в `StartEvents` сразу после блока `if factory := a.options.CollectorFactory; factory != nil { ... }` добавить:

```go
	if worker := a.options.Artifacts; worker != nil {
		worker.SetConfig(artifacts.ConfigFromDocument(a.state.Document))
		collectors.Add(1)
		go func() {
			defer collectors.Done()
			worker.Run(collectorCtx, a.queue.Emit)
		}()
	}
```

и рядом с `notifyCollectorsReload`:

```go
// applyArtifactConfig отдаёт воркеру загрузки свежий раздел collectors.artifact.
func (a *Agent) applyArtifactConfig() {
	if worker := a.options.Artifacts; worker != nil {
		worker.SetConfig(artifacts.ConfigFromDocument(a.state.Document))
	}
}
```

`agent/internal/collectors/factory.go` — импорт `artifacts`; в `Platform` добавить поле `Stager artifacts.Stager`; в `Build` в `filewatch.Deps{...}` добавить `Stager: plat.Stager,`.

`agent/cmd/barysguard-agent/main.go` — импорт `"github.com/barysguard/agent/internal/artifacts"`; в `loadAgent` после открытия буфера (`buf, reset, err := buffer.OpenAt(...)`) и до `runner.New`:

```go
	// Копии файлов шифруются тем же ключом, что и буфер событий.
	key, _, err := buffer.LoadOrCreateKey(layout, guard)
	if err != nil {
		buf.Close()
		return nil, layout, fmt.Errorf("ключ копий файлов: %w", err)
	}
	if err := guard.SecureDir(layout.StagingDir()); err != nil {
		buf.Close()
		return nil, layout, fmt.Errorf("каталог копий файлов: %w", err)
	}
	store, err := artifacts.NewStore(layout.StagingDir(), key, time.Now)
	if err != nil {
		buf.Close()
		return nil, layout, fmt.Errorf("каталог копий файлов: %w", err)
	}
	store.SetConfig(artifacts.ConfigFromDocument(state.Document))
	plat := collectors.DefaultPlatform()
	plat.Stager = store
```

и в `runner.Options{...}` заменить `CollectorFactory: collectors.NewFactory(layout.Dir),` на

```go
		CollectorFactory: collectors.NewFactoryFor(plat, layout.Dir),
		Artifacts:        artifacts.NewWorker(store, client),
```

(`time` в `main.go` уже импортирован; если нет — добавить.)

- [ ] **Step 4: Run to verify it passes**

Run (из `agent/`):
```bash
go test ./... && go vet ./...
GOOS=windows go build ./... && GOOS=windows go vet ./...
GOOS=linux go build ./...
```
Expected: PASS на всех трёх; прежние тесты `runner`, `collectors` зелёные.

- [ ] **Step 5: Commit**

```bash
git add agent
git commit -F - <<'EOF'
feat(agent): wire artifact staging and upload into the runner

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 13: Документация и приведение спека в соответствие

**Files:**
- Modify: `docs/COLLECTORS_MANUAL.md`
- Modify: `docs/QUICKSTART.md`
- Modify: `docs/superpowers/specs/2026-10-05-artifact-upload-design.md`

**Interfaces:** нет (документы).

- [ ] **Step 1: `docs/COLLECTORS_MANUAL.md`**

В список «Что агент не умеет в этом цикле» заменить строку

```
- Сетевые папки и диски, буфер обмена, печать, содержимое файлов: следующие циклы.
```

на

```
- Сетевые папки и диски, буфер обмена, печать: следующие циклы.
- Проверять содержимое загруженных файлов: инспекция появится в следующем подпроекте; пока сервер только хранит их.
```

и перед разделом «## Что агент не умеет в этом цикле» вставить:

````markdown
## Загрузка файлов с флешки на сервер

Для событий `create`, `modify` и `copy` на **внешнем томе** агент снимает зашифрованную копию файла и загружает её на сервер. Копия снимается в том же проходе чтения, что и хеширование (флешка читается один раз), лежит в `<data-dir>\staging\` до загрузки и удаляется после неё. Если связи нет или флешку уже вытащили, содержимое не теряется: копия ждёт на агенте. Файлы наблюдаемых папок (Документы, Рабочий стол) не загружаются.

В событии `copy` рядом с `artifact.sha256` консоль показывает «Содержимое на сервере: Загружено / Не загружено».

Настройка в разделе `collectors.artifact` конфигурации агента:

| Ключ | Умолчание | Что делает |
|---|---|---|
| `enabled` | `true` | выключатель загрузки |
| `max_bytes` | 52 428 800 (50 МиБ) | файл больше не копируется и не загружается |
| `staging_max_bytes` | 524 288 000 | предел каталога копий на агенте; при нехватке вытесняются самые старые |
| `upload_bytes_per_second` | 2 097 152 | скорость отправки |
| `stage_bytes_per_minute` | 209 715 200 | бюджет копирования в минуту; сверх него файл пропускается |

Нагрузка на хост. Копия и загрузка не должны быть заметны пользователю: файл не читается в память целиком (буфер 1 МиБ), загрузкой занят один воркер в фоновом режиме потока Windows (низкий приоритет процессора и диска), скорость ограничена. Если пользователь копирует на флешку сотни гигабайт, агент не мешает и пропускает лишнее: событие уходит с `labels.artifact_skipped = "rate"`, а потери фиксирует событие `agent/artifact_dropped`.

### Ручная проверка

Нужны стенд (`.\stand.cmd up`) и Windows-агент, зарегистрированный на нём.

1. Скопируйте файл (например, `test.txt` размером около 1 МБ) из «Документов» на флешку. В консоли, раздел «События»: `copy`, критичность «Высокая», через несколько секунд в окне «Подробнее» **Содержимое на сервере: Загружено**.
2. Скопируйте тот же файл на флешку ещё раз (или с другого компьютера): в сети передачи нет (ответ `exists`), на сервере по-прежнему один артефакт.
3. Обрыв связи: остановите сервер (`.\stand.cmd down`), скопируйте файл на флешку, **выньте флешку**, запустите стенд (`.\stand.cmd up`). Событие дойдёт из буфера, содержимое загрузится из `<data-dir>\staging\`: «Загружено».
4. Файл больше 50 МиБ: событие есть, содержимое не загружается («Не загружено»), копия не снимается.
5. На сервере файл лежит зашифрованным: в томе `artifacts` (`/var/lib/barysguard/artifacts/ab/cd/<sha256>.enc`) открытого текста нет.
````

- [ ] **Step 2: `docs/QUICKSTART.md`**

В таблицу «Переменные окружения» добавить строки:

```
| `BG_ARTIFACT_MASTER_KEY` | для загрузки файлов | Мастер-ключ хранилища артефактов: 32 байта в base64. Без него загрузка отвечает `503`, остальной шлюз работает |
| `BG_ARTIFACT_MASTER_KEY_FILE` | нет | То же, но из файла секретов |
| `BG_ARTIFACT_PATH` | нет | Каталог хранилища артефактов, по умолчанию `/var/lib/barysguard/artifacts` |
| `BG_ARTIFACT_MAX_BYTES` | нет | Предел размера артефакта, по умолчанию 52 428 800 |
| `BG_UPLOAD_SESSION_TTL_HOURS` | нет | Срок жизни открытой загрузки, по умолчанию 24 |
```

и после абзаца о `BG_CA_PASSPHRASE` добавить:

````markdown
`BG_ARTIFACT_MASTER_KEY` защищает содержимое всех загруженных файлов: каждый артефакт шифруется своим ключом, а ключи завёрнуты этим мастер-ключом. Хранить вне сервера и вне базы. Сгенерировать:

```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Потеря ключа делает уже загруженные файлы нечитаемыми. На Docker-стенде используется известный ключ (`BG_STAND_ARTIFACT_KEY` в `deploy/stand/.env` его меняет).
````

В раздел «Сборщики файлов и USB» добавить абзац:

```
Для файлов на внешних томах агент также загружает содержимое: `POST /gateway/v1/artifacts` открывает или продолжает сессию, `PUT /gateway/v1/artifacts/{upload_id}` принимает чанки по 1 МиБ с заголовком `X-Offset`, в конце сервер сверяет SHA-256. Известный серверу файл повторно не передаётся (`200 exists`). Подробнее — `docs/COLLECTORS_MANUAL.md`, раздел «Загрузка файлов с флешки на сервер».
```

- [ ] **Step 3: Спек — привести к реализованному**

В `docs/superpowers/specs/2026-10-05-artifact-upload-design.md`:

1. Строку `- **Статус:** проект, ждёт утверждения` → `- **Статус:** реализовано (подпроект 3a / B1)`.
2. В разделе 4.1 строку `  key_wrapped bytea · key_nonce bytea · wrap_version smallint` → `  key_wrapped bytea · wrap_version smallint`, а после блока кода схемы добавить фразу: «Обёртка ключа — AES Key Wrap (RFC 3394): она детерминирована и nonce не требует, поэтому колонки `key_nonce` нет.»
3. В разделе 4.4 перед блоком `class ArtifactStore(Protocol)` добавить фразу «Методы синхронные: шифрование и файловый ввод-вывод блокирующие, вызывающий код оборачивает их в `asyncio.to_thread`.», а в блоке заменить `async def` на `def`, а `async def open(...) -> AsyncIterator[bytes]` на `def open(self, sha256: str, key_wrapped: bytes) -> Iterator[bytes]`; сигнатура `put` — `def put(self, sha256: str, source: Path) -> StoredArtifact`.
4. В разделе 4.5 пункт про `GET /api/v1/events`: «в `artifact` каждого события добавляется `uploaded`» → «в каждое событие добавляется булево поле `artifact_uploaded`».
5. В разделе 6 пункт «**Сквозная:**» заменить на: «**Сквозная:** контур агент → nginx → сервер проверяется вручную (раздел «Ручная проверка» в `docs/COLLECTORS_MANUAL.md`): агенты стенда работают на Linux и копий не снимают, а загрузка идёт по mTLS, которого нет у локального uvicorn. Автоматически его покрывают серверные тесты (`test_artifact_upload.py`) и тесты транспорта агента на `httptest` с настоящим TLS.»

- [ ] **Step 4: Проверить ссылки и отсутствие остатков**

Run: `grep -n "key_nonce\|ждёт утверждения\|async def put" docs/superpowers/specs/2026-10-05-artifact-upload-design.md`
Expected: пустой вывод.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -F - <<'EOF'
docs: artifact upload manual, quickstart variables, spec marked implemented

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 14: Итоговая проверка всей ветки

**Files:** нет (проверка; правки — только если что-то найдено).

- [ ] **Step 1: Серверный набор**

Run (из `server/`): `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest -q && .venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check . && .venv/Scripts/python -m mypy barysguard`
Expected: все тесты зелёные, lint и типы чистые.

- [ ] **Step 2: Агентский набор на трёх платформах**

Run (из `agent/`):
```bash
go test ./... && go vet ./...
GOOS=windows go build ./... && GOOS=windows go vet ./...
GOOS=linux go build ./...
```
Expected: всё проходит; в выводе нет предупреждений `vet`.

- [ ] **Step 3: Консоль**

Run (из `web/`): `npx vitest run && npm run typecheck && npm run build`
Expected: все тесты, типы и сборка проходят.

- [ ] **Step 4: Контракт совпадает с приложением**

Run (из `server/`): `BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest tests/test_openapi_contract.py -q`
Expected: PASS (`api/gateway-v1.yaml` не разошёлся).

- [ ] **Step 5: Нагрузка и память (проверка требования «минимальная нагрузка»)**

Run (из `agent/`): `go test ./internal/artifacts -run 'Writer|Uploaded|Paced' -v -count=1`
Expected: PASS; тест `TestWriterNeverHoldsMoreThanOneBlock` подтверждает, что буфер шифрования не превышает 1 МиБ, `TestFileIsUploadedInOrderedChunksAndTheCopyIsRemoved` — что чанк не больше 1 МиБ, `TestUploadSpeedIsPaced` — что отправка укладывается в лимит скорости.

- [ ] **Step 6: Стенд (если Docker запущен)**

Run (cmd/PowerShell из корня): `.\stand.cmd up`, затем `.\smoke.cmd`
Expected: smoke проходит; сервер стартует с хранилищем артефактов (`docker compose logs server` без ошибки о мастер-ключе); в томе `artifacts` создаётся каталог `tmp/` при первой загрузке.

- [ ] **Step 7: Ручная проверка на Windows**

Выполнить сценарии 1–5 из `docs/COLLECTORS_MANUAL.md`, раздел «Ручная проверка». Результат записать в сообщении пользователю: что проверено, что нет (Windows-агент и флешка нужны для пунктов 1–4).

- [ ] **Step 8: Итог**

Если на шагах 1–6 что-то найдено — исправить отдельным коммитом `fix: ...` с тестом, воспроизводящим проблему. Затем `git status` (дерево чистое, кроме `prompt.txt`) и `git log --oneline` (по коммиту на Task 1–13).
