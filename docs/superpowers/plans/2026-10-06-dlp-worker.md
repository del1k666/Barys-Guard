# DLP Inspection Worker (B2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сервер сам инспектирует загруженное содержимое файлов с внешних томов: очередь в PostgreSQL, воркер, извлечение текста, детекторы (ИИН/БИН, карты, грифы), вердикты и инциденты, видимые через API.

**Architecture:** Событие с артефактом ставится в `event_queue` при завершении загрузки артефакта или при приёме события, если артефакт уже есть; страховочная проверка по свежим артефактам закрывает гонку. Отдельный процесс `barysguard-admin worker` забирает задачи через `SKIP LOCKED`, расшифровывает артефакт в памяти, извлекает текст, прогоняет детекторы потоково, пишет скан (один на хеш и версию набора правил), вердикт (на событие) и дополняет инцидент (ключ: агент + пользователь + хеш).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, PostgreSQL 16, pytest (`asyncio_mode = "auto"`), `pypdf` (единственная новая зависимость).

**Spec:** `docs/superpowers/specs/2026-10-06-dlp-worker-design.md`

## Global Constraints

- Весь код сервера — в `server/`, команды запускать из `server/`. Тесты: `BG_TEST_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard" .venv/Scripts/python -m pytest <путь> -q` (локальный PostgreSQL на 5432, роль с CREATEDB). Перед коммитом задачи: `.venv/Scripts/python -m ruff check .`, `.venv/Scripts/python -m ruff format --check .`, `.venv/Scripts/python -m mypy barysguard`.
- Строки интерфейса, комментарии и докстринги — на русском, как в остальном коде; идентификаторы — английские. Длина строки 100 (ruff).
- Ruff выбирает правила `E,F,I,N,UP,B,S,ASYNC`; подавление (`# noqa: ...`) только с причиной в комментарии.
- Файлы пишутся с LF-окончаниями строк (Edit/Write сохраняют; не прогонять правки через Python в текстовом режиме на Windows — он запишет CRLF).
- Полные значения совпадений (ИИН, БИН, номера карт) **никогда** не попадают в БД, API, логи, тексты исключений: только счётчики и маски (ИИН/БИН — 2 последние цифры, карта — 4 последние, не более 5 образцов на правило).
- Содержимое артефакта расшифровывается только в памяти, на диск открытым не пишется, не больше `BG_ARTIFACT_MAX_BYTES` (по умолчанию 50 МиБ).
- Пороги вердикта (фиксированы в коде): score 0–19 → `clean`/`info`; 20–49 → `flagged`/`medium`; 50–79 → `flagged`/`high`; 80–100 → `flagged`/`critical`. Порог инцидента — `BG_INCIDENT_MIN_SCORE` (по умолчанию 20).
- Веса стартового набора: ИИН/БИН weight 20 cap 5; карта weight 25 cap 4; грифы weight 15 cap 2. Вклад правила = `weight × min(count, cap)`, итог ограничен 100.
- Коммиты оканчиваются строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Никаких изменений в агенте и консоли (`web/`) кроме регенерации типов в Task 8.

## Review Focus

Входы и условия, которые спека подразумевает, но которые легко сломать; у каждого есть тест в указанной задаче.

1. **Число в несколько фрагментов текста.** ИИН/карта на границе порции текста, а также длинная цифровая последовательность, режущаяся границей, не должны ни теряться, ни давать ложное совпадение, ни считаться дважды (Task 2: тест «любой размер порции даёт тот же результат»).
2. **ИИН в числовой ячейке Excel и в нескольких runs Word.** Значение лежит в `<v>`, а не в общих строках; в docx разорвано на соседние `w:t` (Task 3).
3. **Порядок прихода.** Событие раньше артефакта и артефакт раньше события; два события на один хеш; повторная постановка не дублирует (Task 5, Task 9).
4. **Зависание и «бомбы».** Битый или защищённый файл, zip-бомба, таймаут не должны ни зациклить воркер, ни повторяться три раза (Task 3, Task 7).
5. **Конфиденциальность результата.** Ни `artifact_scans`, ни `verdicts`, ни `incidents`, ни ответы API не содержат полного ИИН/номера карты (Task 7, Task 8).
6. **Чужая область видимости.** Оператор вне области получает `404` на инцидент, а список пуст (Task 8).

---

## File Structure

Создаются:

- `server/alembic/versions/20261006_1500_inspection.py` — миграция.
- `server/barysguard/db/models/inspection.py` — модели `EventQueue`, `Rule`, `RuleVersion`, `Dictionary`, `DictionaryTerm`, `ArtifactScan`, `Verdict`, `Incident`, `IncidentEvent`.
- `server/barysguard/services/inspection/__init__.py` — пустой.
- `server/barysguard/services/inspection/detectors.py` — детекторы и потоковый `ContentScanner`.
- `server/barysguard/services/inspection/scoring.py` — оценка и пороги.
- `server/barysguard/services/inspection/extract.py` — извлечение текста и защитные пределы.
- `server/barysguard/services/inspection/engine.py` — `scan_bytes`: извлечение + сканирование.
- `server/barysguard/services/inspection/rules.py` — `seed_rules`, `load_ruleset`.
- `server/barysguard/services/inspection/queue.py` — постановка и захват задач.
- `server/barysguard/services/inspection/verdicts.py` — запись вердиктов.
- `server/barysguard/services/inspection/incidents.py` — создание и дополнение инцидентов.
- `server/barysguard/services/inspection/worker.py` — обработка задачи, проход, цикл.
- `server/barysguard/services/demo_artifacts.py` — демо-артефакты для стенда.
- `server/barysguard/api/incidents.py` — маршруты инцидентов.
- Тесты: `test_inspection_models.py`, `test_inspection_detectors.py`, `test_inspection_scoring.py`, `test_inspection_extract.py`, `test_inspection_rules.py`, `test_inspection_queue.py`, `test_inspection_worker.py`, `test_inspection_incidents.py`, `test_incidents_api.py`, `test_inspection_e2e.py`, `test_demo_artifacts.py`.
- `docs/DLP_WORKER.md`.

Меняются: `server/pyproject.toml`, `server/barysguard/core/config.py`, `server/barysguard/db/models/event.py`, `server/barysguard/db/models/__init__.py` (если там перечислены модели), `server/barysguard/services/events.py`, `server/barysguard/services/artifacts.py`, `server/barysguard/cli.py`, `server/barysguard/main.py`, `server/barysguard/api/schemas.py`, `server/barysguard/api/events.py`, `server/tests/conftest.py`, `api/gateway-v1.yaml`, `web/src/api/schema.d.ts`, `deploy/stand/docker-compose.yml`, `scripts/smoke.ps1`, `docs/QUICKSTART.md`.

---

### Task 1: Настройки, модели и миграция

**Files:**
- Modify: `server/pyproject.toml`, `server/barysguard/core/config.py`, `server/barysguard/db/models/event.py`, `server/tests/conftest.py`
- Create: `server/barysguard/db/models/inspection.py`, `server/alembic/versions/20261006_1500_inspection.py`, `server/tests/test_inspection_models.py`

**Interfaces:**
- Produces: модели `EventQueue(id, event_occurred_at, event_id, artifact_sha256, enqueued_at, attempts, locked_until, state)`, `Rule(id, key, kind, title, enabled, created_at)`, `RuleVersion(id, rule_id, version, params, created_at)`, `Dictionary(id, key, title)`, `DictionaryTerm(id, dictionary_id, term)`, `ArtifactScan(id, artifact_sha256, ruleset_hash, status, truncated, findings, scanned_at)`, `Verdict(id, event_occurred_at, event_id, scan_id, status, reason, score, severity, matches, created_at)`, `Incident(id, group_key, agent_id, artifact_sha256, title, severity, score, status, assignee, first_event_at, last_event_at, events_count, created_at, closed_at)`, `IncidentEvent(incident_id, event_occurred_at, event_id)`; колонка `Event.verdict_id`; настройки `Settings.worker_batch`, `worker_poll_seconds`, `worker_lock_seconds`, `worker_max_attempts`, `inspect_max_text_bytes`, `inspect_max_unpacked_bytes`, `inspect_max_entries`, `inspect_max_ratio`, `inspect_timeout_seconds`, `incident_min_score`.

- [ ] **Step 1: Зависимость и настройки**

В `server/pyproject.toml` добавить в `dependencies` строку `"pypdf>=5.0",` после `"python-json-logger>=3.1",`. Установить: `.venv/Scripts/python -m pip install "pypdf>=5.0"`.

В `server/barysguard/core/config.py` в конец класса `Settings` (после `upload_session_ttl_hours`) добавить:

```python

    # Воркер инспекции содержимого (B2).
    worker_batch: int = 10
    worker_poll_seconds: float = 2.0
    worker_lock_seconds: int = 300
    worker_max_attempts: int = 3
    inspect_max_text_bytes: int = 20 * 1024 * 1024
    inspect_max_unpacked_bytes: int = 200 * 1024 * 1024
    inspect_max_entries: int = 10000
    inspect_max_ratio: int = 100
    inspect_timeout_seconds: int = 30
    incident_min_score: int = 20
```

- [ ] **Step 2: Написать падающий тест моделей**

`server/tests/test_inspection_models.py`:

```python
"""Таблицы инспекции: ограничения целостности."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import ArtifactScan, EventQueue, Incident
from tests.helpers import enroll_agent

SHA = "cd" * 32


async def test_queue_defaults_and_uniqueness(app_client, session) -> None:
    at, event_id = datetime.now(UTC), uuid.uuid4()
    session.add(EventQueue(event_occurred_at=at, event_id=event_id, artifact_sha256=SHA))
    await session.commit()

    row = (await session.scalars(select(EventQueue))).one()
    assert (row.attempts, row.state, row.locked_until) == (0, "pending", None)

    session.add(EventQueue(event_occurred_at=at, event_id=event_id, artifact_sha256=SHA))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_scan_is_unique_per_hash_and_ruleset(app_client, session) -> None:
    def make() -> ArtifactScan:
        return ArtifactScan(artifact_sha256=SHA, ruleset_hash="r1", status="ok", findings={})

    session.add(make())
    await session.commit()
    session.add(make())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_one_open_incident_per_group_key(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "inc-models")
    now = datetime.now(UTC)

    def make(status: str) -> Incident:
        return Incident(
            group_key="k1",
            agent_id=agent.agent_id,
            artifact_sha256=SHA,
            title="t",
            severity="high",
            score=60,
            status=status,
            first_event_at=now,
            last_event_at=now,
            events_count=1,
        )

    session.add(make("open"))
    await session.commit()

    session.add(make("acknowledged"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    first = (await session.scalars(select(Incident))).one()
    first.status = "closed"
    await session.commit()

    session.add(make("open"))
    await session.commit()
    assert len((await session.scalars(select(Incident))).all()) == 2


async def test_events_have_a_nullable_verdict_id(app_client, session) -> None:
    assert (await session.execute(select(Event.verdict_id).limit(1))).first() is None
```

- [ ] **Step 3: Запустить тест — должен упасть**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_models.py -q`
Expected: FAIL (`ModuleNotFoundError: barysguard.db.models.inspection`).

- [ ] **Step 4: Колонка в модели Event**

В `server/barysguard/db/models/event.py` после поля `severity` добавить:

```python
    # Заполняет воркер инспекции. Внешнего ключа нет: ссылки с партиционированной
    # таблицы ограничены, целостность обеспечивает код.
    verdict_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
```

- [ ] **Step 5: Модели**

`server/barysguard/db/models/inspection.py`:

```python
"""Таблицы инспекции содержимого: очередь, правила, сканы, вердикты, инциденты."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class EventQueue(Base):
    """Очередь инспекции. Ссылка на событие составная: ключ events партиционирован."""

    __tablename__ = "event_queue"
    __table_args__ = (
        UniqueConstraint("event_occurred_at", "event_id", name="uq_event_queue_event"),
        Index("ix_event_queue_claim", "state", "locked_until", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[uuid.UUID] = mapped_column()
    artifact_sha256: Mapped[str] = mapped_column(Text)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pending | failed
    state: Mapped[str] = mapped_column(Text, server_default="pending")


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(Text, unique=True)
    # detector | dictionary
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuleVersion(Base):
    """Неизменяемая версия правила: вердикт ссылается именно на неё."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_rule_versions_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dictionary(Base):
    __tablename__ = "dictionaries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)


class DictionaryTerm(Base):
    __tablename__ = "dictionary_terms"
    __table_args__ = (UniqueConstraint("dictionary_id", "term", name="uq_dictionary_terms_term"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dictionaries.id", ondelete="CASCADE")
    )
    term: Mapped[str] = mapped_column(Text)


class ArtifactScan(Base):
    """Результат скана содержимого: один на хеш и версию набора правил."""

    __tablename__ = "artifact_scans"
    __table_args__ = (
        UniqueConstraint("artifact_sha256", "ruleset_hash", name="uq_artifact_scans_ruleset"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artifact_sha256: Mapped[str] = mapped_column(Text, index=True)
    ruleset_hash: Mapped[str] = mapped_column(Text)
    # ok | unsupported | no_text | encrypted | too_large | error
    status: Mapped[str] = mapped_column(Text)
    truncated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # {rule_key: {rule_version_id, count, samples}} — только счётчики и маски.
    findings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Verdict(Base):
    __tablename__ = "verdicts"
    __table_args__ = (UniqueConstraint("event_occurred_at", "event_id", name="uq_verdicts_event"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[uuid.UUID] = mapped_column()
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_scans.id", ondelete="SET NULL")
    )
    # clean | flagged | not_inspected
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, server_default="0")
    severity: Mapped[str] = mapped_column(Text, server_default="info")
    # [{rule_key, rule_version_id, count, points, samples}]
    matches: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        # Пока инцидент не закрыт, у ключа группировки он единственный.
        Index(
            "uq_incidents_open_group",
            "group_key",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    group_key: Mapped[str] = mapped_column(Text)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    artifact_sha256: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    # open | acknowledged | closed
    status: Mapped[str] = mapped_column(Text, server_default="open")
    assignee: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    events_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    event_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
```

Если `server/barysguard/db/models/__init__.py` импортирует модели для Alembic/metadata, добавить импорт `barysguard.db.models.inspection` по тому же образцу (проверить файл: `cat barysguard/db/models/__init__.py`; если он пуст — ничего не менять, но убедиться, что `alembic/env.py` импортирует модули моделей; при необходимости добавить импорт там).

- [ ] **Step 6: Миграция**

`server/alembic/versions/20261006_1500_inspection.py`:

```python
"""inspection: queue, rules, scans, verdicts, incidents

Revision ID: c91f5e2a7d34
Revises: a4d2f6c81b53
Create Date: 2026-10-06 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c91f5e2a7d34"
down_revision: str | None = "a4d2f6c81b53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> sa.sql.elements.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.add_column("events", sa.Column("verdict_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_events_artifact_sha256",
        "events",
        ["artifact_sha256"],
        postgresql_where=sa.text("artifact_sha256 IS NOT NULL"),
    )

    op.create_table(
        "event_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.UniqueConstraint("event_occurred_at", "event_id", name="uq_event_queue_event"),
    )
    op.create_index("ix_event_queue_claim", "event_queue", ["state", "locked_until", "id"])

    op.create_table(
        "rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )
    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "rule_id", sa.Uuid(), sa.ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("rule_id", "version", name="uq_rule_versions_version"),
    )
    op.create_table(
        "dictionaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
    )
    op.create_table(
        "dictionary_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "dictionary_id",
            sa.Uuid(),
            sa.ForeignKey("dictionaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("term", sa.Text(), nullable=False),
        sa.UniqueConstraint("dictionary_id", "term", name="uq_dictionary_terms_term"),
    )

    op.create_table(
        "artifact_scans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("ruleset_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("artifact_sha256", "ruleset_hash", name="uq_artifact_scans_ruleset"),
    )
    op.create_index("ix_artifact_scans_artifact_sha256", "artifact_scans", ["artifact_sha256"])

    op.create_table(
        "verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "scan_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_scans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("severity", sa.Text(), nullable=False, server_default="info"),
        sa.Column(
            "matches", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("event_occurred_at", "event_id", name="uq_verdicts_event"),
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column(
            "assignee", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("events_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_incidents_last_event_at", "incidents", ["last_event_at"])
    op.create_index(
        "uq_incidents_open_group",
        "incidents",
        ["group_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'closed'"),
    )

    op.create_table(
        "incident_events",
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("incident_events")
    op.drop_table("incidents")
    op.drop_table("verdicts")
    op.drop_table("artifact_scans")
    op.drop_table("dictionary_terms")
    op.drop_table("dictionaries")
    op.drop_table("rule_versions")
    op.drop_table("rules")
    op.drop_table("event_queue")
    op.drop_index("ix_events_artifact_sha256", table_name="events")
    op.drop_column("events", "verdict_id")
```

Проверить, что `a4d2f6c81b53` — единственная голова: `.venv/Scripts/python -m alembic heads` должен показать одну голову `a4d2f6c81b53` до добавления файла и `c91f5e2a7d34` после.

- [ ] **Step 7: Сброс таблиц между тестами**

В `server/tests/conftest.py` в SQL `TRUNCATE` заменить начало списка таблиц на:

```python
                "TRUNCATE events, event_queue, verdicts, artifact_scans, incident_events, "
                "incidents, rule_versions, rules, dictionary_terms, dictionaries, "
                "artifacts, upload_sessions, commands, agent_configs, "
```

(остальная часть строки — `"agent_certificates, enrollment_tokens, " "console_sessions, agents, agent_groups, audit_log, users " "RESTART IDENTITY CASCADE"` — без изменений).

- [ ] **Step 8: Запустить тест — должен пройти**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_models.py tests/test_migrations.py -q`
Expected: PASS (4 + существующие).

- [ ] **Step 9: Полный прогон и проверки**

Run: `.venv/Scripts/python -m pytest -q` (с `BG_TEST_DATABASE_URL`), затем `ruff check .`, `ruff format --check .`, `mypy barysguard`.
Expected: всё зелёное (335 + 4 passed). Если `ruff format --check` ругается на новые файлы — выполнить `ruff format barysguard tests` и проверить, что diff касается только новых файлов.

- [ ] **Step 10: Commit**

```bash
git add server/pyproject.toml server/barysguard server/alembic server/tests
git commit -m "feat(server): tables, models and settings for content inspection

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Детекторы и оценка

**Files:**
- Create: `server/barysguard/services/inspection/__init__.py` (пустой), `server/barysguard/services/inspection/detectors.py`, `server/barysguard/services/inspection/scoring.py`, `server/tests/test_inspection_detectors.py`, `server/tests/test_inspection_scoring.py`

**Interfaces:**
- Produces (`detectors.py`):
  - `class Finding: count: int; samples: list[str]; add(sample: str) -> None`
  - `normalize(text: str) -> str` — `lower()` и `ё→е`.
  - `kz_control_ok(number: str) -> bool`, `is_iin(number: str) -> bool`, `is_bin(number: str) -> bool`, `luhn_ok(digits: str) -> bool`.
  - `class IinBinDetector(key: str = "iin_bin")`, `class CardDetector(key: str = "card")`, `class DictionaryDetector(key: str, terms: Iterable[str])` — у каждого `key: str`, `max_length: int`, `pattern: re.Pattern[str]`, `accept(match) -> str | None` (маска образца или `None`).
  - `class ContentScanner(detectors: Sequence[Detector])`: `feed(chunk: str) -> None`, `finish() -> dict[str, Finding]`.
- Produces (`scoring.py`): `RuleWeight(key, rule_version_id, weight, cap)`, `VerdictResult(status, severity, score, matches)`, `evaluate(findings: dict[str, dict[str, Any]], weights: Sequence[RuleWeight]) -> VerdictResult`, `classify(score: int) -> tuple[str, str]`, `FLAG_SCORE = 20`.
- `findings` в `evaluate` — формат скана: `{rule_key: {"count": int, "samples": list[str], ...}}`.

- [ ] **Step 1: Падающие тесты детекторов**

`server/tests/test_inspection_detectors.py`:

```python
"""Детекторы ИИН/БИН, карт и словаря; потоковое сканирование."""

import pytest

from barysguard.services.inspection.detectors import (
    CardDetector,
    ContentScanner,
    DictionaryDetector,
    IinBinDetector,
    is_bin,
    is_iin,
    luhn_ok,
    normalize,
)

# Контрольный разряд посчитан вручную по стандарту (веса 1..11, затем 3..11,1,2).
IIN_FIRST_PASS = "900101300017"  # остаток 7 с первого прохода
IIN_ZERO = "850612412340"  # остаток 0 с первого прохода
IIN_SECOND_PASS = "900101300811"  # первый проход даёт 10, второй — 1
IIN_BOTH_TEN = "900101300800"  # оба прохода дают 10 → номер невалиден
BIN_VALID = "120340000014"  # 5-я цифра 4, месяц 03

VISA = "4111111111111111"
MASTERCARD = "5555555555554444"
AMEX = "378282246310005"
MIR = "2200000000000004"


@pytest.mark.parametrize("number", [IIN_FIRST_PASS, IIN_ZERO, IIN_SECOND_PASS])
def test_valid_iin(number: str) -> None:
    assert is_iin(number) is True


@pytest.mark.parametrize(
    "number",
    [
        "900101300018",  # неверный контрольный разряд
        IIN_BOTH_TEN,  # оба прохода дают 10
        "901301300010",  # месяц 13
        "900230300010",  # 30 февраля
        "900101000010",  # 7-я цифра 0
        "900101700010",  # 7-я цифра 7
    ],
)
def test_invalid_iin(number: str) -> None:
    assert is_iin(number) is False


def test_valid_and_invalid_bin() -> None:
    assert is_bin(BIN_VALID) is True
    assert is_bin("120340000015") is False  # контрольный разряд
    assert is_bin("121340000010") is False  # месяц 13
    assert is_bin("120310000010") is False  # 5-я цифра 1


@pytest.mark.parametrize("number", [VISA, MASTERCARD, AMEX, MIR])
def test_luhn_accepts_known_numbers(number: str) -> None:
    assert luhn_ok(number) is True


def test_luhn_rejects_a_changed_digit() -> None:
    assert luhn_ok("4111111111111112") is False


def _scan(text: str, *detectors) -> dict:
    scanner = ContentScanner(list(detectors))
    scanner.feed(text)
    return scanner.finish()


def test_iin_is_found_masked_and_counted_once() -> None:
    found = _scan(f"ИИН клиента {IIN_FIRST_PASS}; БИН {BIN_VALID}", IinBinDetector())

    assert found["iin_bin"].count == 2
    assert found["iin_bin"].samples == ["**********17", "**********14"]


def test_digits_inside_a_longer_number_are_ignored() -> None:
    found = _scan(f"1{IIN_FIRST_PASS} {IIN_FIRST_PASS}9 {IIN_FIRST_PASS}", IinBinDetector())

    assert found["iin_bin"].count == 1


def test_random_twelve_digits_are_ignored() -> None:
    assert _scan("номер 123456789012", IinBinDetector())["iin_bin"].count == 0


@pytest.mark.parametrize(
    "text",
    [
        VISA,
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        MASTERCARD,
        AMEX,
        MIR,
    ],
)
def test_card_formats_are_found(text: str) -> None:
    found = _scan(f"оплата: {text}.", CardDetector())

    assert found["card"].count == 1
    assert found["card"].samples[0].endswith(text[-4:])
    assert set(found["card"].samples[0][:-4]) == {"*"}


def test_card_rejects_bad_luhn_unknown_prefix_and_long_runs() -> None:
    text = "4111111111111112 9111111111111111 41111111111111111111"
    assert _scan(text, CardDetector())["card"].count == 0


def test_dictionary_ignores_case_yo_and_word_boundaries() -> None:
    detector = DictionaryDetector("markings", ["Конфиденциально", "для служебного пользования"])
    text = "КОНФИДЕНЦИАЛЬНО. Для   служебного\nпользования. неконфиденциально"

    found = _scan(text, detector)

    assert found["markings"].count == 2
    assert found["markings"].samples == ["конфиденциально", "для служебного пользования"]


def test_dictionary_treats_yo_like_ye() -> None:
    assert normalize("Ёлка") == "елка"
    found = _scan("Тёмный список", DictionaryDetector("m", ["темный"]))
    assert found["m"].count == 1


def test_empty_dictionary_finds_nothing() -> None:
    assert _scan("что угодно", DictionaryDetector("m", []))["m"].count == 0


def test_samples_are_capped_at_five() -> None:
    found = _scan(" ".join([IIN_FIRST_PASS] * 9), IinBinDetector())

    assert found["iin_bin"].count == 9
    assert len(found["iin_bin"].samples) == 5


def _document() -> str:
    return (
        "Ведомость. ИИН "
        + IIN_FIRST_PASS
        + ", карта "
        + "4111 1111 1111 1111"
        + ". "
        + "9" * 30
        + " гриф: СТРОГО конфиденциально. "
        + "x" * 200
        + f" БИН {BIN_VALID}; ещё карта {MIR}; ИИН {IIN_ZERO}. "
        + "1" * 40
        + " конфиденциально"
    )


@pytest.mark.parametrize("size", [1, 2, 3, 7, 13, 50, 64, 1000])
def test_chunking_does_not_change_the_result(size: int) -> None:
    detectors = [IinBinDetector(), CardDetector(), DictionaryDetector("markings", ["конфиденциально"])]
    whole = _scan(_document(), *detectors)

    scanner = ContentScanner(detectors)
    text = _document()
    for start in range(0, len(text), size):
        scanner.feed(text[start : start + size])
    chunked = scanner.finish()

    assert {key: (f.count, f.samples) for key, f in chunked.items()} == {
        key: (f.count, f.samples) for key, f in whole.items()
    }
    assert whole["iin_bin"].count == 3
    assert whole["card"].count == 2
    assert whole["markings"].count == 2
```

- [ ] **Step 2: Запустить — должен упасть**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_detectors.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать детекторы**

`server/barysguard/services/inspection/__init__.py` — пустой файл.

`server/barysguard/services/inspection/detectors.py`:

```python
"""Детекторы чувствительных данных и потоковый сканер текста.

Каждое совпадение проверяется контрольной суммой: случайные цифры не должны
давать инцидентов. Найденное значение не сохраняется: детектор возвращает
только маску (ИИН/БИН — 2 последние цифры, карта — 4 последние).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

MAX_SAMPLES = 5

_WEIGHTS_FIRST = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
_WEIGHTS_SECOND = (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2)
_CENTURY = {1: 1800, 2: 1800, 3: 1900, 4: 1900, 5: 2000, 6: 2000}


def normalize(text: str) -> str:
    """Нижний регистр и ё=е: словарь и текст сравниваются в одном виде."""
    return text.lower().replace("ё", "е")


def kz_control_ok(number: str) -> bool:
    """Контрольный разряд ИИН/БИН: веса 1..11, при остатке 10 — веса 3..11,1,2."""
    head = [int(char) for char in number[:11]]
    total = sum(w * d for w, d in zip(_WEIGHTS_FIRST, head, strict=True)) % 11
    if total == 10:
        total = sum(w * d for w, d in zip(_WEIGHTS_SECOND, head, strict=True)) % 11
        if total == 10:
            return False
    return total == int(number[11])


def is_iin(number: str) -> bool:
    if not kz_control_ok(number):
        return False
    century = _CENTURY.get(int(number[6]))
    if century is None:
        return False
    try:
        date(century + int(number[0:2]), int(number[2:4]), int(number[4:6]))
    except ValueError:
        return False
    return True


def is_bin(number: str) -> bool:
    return kz_control_ok(number) and 1 <= int(number[2:4]) <= 12 and number[4] in "456"


def luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _card_network_ok(digits: str) -> bool:
    size = len(digits)
    if digits[0] == "4" and size in (13, 16, 19):  # Visa
        return True
    if size == 16 and (51 <= int(digits[:2]) <= 55 or 2221 <= int(digits[:4]) <= 2720):
        return True  # Mastercard
    if size == 16 and 2200 <= int(digits[:4]) <= 2204:  # МИР
        return True
    return size == 15 and digits[:2] in ("34", "37")  # Amex


@dataclass
class Finding:
    count: int = 0
    samples: list[str] = field(default_factory=list)

    def add(self, sample: str) -> None:
        self.count += 1
        if len(self.samples) < MAX_SAMPLES:
            self.samples.append(sample)


class Detector(Protocol):
    key: str
    max_length: int
    pattern: re.Pattern[str]

    def accept(self, match: re.Match[str]) -> str | None:
        """Маска образца, если совпадение настоящее; None — ложное срабатывание."""


class IinBinDetector:
    max_length = 12
    pattern = re.compile(r"(?<!\d)\d{12}(?!\d)")

    def __init__(self, key: str = "iin_bin") -> None:
        self.key = key

    def accept(self, match: re.Match[str]) -> str | None:
        number = match.group()
        if is_iin(number) or is_bin(number):
            return "*" * 10 + number[-2:]
        return None


class CardDetector:
    # До 19 цифр и до 18 одиночных разделителей между ними.
    max_length = 37
    pattern = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")

    def __init__(self, key: str = "card") -> None:
        self.key = key

    def accept(self, match: re.Match[str]) -> str | None:
        digits = re.sub(r"\D", "", match.group())
        if luhn_ok(digits) and _card_network_ok(digits):
            return "*" * (len(digits) - 4) + digits[-4:]
        return None


class DictionaryDetector:
    def __init__(self, key: str, terms: Iterable[str]) -> None:
        self.key = key
        cleaned = sorted(
            {normalize(" ".join(term.split())) for term in terms if term.strip()},
            key=len,
            reverse=True,
        )
        # Пробелы между словами допускают до четырёх пробельных символов подряд.
        self.max_length = max((len(term) + 3 * term.count(" ") for term in cleaned), default=0) + 2
        parts = [r"\s{1,4}".join(re.escape(word) for word in term.split(" ")) for term in cleaned]
        body = "|".join(parts) if parts else "(?!)"
        self.pattern = re.compile(rf"(?<!\w)(?:{body})(?!\w)")

    def accept(self, match: re.Match[str]) -> str | None:
        return " ".join(match.group().split())


class ContentScanner:
    """Потоковый поиск: текст подаётся порциями, совпадение на стыке не теряется.

    Порция склеивается с хвостом прошлой. Считаются только совпадения, начавшиеся
    до последних `overlap` символов (они гарантированно закончились внутри данных);
    остальное переходит в хвост вместе с одним символом контекста, чтобы
    проверка «не часть более длинного числа» видела предыдущий символ.
    """

    def __init__(self, detectors: Sequence[Detector]) -> None:
        self._detectors = list(detectors)
        self._overlap = max((d.max_length for d in self._detectors), default=0) + 2
        self._carry = ""
        self._context = 0
        self._findings = {d.key: Finding() for d in self._detectors}

    def feed(self, chunk: str) -> None:
        data = self._carry + normalize(chunk)
        owned_end = len(data) - self._overlap
        if owned_end <= self._context:
            self._carry = data
            return
        consumed = self._scan(data, self._context, owned_end)
        cut = max(owned_end, consumed)
        self._carry = data[cut - 1 :]
        self._context = 1

    def finish(self) -> dict[str, Finding]:
        self._scan(self._carry, self._context, len(self._carry))
        self._carry = ""
        return self._findings

    def _scan(self, data: str, start: int, end: int) -> int:
        """Считает совпадения, начавшиеся в [start, end); возвращает конец последнего."""
        consumed = 0
        for detector in self._detectors:
            for match in detector.pattern.finditer(data, start):
                if match.start() >= end:
                    break
                sample = detector.accept(match)
                if sample is not None:
                    self._findings[detector.key].add(sample)
                    consumed = max(consumed, match.end())
        return consumed
```

- [ ] **Step 4: Запустить — должен пройти**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_detectors.py -q`
Expected: PASS. Если `test_chunking_does_not_change_the_result` падает на размере 1 или 2 — проверить, что `cut >= 1` (при `owned_end > self._context >= 0` это так) и что `finditer(data, start)` учитывает контекст слева (lookbehind видит символ до `pos`).

- [ ] **Step 5: Падающие тесты оценки**

`server/tests/test_inspection_scoring.py`:

```python
"""Оценка и пороги вердикта."""

import pytest

from barysguard.services.inspection.scoring import RuleWeight, classify, evaluate

WEIGHTS = [
    RuleWeight("iin_bin", "v-iin", weight=20, cap=5),
    RuleWeight("card", "v-card", weight=25, cap=4),
    RuleWeight("markings", "v-mark", weight=15, cap=2),
]


def _findings(**counts: int) -> dict:
    return {key: {"count": n, "samples": ["***"]} for key, n in counts.items()}


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, ("clean", "info")),
        (19, ("clean", "info")),
        (20, ("flagged", "medium")),
        (49, ("flagged", "medium")),
        (50, ("flagged", "high")),
        (79, ("flagged", "high")),
        (80, ("flagged", "critical")),
        (100, ("flagged", "critical")),
    ],
)
def test_thresholds(score: int, expected: tuple[str, str]) -> None:
    assert classify(score) == expected


def test_contribution_is_weight_times_capped_count() -> None:
    result = evaluate(_findings(iin_bin=9, card=1), WEIGHTS)

    assert result.score == 20 * 5 + 25 * 1
    assert {m["rule_key"]: m["points"] for m in result.matches} == {"iin_bin": 100, "card": 25}


def test_total_is_capped_at_one_hundred() -> None:
    result = evaluate(_findings(iin_bin=5, card=4, markings=2), WEIGHTS)

    assert result.score == 100
    assert (result.status, result.severity) == ("flagged", "critical")


def test_clean_when_nothing_found() -> None:
    result = evaluate({}, WEIGHTS)

    assert (result.status, result.severity, result.score, result.matches) == (
        "clean",
        "info",
        0,
        [],
    )


def test_matches_carry_rule_version_and_masked_samples() -> None:
    result = evaluate(_findings(markings=1), WEIGHTS)

    assert result.matches == [
        {
            "rule_key": "markings",
            "rule_version_id": "v-mark",
            "count": 1,
            "points": 15,
            "samples": ["***"],
        }
    ]
    assert result.status == "clean"  # 15 < 20


def test_findings_of_unknown_rules_are_ignored() -> None:
    assert evaluate(_findings(other=3), WEIGHTS).score == 0
```

- [ ] **Step 6: Запустить — упасть; реализовать**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_scoring.py -q` → FAIL (`ModuleNotFoundError`).

`server/barysguard/services/inspection/scoring.py`:

```python
"""Оценка находок и пороги вердикта."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

FLAG_SCORE = 20


@dataclass(frozen=True)
class RuleWeight:
    key: str
    rule_version_id: str
    weight: int
    cap: int


@dataclass(frozen=True)
class VerdictResult:
    status: str
    severity: str
    score: int
    matches: list[dict[str, Any]]


def classify(score: int) -> tuple[str, str]:
    """(статус, критичность) по итоговой оценке."""
    if score < FLAG_SCORE:
        return "clean", "info"
    if score < 50:
        return "flagged", "medium"
    if score < 80:
        return "flagged", "high"
    return "flagged", "critical"


def evaluate(findings: dict[str, dict[str, Any]], weights: Sequence[RuleWeight]) -> VerdictResult:
    """Вклад правила — вес × min(число совпадений, потолок); итог не больше 100."""
    matches: list[dict[str, Any]] = []
    total = 0
    for rule in weights:
        found = findings.get(rule.key)
        if not found or found.get("count", 0) <= 0:
            continue
        count = int(found["count"])
        points = rule.weight * min(count, rule.cap)
        total += points
        matches.append(
            {
                "rule_key": rule.key,
                "rule_version_id": rule.rule_version_id,
                "count": count,
                "points": points,
                "samples": list(found.get("samples", [])),
            }
        )
    score = min(total, 100)
    status, severity = classify(score)
    return VerdictResult(status=status, severity=severity, score=score, matches=matches)
```

- [ ] **Step 7: Запустить оба файла и проверки**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_detectors.py tests/test_inspection_scoring.py -q` → PASS; `ruff check .`, `ruff format --check .`, `mypy barysguard` → чисто (при замечании формата — `ruff format` на новых файлах).

- [ ] **Step 8: Commit**

```bash
git add server/barysguard/services/inspection server/tests/test_inspection_detectors.py server/tests/test_inspection_scoring.py
git commit -m "feat(server): IIN/BIN, card and dictionary detectors with scoring

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Извлечение текста и движок скана

**Files:**
- Create: `server/barysguard/services/inspection/extract.py`, `server/barysguard/services/inspection/engine.py`, `server/tests/test_inspection_extract.py`

**Interfaces:**
- Consumes: `ContentScanner`, `Detector` из `detectors.py`.
- Produces (`extract.py`): `ExtractFailure(status: str)`, `Limits(max_text: int, max_unpacked: int, max_entries: int, max_ratio: int, timeout: float)`, `Deadline(seconds: float)` с `check()`, `TextStream` (итерируется строками, атрибут `truncated`), `extract(name: str, data: bytes, limits: Limits, deadline: Deadline) -> TextStream`.
- Produces (`engine.py`): `ScanOutcome(status: str, truncated: bool, findings: dict[str, dict[str, Any]])`, `scan_bytes(name: str, data: bytes, detectors: Sequence[Detector], limits: Limits) -> ScanOutcome`, `limits_from_settings(settings: Settings) -> Limits`. Статусы: `ok`, `unsupported`, `no_text`, `encrypted`, `too_large`, `error`. `findings` содержит только ключи с ненулевым счётчиком: `{key: {"count": n, "samples": [...]}}`.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_inspection_extract.py`:

```python
"""Извлечение текста и скан байтов: форматы, пределы, отказы."""

import io
import zipfile

import pytest
from pypdf import PdfWriter

from barysguard.services.inspection.detectors import CardDetector, IinBinDetector
from barysguard.services.inspection.engine import ScanOutcome, scan_bytes
from barysguard.services.inspection.extract import Limits

IIN = "900101300017"
CARD = "4111 1111 1111 1111"
LIMITS = Limits(
    max_text=1_000_000, max_unpacked=50_000_000, max_entries=1000, max_ratio=100, timeout=30.0
)
DETECTORS = [IinBinDetector(), CardDetector()]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _scan(name: str, data: bytes, limits: Limits = LIMITS) -> ScanOutcome:
    return scan_bytes(name, data, DETECTORS, limits)


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def test_plain_text_utf8_and_cp1251() -> None:
    utf8 = _scan("C:\\x\\a.txt", f"ИИН {IIN}".encode())
    cp1251 = _scan("E:\\b.csv", f"клиент;{IIN};карта;{CARD}".encode("cp1251"))

    assert utf8.status == "ok" and utf8.findings["iin_bin"]["count"] == 1
    assert cp1251.status == "ok"
    assert cp1251.findings["iin_bin"]["count"] == 1 and cp1251.findings["card"]["count"] == 1


def test_findings_hold_only_nonzero_counts_and_masks() -> None:
    outcome = _scan("a.txt", f"ИИН {IIN}".encode())

    assert set(outcome.findings) == {"iin_bin"}
    assert outcome.findings["iin_bin"]["samples"] == ["**********17"]
    assert IIN not in repr(outcome.findings)


def test_empty_and_blank_files_have_no_text() -> None:
    assert _scan("a.txt", b"").status == "no_text"
    assert _scan("a.txt", b"  \n\t ").status == "no_text"


def test_unknown_extension_is_unsupported() -> None:
    assert _scan("photo.jpg", b"\xff\xd8\xff").status == "unsupported"
    assert _scan("archive.zip", b"PK\x03\x04").status == "unsupported"


def test_docx_text_split_across_runs_is_found() -> None:
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body><w:p>'
        f"<w:r><w:t>ИИН 90010</w:t></w:r><w:r><w:t>1300017</w:t></w:r>"
        f"</w:p></w:body></w:document>"
    )

    outcome = _scan("report.docx", _zip({"word/document.xml": document}))

    assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 1


def test_xlsx_numeric_cell_and_shared_string_are_read() -> None:
    sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData><row>'
        f'<c r="A1"><v>{IIN}</v></c><c r="B1" t="s"><v>0</v></c>'
        f'<c r="C1" t="inlineStr"><is><t>{CARD}</t></is></c>'
        f"</row></sheetData></worksheet>"
    )
    strings = f'<?xml version="1.0"?><sst xmlns="{S}"><si><t>просто слово</t></si></sst>'

    outcome = _scan(
        "pay.xlsx", _zip({"xl/worksheets/sheet1.xml": sheet, "xl/sharedStrings.xml": strings})
    )

    assert outcome.status == "ok"
    assert outcome.findings["iin_bin"]["count"] == 1
    assert outcome.findings["card"]["count"] == 1


def test_pptx_slide_text_is_read() -> None:
    slide = (
        f'<?xml version="1.0"?><p:sld xmlns:p="urn:p" xmlns:a="{A}"><a:p><a:r>'
        f"<a:t>карта {CARD}</a:t></a:r></a:p></p:sld>"
    )

    outcome = _scan("deck.pptx", _zip({"ppt/slides/slide1.xml": slide}))

    assert outcome.findings["card"]["count"] == 1


def test_pdf_text_layer_is_read() -> None:
    outcome = _scan("a.pdf", make_pdf(f"IIN {IIN}"))

    assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 1


def test_pdf_without_text_has_no_text() -> None:
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    buffer = io.BytesIO()
    writer.write(buffer)

    assert _scan("scan.pdf", buffer.getvalue()).status == "no_text"


def test_password_protected_pdf_is_encrypted() -> None:
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)

    assert _scan("secret.pdf", buffer.getvalue()).status == "encrypted"


def test_password_protected_office_file_is_encrypted() -> None:
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 64

    assert _scan("secret.docx", ole).status == "encrypted"


def test_corrupt_office_and_pdf_files_are_errors_not_crashes() -> None:
    assert _scan("broken.docx", b"not a zip at all").status == "error"
    assert _scan("broken.pdf", b"%PDF-1.4 garbage").status == "error"
    bad_xml = _zip({"word/document.xml": "<w:document"})
    assert _scan("bad.docx", bad_xml).status == "error"


def test_xml_with_a_doctype_is_refused() -> None:
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'

    assert _scan("evil.docx", _zip({"word/document.xml": bomb})).status == "error"


def test_zip_bomb_by_ratio_is_too_large() -> None:
    payload = "<w:document xmlns:w='x'>" + "<w:t>0</w:t>" * 400_000 + "</w:document>"
    bomb = _zip({"word/document.xml": payload})

    assert _scan("bomb.docx", bomb).status == "too_large"


def test_zip_with_too_many_entries_is_too_large() -> None:
    many = _zip({f"f{i}.xml": "x" for i in range(5)})
    limits = Limits(max_text=10**6, max_unpacked=10**9, max_entries=3, max_ratio=100, timeout=30.0)

    assert _scan("many.docx", many, limits).status == "too_large"


def test_unpacked_size_limit_is_enforced() -> None:
    document = f'<w:document xmlns:w="{W}"><w:body><w:p><w:t>{"a " * 5000}</w:t></w:p></w:body></w:document>'
    limits = Limits(max_text=10**6, max_unpacked=1000, max_entries=10, max_ratio=10**6, timeout=30.0)

    assert _scan("big.docx", _zip({"word/document.xml": document}), limits).status == "too_large"


def test_text_is_truncated_at_the_limit() -> None:
    limits = Limits(max_text=100, max_unpacked=10**9, max_entries=10, max_ratio=100, timeout=30.0)
    text = ("x" * 90 + " ") + f"{IIN} " + "y" * 500

    outcome = _scan("a.txt", text.encode(), limits)

    assert outcome.status == "ok" and outcome.truncated is True
    assert "iin_bin" not in outcome.findings  # ИИН лежит за пределом 100 символов


def test_timeout_gives_an_error_status() -> None:
    limits = Limits(max_text=10**6, max_unpacked=10**9, max_entries=10, max_ratio=100, timeout=-1.0)

    assert _scan("a.txt", b"hello", limits).status == "error"


def test_extension_is_taken_from_a_windows_path() -> None:
    assert _scan("E:\\Папка с пробелами\\Файл.TXT", f"{IIN}".encode()).status == "ok"
```

- [ ] **Step 2: Запустить — упасть**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_extract.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать извлечение**

`server/barysguard/services/inspection/extract.py`:

```python
"""Извлечение текста из файлов с защитными пределами.

Формат выбирается по расширению имени файла (из пути события), а не по типу,
заявленному агентом. Контейнеры Office разбираются стандартной библиотекой;
файл читается из байтов в памяти и на диск не пишется.
"""

import io
import re
import time
import zipfile
import xml.etree.ElementTree as ET  # noqa: S405 - XML Office без DTD, DOCTYPE отсекается вручную
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PureWindowsPath

CHUNK = 64 * 1024
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
# Степень сжатия проверяется только у крупных элементов: у мелких она ни о чём не говорит.
_RATIO_FLOOR = 1 << 20
_MAX_PDF_PAGES = 2000

TEXT_EXTENSIONS = frozenset(
    {
        ".txt", ".csv", ".tsv", ".json", ".log", ".md", ".xml", ".html", ".htm", ".ini",
        ".cfg", ".conf", ".yaml", ".yml", ".py", ".js", ".ts", ".java", ".go", ".c", ".h",
        ".cpp", ".cs", ".sql", ".sh", ".ps1", ".bat",
    }
)  # fmt: skip
OOXML_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})


class ExtractFailure(Exception):  # noqa: N818 - имя отражает смысл, статус скана в .status
    """Файл нельзя разобрать; `status` — итоговый статус скана."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class Limits:
    max_text: int
    max_unpacked: int
    max_entries: int
    max_ratio: int
    timeout: float


class Deadline:
    """Кооперативный таймаут: проверяется между порциями и страницами."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self._end:
            raise ExtractFailure("error")


class TextStream:
    """Текст порциями; `truncated` становится истинным, если сработал предел."""

    def __init__(self, source: Iterator[str], max_chars: int, deadline: Deadline) -> None:
        self._source = source
        self._remaining = max_chars
        self._deadline = deadline
        self.truncated = False

    def __iter__(self) -> Iterator[str]:
        for piece in self._source:
            self._deadline.check()
            for start in range(0, len(piece), CHUNK):
                part = piece[start : start + CHUNK]
                if len(part) > self._remaining:
                    if self._remaining > 0:
                        yield part[: self._remaining]
                    self.truncated = True
                    return
                self._remaining -= len(part)
                yield part


def extract(name: str, data: bytes, limits: Limits, deadline: Deadline) -> TextStream:
    extension = PureWindowsPath(name).suffix.lower()
    if extension in OOXML_EXTENSIONS:
        source = _ooxml(extension, data, limits)
    elif extension == ".pdf":
        source = _pdf(data, deadline)
    elif extension in TEXT_EXTENSIONS:
        source = iter([_decode(data)])
    else:
        raise ExtractFailure("unsupported")
    return TextStream(source, limits.max_text, deadline)


def _decode(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


# --- Office -----------------------------------------------------------------


class _Budget:
    def __init__(self, left: int) -> None:
        self.left = left


def _open_archive(data: bytes, limits: Limits) -> zipfile.ZipFile:
    if data[:8] == _OLE_MAGIC:
        # Документ Office с паролем хранится как составной файл OLE, а не как zip.
        raise ExtractFailure("encrypted")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExtractFailure("error") from None
    infos = archive.infolist()
    if len(infos) > limits.max_entries:
        raise ExtractFailure("too_large")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ExtractFailure("encrypted")
        total += info.file_size
        if (
            info.file_size > _RATIO_FLOOR
            and info.compress_size
            and info.file_size / info.compress_size > limits.max_ratio
        ):
            raise ExtractFailure("too_large")
    if total > limits.max_unpacked:
        raise ExtractFailure("too_large")
    return archive


def _read_member(archive: zipfile.ZipFile, name: str, budget: _Budget) -> bytes:
    # Заголовок архива может врать о размере: читаем не больше оставшегося бюджета.
    try:
        with archive.open(name) as member:
            raw = member.read(budget.left + 1)
    except (zipfile.BadZipFile, KeyError, NotImplementedError, RuntimeError):
        raise ExtractFailure("error") from None
    if len(raw) > budget.left:
        raise ExtractFailure("too_large")
    budget.left -= len(raw)
    return raw


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_events(raw: bytes) -> Iterator[ET.Element]:
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ExtractFailure("error")
    try:
        for _, element in ET.iterparse(io.BytesIO(raw), events=("end",)):  # noqa: S314 - DOCTYPE отсечён выше
            yield element
    except ET.ParseError:
        raise ExtractFailure("error") from None


def _text_runs(raw: bytes, text_tag: str, break_tag: str) -> Iterator[str]:
    for element in _xml_events(raw):
        name = _local(element.tag)
        if name == text_tag and element.text:
            yield element.text
        elif name == break_tag:
            yield "\n"
            element.clear()


def _xlsx_sheet(raw: bytes) -> Iterator[str]:
    for element in _xml_events(raw):
        name = _local(element.tag)
        if name == "c":
            kind = element.get("t")
            if kind == "inlineStr":
                yield "".join(node.text or "" for node in element.iter() if _local(node.tag) == "t")
            elif kind != "s":
                value = element.find("{*}v")
                if value is not None and value.text:
                    yield value.text
            yield "\n"
            element.clear()


def _natural(names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", n)])


def _ooxml(extension: str, data: bytes, limits: Limits) -> Iterator[str]:
    archive = _open_archive(data, limits)
    budget = _Budget(limits.max_unpacked)
    names = archive.namelist()
    if extension == ".docx":
        parts = [n for n in names if re.fullmatch(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", n)]
        for part in _natural(parts):
            yield from _text_runs(_read_member(archive, part, budget), "t", "p")
    elif extension == ".pptx":
        for part in _natural([n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]):
            yield from _text_runs(_read_member(archive, part, budget), "t", "p")
    else:
        if "xl/sharedStrings.xml" in names:
            yield from _text_runs(_read_member(archive, "xl/sharedStrings.xml", budget), "t", "si")
        for part in _natural([n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]):
            yield from _xlsx_sheet(_read_member(archive, part, budget))


# --- PDF --------------------------------------------------------------------


def _pdf(data: bytes, deadline: Deadline) -> Iterator[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:  # noqa: BLE001 - любой сбой расшифровки означает «закрыт паролем»
                unlocked = 0
            if not unlocked:
                raise ExtractFailure("encrypted")
        for index, page in enumerate(reader.pages):
            if index >= _MAX_PDF_PAGES:
                return
            deadline.check()
            text = page.extract_text() or ""
            if text:
                yield text + "\n"
    except ExtractFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf бросает много разных исключений на битых файлах
        raise ExtractFailure("error") from exc
```

Замечание по формату: блок `TEXT_EXTENSIONS` помечен `# fmt: skip`; остальной код форматировать `ruff format` (длинные строки в `_ooxml` будут перенесены).

- [ ] **Step 4: Реализовать движок**

`server/barysguard/services/inspection/engine.py`:

```python
"""Скан байтов файла: извлечение текста и поиск, в одном синхронном вызове.

Вызывается через asyncio.to_thread: разбор файла не должен занимать цикл событий.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from barysguard.core.config import Settings
from barysguard.services.inspection.detectors import ContentScanner, Detector
from barysguard.services.inspection.extract import Deadline, ExtractFailure, Limits, extract


@dataclass(frozen=True)
class ScanOutcome:
    status: str
    truncated: bool
    # {rule_key: {"count": n, "samples": [...]}} — только ключи с ненулевым счётчиком.
    findings: dict[str, dict[str, Any]]


def limits_from_settings(settings: Settings) -> Limits:
    return Limits(
        max_text=settings.inspect_max_text_bytes,
        max_unpacked=settings.inspect_max_unpacked_bytes,
        max_entries=settings.inspect_max_entries,
        max_ratio=settings.inspect_max_ratio,
        timeout=float(settings.inspect_timeout_seconds),
    )


def scan_bytes(
    name: str, data: bytes, detectors: Sequence[Detector], limits: Limits
) -> ScanOutcome:
    deadline = Deadline(limits.timeout)
    try:
        stream = extract(name, data, limits, deadline)
        scanner = ContentScanner(detectors)
        seen = 0
        for part in stream:
            seen += len(part.strip())
            scanner.feed(part)
            deadline.check()
        if seen == 0:
            return ScanOutcome("no_text", False, {})
        found = scanner.finish()
    except ExtractFailure as failure:
        return ScanOutcome(failure.status, False, {})

    findings = {
        key: {"count": finding.count, "samples": finding.samples}
        for key, finding in found.items()
        if finding.count > 0
    }
    return ScanOutcome("ok", stream.truncated, findings)
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_extract.py -q`
Expected: PASS. Возможные расхождения, которые надо разрешить, не ослабляя тесты:
- `test_zip_bomb_by_ratio_is_too_large`: полезная нагрузка должна быть > 1 МиБ в распакованном виде и сжиматься более чем в 100 раз (повтор `<w:t>0</w:t>` × 400 000 ≈ 4,8 МБ и сжимается ≫100×). Если порог `_RATIO_FLOOR` не достигнут — увеличить число повторов.
- `test_pdf_without_text_has_no_text`: пустая страница `pypdf` даёт пустой текст → `seen == 0` → `no_text`.
- `test_password_protected_pdf_is_encrypted`: для `PdfWriter.encrypt("secret")` по умолчанию используется AES, нужна `cryptography` (уже в зависимостях).
- `test_text_is_truncated_at_the_limit`: ИИН начинается на позиции 91 — внутри усечённых 100 символов лежит лишь его начало, совпадения быть не должно.

- [ ] **Step 6: Проверки и commit**

Run: `ruff check .`, `ruff format barysguard tests` (форматирование новых файлов), `ruff format --check .`, `mypy barysguard`.

```bash
git add server/barysguard/services/inspection server/tests/test_inspection_extract.py
git commit -m "feat(server): text extraction with safety limits and the scan engine

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Правила: заведение и загрузка набора, команда seed-rules

**Files:**
- Create: `server/barysguard/services/inspection/rules.py`, `server/tests/test_inspection_rules.py`
- Modify: `server/barysguard/cli.py`

**Interfaces:**
- Consumes: модели `Rule`, `RuleVersion`, `Dictionary`, `DictionaryTerm`; детекторы; `RuleWeight`.
- Produces: `BUILTIN_TERMS: tuple[str, ...]`, `terms_hash(terms: Iterable[str]) -> str`, `SeedReport(rules_created: int, versions_created: int, terms_added: int)`, `seed_rules(session: AsyncSession) -> SeedReport`, `RuleRuntime`, `Ruleset(hash: str, rules: tuple[RuleRuntime, ...])` с методами `detectors() -> list[Detector]`, `weights() -> list[RuleWeight]`, `version_ids() -> dict[str, str]`; `load_ruleset(session) -> Ruleset`. Команды `barysguard-admin seed-rules`; `bootstrap-dev` вызывает `seed_rules`.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_inspection_rules.py`:

```python
"""Заведение встроенных правил и загрузка действующего набора."""

from sqlalchemy import select

from barysguard.db.models.inspection import Dictionary, DictionaryTerm, Rule, RuleVersion
from barysguard.services.inspection.detectors import ContentScanner
from barysguard.services.inspection.rules import BUILTIN_TERMS, load_ruleset, seed_rules


async def test_seed_creates_three_rules_with_first_versions(app_client, session) -> None:
    report = await seed_rules(session)
    await session.commit()

    assert (report.rules_created, report.versions_created) == (3, 3)
    assert report.terms_added == len(BUILTIN_TERMS)
    keys = set((await session.scalars(select(Rule.key))).all())
    assert keys == {"iin_bin", "card", "markings"}
    versions = (await session.scalars(select(RuleVersion))).all()
    assert {v.version for v in versions} == {1}


async def test_seed_is_idempotent(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()

    again = await seed_rules(session)
    await session.commit()

    assert (again.rules_created, again.versions_created, again.terms_added) == (0, 0, 0)
    assert len((await session.scalars(select(RuleVersion))).all()) == 3


async def test_changed_dictionary_gets_a_new_rule_version_and_ruleset_hash(
    app_client, session
) -> None:
    await seed_rules(session)
    await session.commit()
    before = await load_ruleset(session)

    dictionary = (await session.scalars(select(Dictionary))).one()
    session.add(DictionaryTerm(dictionary_id=dictionary.id, term="тайна"))
    await session.commit()
    report = await seed_rules(session)
    await session.commit()
    after = await load_ruleset(session)

    assert report.versions_created == 1
    assert after.hash != before.hash
    markings_versions = (
        await session.scalars(
            select(RuleVersion.version).join(Rule).where(Rule.key == "markings").order_by(RuleVersion.version)
        )
    ).all()
    assert list(markings_versions) == [1, 2]


async def test_ruleset_builds_detectors_and_weights(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()

    ruleset = await load_ruleset(session)

    weights = {w.key: (w.weight, w.cap) for w in ruleset.weights()}
    assert weights == {"iin_bin": (20, 5), "card": (25, 4), "markings": (15, 2)}
    assert set(ruleset.version_ids()) == {"iin_bin", "card", "markings"}

    scanner = ContentScanner(ruleset.detectors())
    scanner.feed("Строго конфиденциально. ИИН 900101300017, карта 4111 1111 1111 1111")
    found = scanner.finish()
    assert (found["iin_bin"].count, found["card"].count, found["markings"].count) == (1, 1, 1)


async def test_disabled_rule_leaves_the_ruleset(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()
    full = await load_ruleset(session)
    rule = (await session.scalars(select(Rule).where(Rule.key == "card"))).one()
    rule.enabled = False
    await session.commit()

    reduced = await load_ruleset(session)

    assert {w.key for w in reduced.weights()} == {"iin_bin", "markings"}
    assert reduced.hash != full.hash


async def test_ruleset_is_empty_before_seeding(app_client, session) -> None:
    ruleset = await load_ruleset(session)

    assert ruleset.weights() == [] and ruleset.detectors() == []
```

- [ ] **Step 2: Запустить — упасть**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_rules.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать**

`server/barysguard/services/inspection/rules.py`:

```python
"""Правила инспекции: встроенный набор и загрузка действующих версий."""

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.inspection import Dictionary, DictionaryTerm, Rule, RuleVersion
from barysguard.services.inspection.detectors import (
    CardDetector,
    Detector,
    DictionaryDetector,
    IinBinDetector,
)
from barysguard.services.inspection.scoring import RuleWeight

MARKINGS_KEY = "markings"

BUILTIN_TERMS = (
    "конфиденциально",
    "строго конфиденциально",
    "для служебного пользования",
    "дсп",
    "коммерческая тайна",
    "служебная тайна",
    "секретно",
    "не для распространения",
)

BUILTIN_RULES: tuple[dict[str, Any], ...] = (
    {
        "key": "iin_bin",
        "kind": "detector",
        "title": "ИИН/БИН (Казахстан)",
        "params": {"detector": "iin_bin", "weight": 20, "cap": 5},
    },
    {
        "key": "card",
        "kind": "detector",
        "title": "Банковские карты",
        "params": {"detector": "card", "weight": 25, "cap": 4},
    },
    {
        "key": MARKINGS_KEY,
        "kind": "dictionary",
        "title": "Грифы конфиденциальности",
        "params": {"weight": 15, "cap": 2},
    },
)


def terms_hash(terms: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(terms)).encode()).hexdigest()


@dataclass(frozen=True)
class SeedReport:
    rules_created: int
    versions_created: int
    terms_added: int


async def seed_rules(session: AsyncSession) -> SeedReport:
    """Заводит встроенные правила и словарь. Повторный вызов ничего не дублирует.

    Параметры возвращаются к встроенным: ручные правки весов (когда появится
    редактор) повторный запуск перезапишет новой версией.
    """
    dictionary = await session.scalar(select(Dictionary).where(Dictionary.key == MARKINGS_KEY))
    if dictionary is None:
        dictionary = Dictionary(key=MARKINGS_KEY, title="Грифы")
        session.add(dictionary)
        await session.flush()

    existing = set(
        (
            await session.scalars(
                select(DictionaryTerm.term).where(DictionaryTerm.dictionary_id == dictionary.id)
            )
        ).all()
    )
    added = [term for term in BUILTIN_TERMS if term not in existing]
    session.add_all(DictionaryTerm(dictionary_id=dictionary.id, term=term) for term in added)
    await session.flush()
    digest = terms_hash(existing | set(added))

    rules_created = versions_created = 0
    for definition in BUILTIN_RULES:
        rule = await session.scalar(select(Rule).where(Rule.key == definition["key"]))
        if rule is None:
            rule = Rule(key=definition["key"], kind=definition["kind"], title=definition["title"])
            session.add(rule)
            await session.flush()
            rules_created += 1

        params = dict(definition["params"])
        if rule.kind == "dictionary":
            params["dictionary_id"] = str(dictionary.id)
            params["terms_hash"] = digest

        latest = await session.scalar(
            select(RuleVersion)
            .where(RuleVersion.rule_id == rule.id)
            .order_by(RuleVersion.version.desc())
            .limit(1)
        )
        if latest is None or latest.params != params:
            session.add(
                RuleVersion(
                    rule_id=rule.id,
                    version=latest.version + 1 if latest else 1,
                    params=params,
                )
            )
            versions_created += 1

    await session.flush()
    return SeedReport(rules_created, versions_created, len(added))


@dataclass(frozen=True)
class RuleRuntime:
    key: str
    kind: str
    detector: str
    rule_version_id: str
    weight: int
    cap: int
    terms: tuple[str, ...]


@dataclass(frozen=True)
class Ruleset:
    hash: str
    rules: tuple[RuleRuntime, ...]

    def detectors(self) -> list[Detector]:
        result: list[Detector] = []
        for rule in self.rules:
            if rule.kind == "dictionary":
                result.append(DictionaryDetector(rule.key, rule.terms))
            elif rule.detector == "iin_bin":
                result.append(IinBinDetector(rule.key))
            elif rule.detector == "card":
                result.append(CardDetector(rule.key))
        return result

    def weights(self) -> list[RuleWeight]:
        return [RuleWeight(r.key, r.rule_version_id, r.weight, r.cap) for r in self.rules]

    def version_ids(self) -> dict[str, str]:
        return {rule.key: rule.rule_version_id for rule in self.rules}


async def load_ruleset(session: AsyncSession) -> Ruleset:
    """Последние версии включённых правил и хеш всего набора (с учётом терминов словарей)."""
    latest = (
        select(RuleVersion.rule_id, func.max(RuleVersion.version).label("version"))
        .group_by(RuleVersion.rule_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Rule, RuleVersion)
            .join(latest, latest.c.rule_id == Rule.id)
            .join(
                RuleVersion,
                and_(
                    RuleVersion.rule_id == latest.c.rule_id,
                    RuleVersion.version == latest.c.version,
                ),
            )
            .where(Rule.enabled.is_(True))
            .order_by(Rule.key)
        )
    ).all()

    runtime: list[RuleRuntime] = []
    lines: list[str] = []
    for rule, version in rows:
        terms: tuple[str, ...] = ()
        if rule.kind == "dictionary":
            dictionary_id = uuid.UUID(version.params["dictionary_id"])
            terms = tuple(
                sorted(
                    (
                        await session.scalars(
                            select(DictionaryTerm.term).where(
                                DictionaryTerm.dictionary_id == dictionary_id
                            )
                        )
                    ).all()
                )
            )
        lines.append(f"{version.id}:{terms_hash(terms)}")
        runtime.append(
            RuleRuntime(
                key=rule.key,
                kind=rule.kind,
                detector=str(version.params.get("detector", "")),
                rule_version_id=str(version.id),
                weight=int(version.params["weight"]),
                cap=int(version.params["cap"]),
                terms=terms,
            )
        )
    digest = hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()
    return Ruleset(hash=digest, rules=tuple(runtime))
```

- [ ] **Step 4: CLI**

В `server/barysguard/cli.py` (импорты — рядом с существующими):

```python
from barysguard.services.inspection.rules import seed_rules
```

Добавить функцию рядом с `_seed_demo_events`:

```python
async def _seed_rules() -> int:
    engine = create_engine_from_url(get_settings().database_url)
    try:
        async with session_factory(engine)() as session:
            report = await seed_rules(session)
            await session.commit()
    finally:
        await engine.dispose()

    print(
        f"правил создано: {report.rules_created}, версий: {report.versions_created}, "
        f"терминов добавлено: {report.terms_added}"
    )
    return 0
```

В `main()` добавить подкоманду и диспетчеризацию:

```python
    sub.add_parser("seed-rules", help="завести встроенные правила инспекции (идемпотентно)")
```

```python
    if args.command == "seed-rules":
        raise SystemExit(asyncio.run(_seed_rules()))
```

В `_bootstrap_dev` внутри `async with session_factory(engine)() as session:` после `report = await bootstrap_stand(...)` добавить `await seed_rules(session)` (до `await session.commit()`).

Тест CLI: добавить в `server/tests/test_cli.py` (посмотреть существующие тесты рядом и повторить их стиль) тест, что `_seed_rules()` на мигрированной БД возвращает 0 и дважды подряд не падает. Если `test_cli.py` вызывает функции CLI с подменой `BG_DATABASE_URL` — использовать тот же приём.

- [ ] **Step 5: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_rules.py tests/test_cli.py -q` → PASS; затем `ruff check .`, `ruff format --check .`, `mypy barysguard`.

- [ ] **Step 6: Commit**

```bash
git add server/barysguard server/tests
git commit -m "feat(server): built-in inspection rules, ruleset loading and seed-rules command

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Очередь: постановка в трёх точках и захват задач

**Files:**
- Create: `server/barysguard/services/inspection/queue.py`, `server/tests/test_inspection_queue.py`
- Modify: `server/barysguard/services/events.py`, `server/barysguard/services/artifacts.py`

**Interfaces:**
- Produces (`queue.py`):
  - `QueueTask(id: int, occurred_at: datetime, event_id: uuid.UUID, artifact_sha256: str, attempts: int)` (frozen dataclass)
  - `enqueue_for_artifact(session, sha256: str) -> int`
  - `enqueue_new_events(session, keys: Sequence[tuple[datetime, uuid.UUID]]) -> int` — ставит только события, чей артефакт уже есть
  - `enqueue_missed(session, now: datetime, window: timedelta = timedelta(hours=24)) -> int`
  - `claim_batch(session, *, limit: int, lock_seconds: int, now: datetime) -> list[QueueTask]`
  - `fail_exhausted(session, *, max_attempts: int, now: datetime) -> list[QueueTask]`
  - `finish_task(session, task_id: int) -> None`
- Изменения: `store_events` ставит в очередь вставленные события с известным артефактом; `_finalize` ставит в очередь события нового артефакта.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_inspection_queue.py`:

```python
"""Постановка в очередь и захват задач."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import EventQueue
from barysguard.services.inspection.queue import (
    claim_batch,
    enqueue_for_artifact,
    enqueue_missed,
    enqueue_new_events,
    fail_exhausted,
    finish_task,
)
from tests.helpers import enroll_agent

SHA = "ef" * 32
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


async def _event(session, agent_id, *, sha: str | None = SHA, verdict: uuid.UUID | None = None):
    event = Event(
        occurred_at=datetime.now(UTC),
        event_id=uuid.uuid4(),
        agent_id=agent_id,
        schema_version=1,
        channel="file",
        action="copy",
        artifact_sha256=sha,
        verdict_id=verdict,
    )
    session.add(event)
    await session.flush()
    return event


def _artifact(sha: str = SHA, first_seen: datetime | None = None) -> Artifact:
    artifact = Artifact(sha256=sha, size=5, storage_path="x", key_wrapped=b"k" * 40)
    if first_seen is not None:
        artifact.first_seen_at = first_seen
    return artifact


async def _queued(session) -> list[EventQueue]:
    return list((await session.scalars(select(EventQueue).order_by(EventQueue.id))).all())


async def test_enqueue_for_artifact_takes_events_without_verdict(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-art")
    waiting = await _event(session, agent.agent_id)
    await _event(session, agent.agent_id, verdict=uuid.uuid4())
    await _event(session, agent.agent_id, sha="aa" * 32)
    await _event(session, agent.agent_id, sha=None)

    assert await enqueue_for_artifact(session, SHA) == 1
    assert await enqueue_for_artifact(session, SHA) == 0  # идемпотентно

    rows = await _queued(session)
    assert [(r.event_id, r.artifact_sha256) for r in rows] == [(waiting.event_id, SHA)]


async def test_new_events_are_queued_only_when_the_artifact_exists(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-new")
    known = await _event(session, agent.agent_id)
    unknown = await _event(session, agent.agent_id, sha="bb" * 32)
    keys = [(known.occurred_at, known.event_id), (unknown.occurred_at, unknown.event_id)]

    assert await enqueue_new_events(session, keys) == 0  # артефакта ещё нет

    session.add(_artifact())
    await session.flush()
    assert await enqueue_new_events(session, keys) == 1

    assert [r.event_id for r in await _queued(session)] == [known.event_id]
    assert await enqueue_new_events(session, []) == 0


async def test_missed_sweep_covers_only_recent_artifacts(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-miss")
    old_sha = "cc" * 32
    session.add_all([_artifact(first_seen=NOW - timedelta(hours=1)), _artifact(old_sha, NOW - timedelta(days=3))])
    fresh = await _event(session, agent.agent_id)
    await _event(session, agent.agent_id, sha=old_sha)
    await session.flush()

    assert await enqueue_missed(session, NOW) == 1
    assert [r.event_id for r in await _queued(session)] == [fresh.event_id]
    assert await enqueue_missed(session, NOW) == 0


async def test_claim_locks_tasks_and_skips_locked_ones(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-claim")
    session.add(_artifact())
    for _ in range(3):
        await _event(session, agent.agent_id)
    await enqueue_for_artifact(session, SHA)

    first = await claim_batch(session, limit=2, lock_seconds=300, now=NOW)
    second = await claim_batch(session, limit=5, lock_seconds=300, now=NOW)
    later = await claim_batch(session, limit=5, lock_seconds=300, now=NOW + timedelta(seconds=301))

    assert len(first) == 2 and all(t.attempts == 1 for t in first)
    assert len(second) == 1  # замкнутые не выдаются повторно
    assert len(later) == 3  # срок блокировки истёк
    assert sorted(t.attempts for t in later) == [2, 2, 2]


async def test_finish_removes_the_task(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-fin")
    await _event(session, agent.agent_id)
    await enqueue_for_artifact(session, SHA)
    (task,) = await claim_batch(session, limit=1, lock_seconds=60, now=NOW)

    await finish_task(session, task.id)

    assert await _queued(session) == []


async def test_exhausted_tasks_are_marked_failed_and_returned(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "q-fail")
    await _event(session, agent.agent_id)
    await enqueue_for_artifact(session, SHA)
    for step in range(3):
        await claim_batch(session, limit=1, lock_seconds=60, now=NOW + timedelta(minutes=step * 2))

    # Третья попытка ещё держит блокировку: не провал.
    assert await fail_exhausted(session, max_attempts=3, now=NOW + timedelta(minutes=4, seconds=30)) == []

    failed = await fail_exhausted(session, max_attempts=3, now=NOW + timedelta(minutes=10))

    assert len(failed) == 1 and failed[0].attempts == 3
    assert [r.state for r in await _queued(session)] == ["failed"]
    assert await claim_batch(session, limit=5, lock_seconds=60, now=NOW + timedelta(hours=1)) == []
```

- [ ] **Step 2: Запустить — упасть** (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать очередь**

`server/barysguard/services/inspection/queue.py`:

```python
"""Очередь инспекции в PostgreSQL: постановка и захват через SKIP LOCKED."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Select, Uuid, and_, delete, or_, select, update, values
from sqlalchemy import DateTime as SADateTime
from sqlalchemy import column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import EventQueue

_COLUMNS = ["event_occurred_at", "event_id", "artifact_sha256"]
_BATCH = 500


@dataclass(frozen=True)
class QueueTask:
    id: int
    occurred_at: datetime
    event_id: uuid.UUID
    artifact_sha256: str
    attempts: int


async def _enqueue(session: AsyncSession, source: Select[Any]) -> int:
    statement = (
        pg_insert(EventQueue)
        .from_select(_COLUMNS, source)
        .on_conflict_do_nothing(index_elements=["event_occurred_at", "event_id"])
    )
    result = cast("CursorResult[Any]", await session.execute(statement))
    return int(result.rowcount or 0)


async def enqueue_for_artifact(session: AsyncSession, sha256: str) -> int:
    """Ставит события артефакта, у которых ещё нет вердикта (вызывается при завершении загрузки)."""
    return await _enqueue(
        session,
        select(Event.occurred_at, Event.event_id, Event.artifact_sha256).where(
            Event.artifact_sha256 == sha256, Event.verdict_id.is_(None)
        ),
    )


async def enqueue_new_events(
    session: AsyncSession, keys: Sequence[tuple[datetime, uuid.UUID]]
) -> int:
    """Ставит только что принятые события, чей артефакт уже загружен."""
    total = 0
    for start in range(0, len(keys), _BATCH):
        part = keys[start : start + _BATCH]
        wanted = values(
            column("occurred_at", SADateTime(timezone=True)),
            column("event_id", Uuid()),
            name="wanted",
        ).data(list(part))
        total += await _enqueue(
            session,
            select(Event.occurred_at, Event.event_id, Event.artifact_sha256)
            .join(
                wanted,
                and_(Event.occurred_at == wanted.c.occurred_at, Event.event_id == wanted.c.event_id),
            )
            .join(Artifact, Artifact.sha256 == Event.artifact_sha256)
            .where(Event.verdict_id.is_(None)),
        )
    return total


async def enqueue_missed(
    session: AsyncSession, now: datetime, window: timedelta = timedelta(hours=24)
) -> int:
    """Страховка от гонки вокруг завершения загрузки: свежие артефакты и их события."""
    recent = select(Artifact.sha256).where(Artifact.first_seen_at >= now - window)
    return await _enqueue(
        session,
        select(Event.occurred_at, Event.event_id, Event.artifact_sha256).where(
            Event.artifact_sha256.in_(recent), Event.verdict_id.is_(None)
        ),
    )


async def claim_batch(
    session: AsyncSession, *, limit: int, lock_seconds: int, now: datetime
) -> list[QueueTask]:
    """Забирает готовые задачи и блокирует их; параллельные воркеры не пересекаются."""
    ready = (
        select(EventQueue.id)
        .where(
            EventQueue.state == "pending",
            or_(EventQueue.locked_until.is_(None), EventQueue.locked_until < now),
        )
        .order_by(EventQueue.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    statement = (
        update(EventQueue)
        .where(EventQueue.id.in_(ready.scalar_subquery()))
        .values(
            attempts=EventQueue.attempts + 1,
            locked_until=now + timedelta(seconds=lock_seconds),
        )
        .returning(
            EventQueue.id,
            EventQueue.event_occurred_at,
            EventQueue.event_id,
            EventQueue.artifact_sha256,
            EventQueue.attempts,
        )
    )
    rows = (await session.execute(statement)).all()
    return [QueueTask(*row) for row in rows]


async def fail_exhausted(
    session: AsyncSession, *, max_attempts: int, now: datetime
) -> list[QueueTask]:
    """Задачи, исчерпавшие попытки и не удерживаемые воркером, получают состояние failed."""
    statement = (
        update(EventQueue)
        .where(
            EventQueue.state == "pending",
            EventQueue.attempts >= max_attempts,
            EventQueue.locked_until < now,
        )
        .values(state="failed")
        .returning(
            EventQueue.id,
            EventQueue.event_occurred_at,
            EventQueue.event_id,
            EventQueue.artifact_sha256,
            EventQueue.attempts,
        )
    )
    rows = (await session.execute(statement)).all()
    return [QueueTask(*row) for row in rows]


async def finish_task(session: AsyncSession, task_id: int) -> None:
    await session.execute(delete(EventQueue).where(EventQueue.id == task_id))
```

Замечание: в SQLAlchemy 2.x `values` и `column` — из `sqlalchemy` (`from sqlalchemy import values, column`); `DateTime` импортирован как `SADateTime`, чтобы не конфликтовать. Если линтер сообщит об упорядочивании импортов — `ruff check --fix` на этом файле.

- [ ] **Step 4: Подключить постановку в `store_events`**

В `server/barysguard/services/events.py`: добавить импорт `from barysguard.services.inspection.queue import enqueue_new_events`. Заменить блок вставки:

```python
    inserted = 0
    inserted_keys: list[tuple[datetime, uuid.UUID]] = []
    for start in range(0, len(rows), INSERT_CHUNK):
        statement = (
            pg_insert(Event)
            .values(rows[start : start + INSERT_CHUNK])
            .on_conflict_do_nothing(index_elements=["occurred_at", "event_id"])
            .returning(Event.occurred_at, Event.event_id)
        )
        returned = (await session.execute(statement)).all()
        inserted += len(returned)
        inserted_keys.extend((row.occurred_at, row.event_id) for row in returned)

    # Артефакт мог быть загружен раньше события: тогда загрузка уже прошла
    # и ставить в очередь некому, кроме приёма события.
    with_artifact = {
        (row["occurred_at"], row["event_id"]) for row in rows if row["artifact_sha256"]
    }
    await enqueue_new_events(session, [key for key in inserted_keys if key in with_artifact])
    return inserted
```

(оставить сигнатуру и докстринг функции.)

- [ ] **Step 5: Подключить постановку в `_finalize`**

В `server/barysguard/services/artifacts.py`: импорт `from barysguard.services.inspection.queue import enqueue_for_artifact`. В `_finalize` в ветке `if known is None:` после `session.add(Artifact(...))` ничего не менять; после строки `await session.flush()` (перед `return ChunkResult(size, "complete", leftover=path)`) добавить:

```python
    if known is None:
        # Новый артефакт: события, пришедшие раньше содержимого, ждали именно этого.
        await enqueue_for_artifact(session, digest)
```

(`known` определена выше в функции; `digest` — хеш.)

- [ ] **Step 6: Добавить интеграционные тесты постановки через реальные эндпоинты**

В `server/tests/test_inspection_queue.py` дописать (используя паттерн `artifact_env`, `_open`, `_put` из `tests/test_artifact_upload.py`; скопировать фикстуру `artifact_env` и вспомогательные функции `_sha`, `_open`, `_put` целиком в этот файл, чтобы не импортировать приватные имена):

```python
import base64
import hashlib
import json
from pathlib import Path

import pytest

from barysguard.core.config import get_settings

NDJSON = {"Content-Type": "application/x-ndjson"}


@pytest.fixture
def artifact_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(root))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    get_settings.cache_clear()
    return root


def _file_event(sha: str, size: int) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "channel": "file",
        "action": "copy",
        "subject": {"dst_path": "E:\\a.txt", "volume": {"type": "removable"}},
        "artifact": {"sha256": sha, "size": size, "uploaded": False},
    }


async def _send(client, agent, event: dict) -> None:
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


async def test_event_before_the_artifact_is_queued_when_the_upload_completes(
    app_client, session, artifact_env
) -> None:
    agent = await enroll_agent(app_client, session, "q-order-1")
    data = b"first the event, then the bytes"
    sha = hashlib.sha256(data).hexdigest()

    await _send(app_client, agent, _file_event(sha, len(data)))
    assert await _queued(session) == []  # содержимого ещё нет

    await _upload(app_client, agent, data)

    assert [r.artifact_sha256 for r in await _queued(session)] == [sha]


async def test_event_after_the_artifact_is_queued_on_arrival(
    app_client, session, artifact_env
) -> None:
    agent = await enroll_agent(app_client, session, "q-order-2")
    data = b"first the bytes, then the event"
    sha = hashlib.sha256(data).hexdigest()

    await _upload(app_client, agent, data)
    assert await _queued(session) == []  # событий ещё нет

    await _send(app_client, agent, _file_event(sha, len(data)))
    await _send(app_client, agent, _file_event(sha, len(data)))

    assert len(await _queued(session)) == 2
```

- [ ] **Step 7: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_queue.py tests/test_events_ingest.py tests/test_artifact_upload.py -q` → PASS (существующие тесты загрузки и приёма событий не должны сломаться). Затем весь набор, `ruff check .`, `ruff format --check .`, `mypy barysguard`.

Если `claim_batch`/`values(...).data(...)` падает на типах параметров asyncpg — привести ключи к `datetime` с tzinfo (они уже такие) и проверить, что `Uuid()` импортирован из `sqlalchemy`.

- [ ] **Step 8: Commit**

```bash
git add server/barysguard server/tests
git commit -m "feat(server): inspection queue with enqueue points and SKIP LOCKED claiming

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Вердикты и инциденты

**Files:**
- Create: `server/barysguard/services/inspection/verdicts.py`, `server/barysguard/services/inspection/incidents.py`, `server/tests/test_inspection_incidents.py`

**Interfaces:**
- Consumes: модели; `VerdictResult` из `scoring.py`.
- Produces (`verdicts.py`): `write_verdict(session, *, occurred_at: datetime, event_id: uuid.UUID, scan_id: uuid.UUID | None, status: str, reason: str | None, score: int, severity: str, matches: list[dict[str, Any]]) -> Verdict` — вставляет вердикт и проставляет `events.verdict_id`.
- Produces (`incidents.py`): `SEVERITY_ORDER`, `build_title(action: str, matches: list[dict[str, Any]]) -> str`, `apply_verdict(session, event: Event, verdict: Verdict) -> Incident`.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_inspection_incidents.py`:

```python
"""Запись вердиктов и группировка инцидентов."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident, IncidentEvent, Verdict
from barysguard.services.inspection.incidents import apply_verdict, build_title
from barysguard.services.inspection.verdicts import write_verdict
from tests.helpers import enroll_agent

SHA = "ab" * 32
MATCHES = [
    {"rule_key": "iin_bin", "rule_version_id": "v1", "count": 3, "points": 60, "samples": ["**********17"]},
    {"rule_key": "markings", "rule_version_id": "v3", "count": 1, "points": 15, "samples": ["конфиденциально"]},
]


async def _event(session, agent_id, *, user: str | None = "PC\\ivanov", sha: str = SHA, at=None) -> Event:
    event = Event(
        occurred_at=at or datetime.now(UTC),
        event_id=uuid.uuid4(),
        agent_id=agent_id,
        schema_version=1,
        channel="file",
        action="copy",
        actor={"user_name": user} if user else {},
        artifact_sha256=sha,
    )
    session.add(event)
    await session.flush()
    return event


async def _verdict(session, event: Event, *, score: int = 75, severity: str = "high") -> Verdict:
    return await write_verdict(
        session,
        occurred_at=event.occurred_at,
        event_id=event.event_id,
        scan_id=None,
        status="flagged",
        reason=None,
        score=score,
        severity=severity,
        matches=MATCHES,
    )


def test_title_names_the_action_and_the_rules() -> None:
    assert build_title("copy", MATCHES) == "Копирование на USB: ИИН/БИН ×3, гриф ×1"
    assert build_title("scan", MATCHES).startswith("Файловое событие:")


async def test_write_verdict_links_the_event(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "v-link")
    event = await _event(session, agent.agent_id)

    verdict = await _verdict(session, event)
    await session.refresh(event)

    assert event.verdict_id == verdict.id
    assert (verdict.status, verdict.score, verdict.severity) == ("flagged", 75, "high")


async def test_first_flagged_event_opens_an_incident(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-open")
    event = await _event(session, agent.agent_id)
    verdict = await _verdict(session, event)

    incident = await apply_verdict(session, event, verdict)

    assert incident.status == "open" and incident.events_count == 1
    assert (incident.score, incident.severity) == (75, "high")
    assert incident.title == "Копирование на USB: ИИН/БИН ×3, гриф ×1"
    assert incident.group_key == f"{agent.agent_id}|PC\\ivanov|{SHA}"
    links = (await session.scalars(select(IncidentEvent))).all()
    assert [(l.incident_id, l.event_id) for l in links] == [(incident.id, event.event_id)]


async def test_repeat_copies_from_one_machine_share_an_incident(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-same")
    base = datetime.now(UTC)
    first = await _event(session, agent.agent_id, at=base)
    second = await _event(session, agent.agent_id, at=base + timedelta(minutes=5))

    one = await apply_verdict(session, first, await _verdict(session, first, score=40, severity="medium"))
    two = await apply_verdict(session, second, await _verdict(session, second, score=75, severity="high"))
    await session.flush()

    assert one.id == two.id
    assert (await session.scalars(select(Incident))).all().__len__() == 1
    assert two.events_count == 2
    assert (two.score, two.severity) == (75, "high")  # максимум
    assert two.first_event_at == base and two.last_event_at == base + timedelta(minutes=5)


async def test_a_lower_score_does_not_lower_the_incident(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-max")
    first = await _event(session, agent.agent_id)
    second = await _event(session, agent.agent_id)
    await apply_verdict(session, first, await _verdict(session, first, score=90, severity="critical"))

    incident = await apply_verdict(
        session, second, await _verdict(session, second, score=30, severity="medium")
    )

    assert (incident.score, incident.severity) == (90, "critical")


async def test_other_user_machine_or_document_gets_its_own_incident(app_client, session) -> None:
    one = await enroll_agent(app_client, session, "i-m1")
    two = await enroll_agent(app_client, session, "i-m2")
    cases = [
        await _event(session, one.agent_id),
        await _event(session, one.agent_id, user="PC\\petrov"),
        await _event(session, one.agent_id, sha="cd" * 32),
        await _event(session, one.agent_id, user=None),
        await _event(session, two.agent_id),
    ]

    for event in cases:
        await apply_verdict(session, event, await _verdict(session, event))
    await session.flush()

    assert len((await session.scalars(select(Incident))).all()) == 5
    unknown = (await session.scalars(select(Incident).where(Incident.group_key.like("%|unknown|%")))).all()
    assert len(unknown) == 1


async def test_a_closed_incident_is_not_reopened(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-closed")
    first = await _event(session, agent.agent_id)
    second = await _event(session, agent.agent_id)
    incident = await apply_verdict(session, first, await _verdict(session, first))
    incident.status = "closed"
    await session.flush()

    fresh = await apply_verdict(session, second, await _verdict(session, second))

    assert fresh.id != incident.id and fresh.status == "open"


async def test_the_same_event_is_not_counted_twice(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "i-dup")
    event = await _event(session, agent.agent_id)
    verdict = await _verdict(session, event)

    await apply_verdict(session, event, verdict)
    incident = await apply_verdict(session, event, verdict)

    assert incident.events_count == 1
```

- [ ] **Step 2: Запустить — упасть** (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать вердикты**

`server/barysguard/services/inspection/verdicts.py`:

```python
"""Запись вердикта и привязка его к событию."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Verdict


async def write_verdict(
    session: AsyncSession,
    *,
    occurred_at: datetime,
    event_id: uuid.UUID,
    scan_id: uuid.UUID | None,
    status: str,
    reason: str | None,
    score: int,
    severity: str,
    matches: list[dict[str, Any]],
) -> Verdict:
    """Вердикт события. Задача удерживается одним воркером, поэтому гонки здесь нет."""
    verdict = Verdict(
        event_occurred_at=occurred_at,
        event_id=event_id,
        scan_id=scan_id,
        status=status,
        reason=reason,
        score=score,
        severity=severity,
        matches=matches,
    )
    session.add(verdict)
    await session.flush()
    await session.execute(
        update(Event)
        .where(Event.occurred_at == occurred_at, Event.event_id == event_id)
        .values(verdict_id=verdict.id)
    )
    return verdict
```

- [ ] **Step 4: Реализовать инциденты**

`server/barysguard/services/inspection/incidents.py`:

```python
"""Создание и дополнение инцидентов по вердиктам."""

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident, IncidentEvent, Verdict

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]

_RULE_LABELS = {"iin_bin": "ИИН/БИН", "card": "карта", "markings": "гриф"}
_ACTION_TITLES = {
    "copy": "Копирование на USB",
    "create": "Запись на USB",
    "modify": "Изменение на USB",
}


def build_title(action: str, matches: list[dict[str, Any]]) -> str:
    head = _ACTION_TITLES.get(action, "Файловое событие")
    parts = ", ".join(
        f"{_RULE_LABELS.get(m['rule_key'], m['rule_key'])} ×{m['count']}" for m in matches
    )
    return f"{head}: {parts}"


def _group_key(event: Event) -> str:
    actor = (event.actor or {}).get("user_name") or "unknown"
    return f"{event.agent_id}|{actor}|{event.artifact_sha256}"


async def _open_incident(session: AsyncSession, key: str) -> Incident | None:
    return (
        await session.scalars(
            select(Incident)
            .where(Incident.group_key == key, Incident.status != "closed")
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def apply_verdict(session: AsyncSession, event: Event, verdict: Verdict) -> Incident:
    """Дополняет открытый инцидент группы или заводит новый; возвращает инцидент."""
    key = _group_key(event)
    title = build_title(event.action, verdict.matches)

    incident = await _open_incident(session, key)
    if incident is None:
        await session.execute(
            pg_insert(Incident)
            .values(
                id=uuid.uuid4(),
                group_key=key,
                agent_id=event.agent_id,
                artifact_sha256=event.artifact_sha256,
                title=title,
                severity=verdict.severity,
                score=verdict.score,
                status="open",
                first_event_at=event.occurred_at,
                last_event_at=event.occurred_at,
                events_count=0,
            )
            .on_conflict_do_nothing(index_elements=["group_key"], index_where=text("status <> 'closed'"))
        )
        # Если вставку опередил другой воркер, блокировка возьмёт его строку.
        incident = await _open_incident(session, key)
        assert incident is not None

    linked = (
        await session.execute(
            pg_insert(IncidentEvent)
            .values(
                incident_id=incident.id,
                event_occurred_at=event.occurred_at,
                event_id=event.event_id,
            )
            .on_conflict_do_nothing()
            .returning(IncidentEvent.event_id)
        )
    ).scalar_one_or_none()

    if linked is not None:
        incident.events_count += 1
        incident.first_event_at = min(incident.first_event_at, event.occurred_at)
        incident.last_event_at = max(incident.last_event_at, event.occurred_at)
        if verdict.score > incident.score:
            incident.score = verdict.score
            incident.title = title
        if SEVERITY_ORDER.index(verdict.severity) > SEVERITY_ORDER.index(incident.severity):
            incident.severity = verdict.severity
        await session.flush()
    return incident
```

- [ ] **Step 5: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_incidents.py -q` → PASS. Тест `test_repeat_copies...` использует `.all().__len__()` — заменить на `len((await session.scalars(select(Incident))).all()) == 1` при форматировании, если ruff/mypy заметят. Затем `ruff check .`, `ruff format --check .`, `mypy barysguard`.

- [ ] **Step 6: Commit**

```bash
git add server/barysguard/services/inspection server/tests/test_inspection_incidents.py
git commit -m "feat(server): verdict recording and incident grouping

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Воркер

**Files:**
- Create: `server/barysguard/services/inspection/worker.py`, `server/tests/test_inspection_worker.py`
- Modify: `server/barysguard/cli.py`

**Interfaces:**
- Consumes: всё из Tasks 2–6.
- Produces (`worker.py`): `WorkerConfigError`, `process_task(session, store, ruleset, task, settings) -> None`, `run_once(maker, store, settings, *, now: datetime | None = None) -> int` (число обработанных задач), `run_worker(settings, stop: asyncio.Event) -> None`. Команда `barysguard-admin worker`.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_inspection_worker.py`:

```python
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


async def _prepare(session, store, tmp_path, app_client, data: bytes, name: str, *, events: int = 1):
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
    assert (await session.get(Artifact, (await session.scalars(select(Artifact.id))).one())).scan_status == "scanned"


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
```

- [ ] **Step 2: Запустить — упасть** (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать воркер**

`server/barysguard/services/inspection/worker.py`:

```python
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
            session, limit=settings.worker_batch, lock_seconds=settings.worker_lock_seconds, now=moment
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
                    extra={"task_id": task.id, "sha256": task.artifact_sha256, "attempt": task.attempts},
                )
    return len(tasks)


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    store = build_store(settings)
    if store is None:
        raise WorkerConfigError("Не задан BG_ARTIFACT_MASTER_KEY: воркер не может читать содержимое.")

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
```

- [ ] **Step 4: Команда `worker` в CLI**

В `server/barysguard/cli.py`: вверху `import logging`, `import signal`. Добавить:

```python
async def _worker() -> int:
    from barysguard.services.inspection.worker import WorkerConfigError, run_worker

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:  # Windows: обработчики сигналов цикла недоступны
            pass
    try:
        await run_worker(settings, stop)
    except WorkerConfigError as exc:
        print(str(exc))
        return 1
    return 0
```

В `main()`: `sub.add_parser("worker", help="воркер инспекции содержимого (очередь, правила, вердикты)")` и

```python
    if args.command == "worker":
        try:
            raise SystemExit(asyncio.run(_worker()))
        except KeyboardInterrupt:
            raise SystemExit(0) from None
```

- [ ] **Step 5: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_worker.py -q` → PASS. Замечания:
- `test_a_failing_task_is_retried...`: `run_once` вызывается с `now`, смещённым так, чтобы блокировка (1 с) истекала; три попытки падают из-за отсутствия файла (`FileNotFoundError` из `store.open` — это infra-ошибка, задача остаётся); четвёртый вызов: `fail_exhausted` помечает задачу `failed` и пишет вердикт. Если `store.delete` не вызывает падения, потому что `decrypt_stream` читает лениво, — ошибка всё равно возникает при `b"".join(...)`.
- Проверка `scan_status == "scanned"` в первом тесте написана через `session.get` по id единственного артефакта — при замечании линтера по длине строки разбить на две строки.
- Если `session.refresh(event)` не видит `verdict_id` из-за кеша сессии теста — перед чтением вызывать `await session.rollback()` или `expire_all()`.

Затем весь набор, `ruff check .`, `ruff format --check .`, `mypy barysguard`.

- [ ] **Step 6: Commit**

```bash
git add server/barysguard server/tests
git commit -m "feat(server): inspection worker with scan reuse, verdicts and retries

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: API инцидентов, поле verdict в событиях, контракт

**Files:**
- Create: `server/barysguard/api/incidents.py`, `server/tests/test_incidents_api.py`
- Modify: `server/barysguard/api/schemas.py`, `server/barysguard/api/events.py`, `server/barysguard/main.py`, `api/gateway-v1.yaml`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: модели; `scope_group_ids`; `record_audit`; `_encode_cursor`/`_decode_cursor` из `barysguard.api.events`.
- Produces: схемы `VerdictSummary`, `IncidentSummary`, `IncidentPage`, `IncidentMatch`, `IncidentEventRef`, `IncidentDetail`, `IncidentStatusUpdate`; маршруты `GET /api/v1/incidents`, `GET /api/v1/incidents/{id}`, `PATCH /api/v1/incidents/{id}`; поле `EventSummary.verdict: VerdictSummary | None`.

- [ ] **Step 1: Падающие тесты**

`server/tests/test_incidents_api.py`:

```python
"""GET/PATCH /api/v1/incidents и поле verdict в /api/v1/events."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident, IncidentEvent
from barysguard.db.models.user import UserRole
from barysguard.services.inspection.incidents import apply_verdict
from barysguard.services.inspection.verdicts import write_verdict
from tests.helpers import enroll_agent, login_as

SHA = "ab" * 32
MATCHES = [
    {"rule_key": "iin_bin", "rule_version_id": "v1", "count": 2, "points": 40, "samples": ["**********17"]}
]


async def _flagged(session, agent_id, *, user="PC\\ivanov", at=None, score=75, severity="high") -> Incident:
    event = Event(
        occurred_at=at or datetime.now(UTC),
        event_id=uuid.uuid4(),
        agent_id=agent_id,
        schema_version=1,
        channel="file",
        action="copy",
        actor={"user_name": user},
        subject={"dst_path": "E:\\salary.xlsx"},
        artifact_sha256=SHA,
    )
    session.add(event)
    await session.flush()
    verdict = await write_verdict(
        session,
        occurred_at=event.occurred_at,
        event_id=event.event_id,
        scan_id=None,
        status="flagged",
        reason=None,
        score=score,
        severity=severity,
        matches=MATCHES,
    )
    incident = await apply_verdict(session, event, verdict)
    await session.commit()
    return incident


async def test_incidents_are_listed_newest_first_with_hostname(app_client, session) -> None:
    await login_as(app_client, session, username="inc-admin", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-list")
    now = datetime.now(UTC)
    older = await _flagged(session, agent.agent_id, user="PC\\a", at=now - timedelta(hours=2))
    newer = await _flagged(session, agent.agent_id, user="PC\\b", at=now - timedelta(hours=1))

    response = await app_client.get("/api/v1/incidents")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [i["id"] for i in items] == [str(newer.id), str(older.id)]
    assert items[0]["hostname"] == "ws-1"
    assert items[0]["status"] == "open" and items[0]["severity"] == "high"
    assert response.json()["next_cursor"] is None


async def test_filters_and_pagination(app_client, session) -> None:
    await login_as(app_client, session, username="inc-filter", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-filter")
    now = datetime.now(UTC)
    for index in range(3):
        await _flagged(
            session,
            agent.agent_id,
            user=f"PC\\u{index}",
            at=now - timedelta(minutes=index),
            severity="critical" if index == 0 else "medium",
            score=90 if index == 0 else 30,
        )

    critical = await app_client.get("/api/v1/incidents", params={"severity": "critical"})
    page = await app_client.get("/api/v1/incidents", params={"limit": 2})
    rest = await app_client.get(
        "/api/v1/incidents", params={"limit": 2, "cursor": page.json()["next_cursor"]}
    )

    assert len(critical.json()["items"]) == 1
    assert len(page.json()["items"]) == 2 and page.json()["next_cursor"]
    assert len(rest.json()["items"]) == 1 and rest.json()["next_cursor"] is None
    other = await app_client.get("/api/v1/incidents", params={"status": "closed"})
    assert other.json()["items"] == []


async def test_detail_has_verdict_matches_and_events_without_full_values(app_client, session) -> None:
    await login_as(app_client, session, username="inc-detail", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-detail")
    incident = await _flagged(session, agent.agent_id)

    response = await app_client.get(f"/api/v1/incidents/{incident.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == {"status": "flagged", "score": 75, "severity": "high"}
    assert body["matches"] == [
        {"rule_key": "iin_bin", "count": 2, "points": 40, "samples": ["**********17"]}
    ]
    assert len(body["events"]) == 1
    assert body["events"][0]["dst_path"] == "E:\\salary.xlsx"
    assert "900101300017" not in response.text


async def test_unknown_incident_is_404(app_client, session) -> None:
    await login_as(app_client, session, username="inc-404", role=UserRole.ADMIN)

    response = await app_client.get(f"/api/v1/incidents/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_operator_sees_only_incidents_of_his_scope(app_client, session) -> None:
    mine = AgentGroup(name="mine")
    foreign = AgentGroup(name="foreign")
    session.add_all([mine, foreign])
    await session.commit()
    await login_as(app_client, session, username="inc-scoped", scope_group_id=mine.id)
    visible = await enroll_agent(app_client, session, "api-vis", group_id=mine.id)
    hidden = await enroll_agent(app_client, session, "api-hid", group_id=foreign.id)
    own = await _flagged(session, visible.agent_id)
    other = await _flagged(session, hidden.agent_id)

    listing = await app_client.get("/api/v1/incidents")
    detail = await app_client.get(f"/api/v1/incidents/{other.id}")
    patch = await app_client.patch(f"/api/v1/incidents/{other.id}", json={"status": "closed"})

    assert [i["id"] for i in listing.json()["items"]] == [str(own.id)]
    assert detail.status_code == 404 and patch.status_code == 404


async def test_status_changes_are_validated_and_audited(app_client, session) -> None:
    await login_as(app_client, session, username="inc-patch", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-patch")
    incident = await _flagged(session, agent.agent_id)
    url = f"/api/v1/incidents/{incident.id}"

    ack = await app_client.patch(url, json={"status": "acknowledged"})
    again = await app_client.patch(url, json={"status": "acknowledged"})
    closed = await app_client.patch(url, json={"status": "closed"})
    reopen = await app_client.patch(url, json={"status": "acknowledged"})
    junk = await app_client.patch(url, json={"status": "open"})

    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
    assert again.status_code == 200
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert reopen.status_code == 409
    assert junk.status_code == 422
    actions = (await session.scalars(select(AuditLog.action))).all()
    assert list(actions).count("incident.update") == 2  # ack и close; повтор не пишется
    await session.refresh(incident)
    assert incident.closed_at is not None


async def test_events_carry_the_verdict(app_client, session) -> None:
    await login_as(app_client, session, username="inc-ev", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "api-ev")
    await _flagged(session, agent.agent_id)
    session.add(
        Event(
            occurred_at=datetime.now(UTC) - timedelta(days=1),
            event_id=uuid.uuid4(),
            agent_id=agent.agent_id,
            schema_version=1,
            channel="agent",
            action="start",
        )
    )
    await session.commit()

    items = (await app_client.get("/api/v1/events")).json()["items"]

    by_action = {item["action"]: item for item in items}
    assert by_action["copy"]["verdict"] == {"status": "flagged", "score": 75, "severity": "high"}
    assert by_action["start"]["verdict"] is None
```

- [ ] **Step 2: Запустить — упасть** (404 на маршруты / отсутствует поле).

- [ ] **Step 3: Схемы**

В `server/barysguard/api/schemas.py`: проверить импорты (`Literal` из `typing`, `uuid`, `datetime`, `Any`, `BaseModel` уже есть выше по файлу — при отсутствии `Literal` добавить `from typing import Any, Literal`). Добавить рядом с `EventSummary` ДО неё класс и изменить `EventSummary`:

```python
class VerdictSummary(BaseModel):
    status: str
    score: int
    severity: str
```

В `EventSummary` добавить последним полем:

```python
    verdict: VerdictSummary | None = None
```

В конец файла добавить:

```python
class IncidentSummary(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    hostname: str
    artifact_sha256: str
    title: str
    severity: str
    score: int
    status: str
    events_count: int
    first_event_at: datetime
    last_event_at: datetime
    assignee: uuid.UUID | None = None


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    next_cursor: str | None


class IncidentMatch(BaseModel):
    rule_key: str
    count: int
    points: int
    samples: list[str]


class IncidentEventRef(BaseModel):
    event_id: uuid.UUID
    occurred_at: datetime
    action: str
    severity: str
    dst_path: str | None = None


class IncidentDetail(IncidentSummary):
    verdict: VerdictSummary | None = None
    matches: list[IncidentMatch]
    events: list[IncidentEventRef]


class IncidentStatusUpdate(BaseModel):
    status: Literal["acknowledged", "closed"]
```

- [ ] **Step 4: Поле verdict в `list_events`**

В `server/barysguard/api/events.py`: импорты `from barysguard.api.schemas import EventPage, EventSummary, VerdictSummary`, `from barysguard.db.models.inspection import Verdict`. Заменить запрос и сборку:

```python
    rows = (
        await session.execute(
            select(Event, Agent.hostname, uploaded, Verdict.status, Verdict.score, Verdict.severity)
            .join(Agent, Agent.id == Event.agent_id)
            .outerjoin(Verdict, Verdict.id == Event.verdict_id)
            .where(*conditions)
            .order_by(Event.occurred_at.desc(), Event.event_id.desc())
            .limit(limit + 1)
        )
    ).all()
```

и в конструкторе `EventSummary(...)` добавить `verdict=VerdictSummary(status=v_status, score=v_score, severity=v_severity) if v_status is not None else None`, а цикл — `for event, hostname, is_uploaded, v_status, v_score, v_severity in page`.

- [ ] **Step 5: Маршруты**

`server/barysguard/api/incidents.py`:

```python
"""Инциденты DLP: чтение и смена статуса оператором."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import current_user
from barysguard.api.events import _decode_cursor, _encode_cursor  # общий формат курсора
from barysguard.api.schemas import (
    IncidentDetail,
    IncidentEventRef,
    IncidentMatch,
    IncidentPage,
    IncidentStatusUpdate,
    IncidentSummary,
    VerdictSummary,
)
from barysguard.db.models.agent import Agent
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident, IncidentEvent, Verdict
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.scope import scope_group_ids

router = APIRouter(prefix="/api/v1", tags=["api"])


def _summary(incident: Incident, hostname: str) -> IncidentSummary:
    return IncidentSummary(
        id=incident.id,
        agent_id=incident.agent_id,
        hostname=hostname,
        artifact_sha256=incident.artifact_sha256,
        title=incident.title,
        severity=incident.severity,
        score=incident.score,
        status=incident.status,
        events_count=incident.events_count,
        first_event_at=incident.first_event_at,
        last_event_at=incident.last_event_at,
        assignee=incident.assignee,
    )


@router.get("/incidents", response_model=IncidentPage)
async def list_incidents(
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    agent_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentPage:
    """Инциденты от свежих к старым. Оператор видит только свою область."""
    conditions: list[ColumnElement[bool]] = []

    visible = await scope_group_ids(session, user)
    if visible is not None:
        conditions.append(Agent.group_id.in_(visible))

    if status_filter:
        conditions.append(Incident.status == status_filter)
    if severity:
        conditions.append(Incident.severity == severity)
    if agent_id is not None:
        conditions.append(Incident.agent_id == agent_id)
    if cursor:
        at, identifier = _decode_cursor(cursor)
        conditions.append(
            or_(
                Incident.last_event_at < at,
                and_(Incident.last_event_at == at, Incident.id < identifier),
            )
        )

    rows = (
        await session.execute(
            select(Incident, Agent.hostname)
            .join(Agent, Agent.id == Incident.agent_id)
            .where(*conditions)
            .order_by(Incident.last_event_at.desc(), Incident.id.desc())
            .limit(limit + 1)
        )
    ).all()

    page = rows[:limit]
    next_cursor = (
        _encode_cursor(page[-1][0].last_event_at, page[-1][0].id) if len(rows) > limit else None
    )
    return IncidentPage(
        items=[_summary(incident, hostname) for incident, hostname in page],
        next_cursor=next_cursor,
    )


async def _visible_incident(
    session: AsyncSession, user: User, incident_id: uuid.UUID
) -> tuple[Incident, str]:
    conditions: list[ColumnElement[bool]] = [Incident.id == incident_id]
    visible = await scope_group_ids(session, user)
    if visible is not None:
        conditions.append(Agent.group_id.in_(visible))
    row = (
        await session.execute(
            select(Incident, Agent.hostname)
            .join(Agent, Agent.id == Incident.agent_id)
            .where(*conditions)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return row[0], row[1]


def _linked(incident_id: uuid.UUID) -> ColumnElement[bool]:
    return and_(
        IncidentEvent.incident_id == incident_id,
        IncidentEvent.event_occurred_at == Event.occurred_at,
        IncidentEvent.event_id == Event.event_id,
    )


async def _detail(session: AsyncSession, incident: Incident, hostname: str) -> IncidentDetail:
    best = (
        await session.execute(
            select(Verdict)
            .join(
                IncidentEvent,
                and_(
                    IncidentEvent.event_occurred_at == Verdict.event_occurred_at,
                    IncidentEvent.event_id == Verdict.event_id,
                ),
            )
            .where(IncidentEvent.incident_id == incident.id)
            .order_by(Verdict.score.desc(), Verdict.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    events = (
        (
            await session.execute(
                select(Event)
                .join(IncidentEvent, _linked(incident.id))
                .order_by(Event.occurred_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return IncidentDetail(
        **_summary(incident, hostname).model_dump(),
        verdict=(
            VerdictSummary(status=best.status, score=best.score, severity=best.severity)
            if best
            else None
        ),
        matches=[
            IncidentMatch(
                rule_key=m["rule_key"],
                count=m["count"],
                points=m["points"],
                samples=m.get("samples", []),
            )
            for m in (best.matches if best else [])
        ],
        events=[
            IncidentEventRef(
                event_id=event.event_id,
                occurred_at=event.occurred_at,
                action=event.action,
                severity=event.severity,
                dst_path=(event.subject or {}).get("dst_path"),
            )
            for event in events
        ],
    )


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def read_incident(
    incident_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    incident, hostname = await _visible_incident(session, user, incident_id)
    return await _detail(session, incident, hostname)


@router.patch("/incidents/{incident_id}", response_model=IncidentDetail)
async def update_incident(
    incident_id: uuid.UUID,
    payload: IncidentStatusUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    incident, hostname = await _visible_incident(session, user, incident_id)

    if incident.status != payload.status:
        if incident.status == "closed":
            raise HTTPException(status.HTTP_409_CONFLICT, "incident is closed")
        previous = incident.status
        incident.status = payload.status
        if payload.status == "closed":
            incident.closed_at = datetime.now(UTC)
        await session.flush()
        await record_audit(
            session,
            user_id=user.id,
            action="incident.update",
            target_type="incident",
            target_id=incident.id,
            payload={"from": previous, "to": payload.status},
        )

    return await _detail(session, incident, hostname)
```

Зарегистрировать маршрутизатор в `server/barysguard/main.py`: рядом с импортами `from barysguard.api.incidents import router as incidents_router` (в алфавитном порядке между `groups` и `overview`) и `app.include_router(incidents_router)` после `app.include_router(events_router)`.

- [ ] **Step 6: Запустить тесты API**

Run: `.venv/Scripts/python -m pytest tests/test_incidents_api.py tests/test_console_events_api.py -q` → PASS. Если `Incident.id < identifier` падает на сравнении UUID — это штатно для PostgreSQL `uuid`; если `_decode_cursor` возвращает не UUID (нет) — проверить.

- [ ] **Step 7: Контракт и типы консоли**

Регенерировать контракт (из `server/`):

```bash
.venv/Scripts/python -c "import yaml; from barysguard.main import create_app; open('../api/gateway-v1.yaml', 'w', encoding='utf-8', newline='\n').write(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True))"
```

Затем `.venv/Scripts/python -m pytest tests/test_openapi_contract.py -q` → PASS. Из `web/`: `npm run types`, `npm run typecheck`, `npx vitest run` → зелёные (поле `verdict` в `EventSummary` необязательное, существующие фикстуры не ломаются; если `typecheck` ругается на строгие типы в фикстурах — добавить `verdict: null` в соответствующие фикстуры `web/src/**/*.test.*`).

- [ ] **Step 8: Полный прогон, проверки, commit**

Run: весь `pytest`, `ruff check .`, `ruff format --check .`, `mypy barysguard`.

```bash
git add server api web
git commit -m "feat: incidents API and verdict in the events list

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Стенд, демо-артефакты, сквозной тест, документация

**Files:**
- Create: `server/barysguard/services/demo_artifacts.py`, `server/tests/test_demo_artifacts.py`, `server/tests/test_inspection_e2e.py`, `docs/DLP_WORKER.md`
- Modify: `server/barysguard/cli.py`, `deploy/stand/docker-compose.yml`, `scripts/smoke.ps1`, `docs/QUICKSTART.md`, `docs/COLLECTORS_MANUAL.md`

**Interfaces:**
- Produces: `seed_demo_artifacts(session, store) -> DemoArtifactsResult(agents: int, inserted: int)`; `seed-demo-events` дополнительно заводит демо-артефакты; сервис `worker` в стенде.

- [ ] **Step 1: Падающий сквозной тест через протокол загрузки**

`server/tests/test_inspection_e2e.py`:

```python
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
SENSITIVE = "Зарплатная ведомость. Строго конфиденциально. ИИН 900101300017, карта 4111 1111 1111 1111".encode()
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
        "artifact": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "uploaded": False},
    }
    response = await client.post(
        "/gateway/v1/events", headers={**agent.headers, **NDJSON}, content=(json.dumps(event) + "\n").encode()
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
        for item in (await app_client.get("/api/v1/events", params={"channel": "file"})).json()["items"]
    }
    assert verdicts["E:\\salary.txt"]["status"] == "flagged"
    assert verdicts["E:\\salary-copy.txt"]["status"] == "flagged"
    assert verdicts["E:\\menu.txt"] == {"status": "clean", "score": 0, "severity": "info"}

    # Одно содержимое — один скан, сколько бы раз его ни копировали.
    scans = (await session.scalars(select(ArtifactScan))).all()
    assert len(scans) == 2
```

- [ ] **Step 2: Запустить**

Run: `.venv/Scripts/python -m pytest tests/test_inspection_e2e.py -q`
Expected: PASS (все части уже реализованы Tasks 1–8). Если падает — это найденный дефект интеграции: разобрать по `superpowers:systematic-debugging`, исправить в соответствующем модуле и добавить регрессионный тест в тест этого модуля.

- [ ] **Step 3: Падающий тест демо-артефактов**

`server/tests/test_demo_artifacts.py`:

```python
"""Демо-артефакты стенда: чувствительный и чистый файл на каждого агента."""

import base64
from pathlib import Path

import pytest
from sqlalchemy import select

from barysguard.core.config import get_settings
from barysguard.db.models.artifact import Artifact
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import EventQueue
from barysguard.services.demo_artifacts import seed_demo_artifacts
from barysguard.storage.artifact_store import build_store
from tests.helpers import enroll_agent


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BG_ARTIFACT_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("BG_ARTIFACT_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    get_settings.cache_clear()
    return build_store(get_settings())


async def test_seed_stores_two_artifacts_and_queues_events_idempotently(
    app_client, session, store
) -> None:
    await enroll_agent(app_client, session, "demo-art-1")
    await enroll_agent(app_client, session, "demo-art-2")

    first = await seed_demo_artifacts(session, store)
    await session.commit()
    again = await seed_demo_artifacts(session, store)
    await session.commit()

    assert (first.agents, first.inserted) == (2, 4)
    assert again.inserted == 0
    assert len((await session.scalars(select(Artifact))).all()) == 2
    events = (await session.scalars(select(Event).where(Event.labels["demo_artifact"].astext == "true"))).all()
    assert len(events) == 4
    assert len((await session.scalars(select(EventQueue))).all()) == 4
```

- [ ] **Step 4: Реализовать демо-артефакты**

`server/barysguard/services/demo_artifacts.py`:

```python
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


async def seed_demo_artifacts(session: AsyncSession, store: FileArtifactStore) -> DemoArtifactsResult:
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
```

Подключить к `seed-demo-events` в `cli.py` (внутри `_seed_demo_events`, после `result = await seed_demo_events(session)` и до `await session.commit()`):

```python
            store = build_store(settings)
            artifacts = await seed_demo_artifacts(session, store) if store else None
```

и после `print(f"агентов: ...")` добавить:

```python
    if artifacts is None:
        print("демо-артефакты пропущены: не задан BG_ARTIFACT_MASTER_KEY")
    else:
        print(f"демо-артефакты: событий добавлено {artifacts.inserted}")
```

(импорты: `from barysguard.services.demo_artifacts import seed_demo_artifacts`, `from barysguard.storage.artifact_store import build_store`; `artifacts` объявить до `async with`: `artifacts = None`, чтобы переменная существовала вне блока.)

Run: `.venv/Scripts/python -m pytest tests/test_demo_artifacts.py tests/test_demo_events.py tests/test_cli.py -q` → PASS.

- [ ] **Step 5: Сервис worker в стенде и смоук**

В `deploy/stand/docker-compose.yml` после сервиса `server` (перед `nginx`) добавить:

```yaml
  worker:
    image: barysguard-server:stand
    pull_policy: never
    command: ["barysguard-admin", "worker"]
    environment: *server-environment
    volumes:
      - artifacts:/var/lib/barysguard/artifacts
    depends_on:
      bootstrap:
        condition: service_completed_successfully
    restart: unless-stopped
```

(`bootstrap` выполняет `alembic upgrade head && barysguard-admin bootstrap-dev`, а `bootstrap-dev` теперь вызывает `seed_rules`.)

В `scripts/smoke.ps1` после шага проверки событий (найти по `Assert-Step 'события` или аналогичному — это ближайший шаг, который запрашивает `/api/v1/events`) добавить шаг в том же стиле:

```powershell
Assert-Step 'список инцидентов отвечает (воркер инспекции)' {
    $r = Invoke-Api -Method GET -Url "$base/api/v1/incidents?limit=5" -Session $session
    Expect-Equal $r.Status 200 'GET /api/v1/incidents'
    if ($null -eq $r.Json.items) { throw 'в ответе нет поля items' }
}
```

(Содержимое инцидентов смоук не проверяет: оно появляется после `.\stand.cmd seed`.)

- [ ] **Step 6: Документация**

`docs/DLP_WORKER.md` (создать):

```markdown
# Воркер инспекции содержимого (DLP)

Воркер берёт файлы, загруженные агентами с внешних томов, достаёт из них текст, проверяет правилами и создаёт вердикты и инциденты.

## Как это работает

1. Агент копирует файл на флешку и присылает событие `file/copy`; содержимое загружается отдельно и хранится зашифрованным.
2. Когда есть и событие, и содержимое, событие ставится в очередь (`event_queue`).
3. Процесс `barysguard-admin worker` забирает задачи, расшифровывает файл в памяти, извлекает текст и ищет:
   - **ИИН/БИН** (12 цифр, проверяются дата и контрольный разряд);
   - **банковские карты** (13–19 цифр, проверка Луна и префикс сети: Visa, Mastercard, МИР, Amex);
   - **грифы** из словаря «Грифы» («конфиденциально», «для служебного пользования», «коммерческая тайна» и др.).
4. По находкам считается оценка: вклад правила = вес × min(число совпадений, потолок), итог не больше 100.

| Оценка | Вердикт | Критичность |
|---|---|---|
| 0–19 | `clean` | `info` |
| 20–49 | `flagged` | `medium` |
| 50–79 | `flagged` | `high` |
| 80–100 | `flagged` | `critical` |

Файл, который не удалось проверить (неподдерживаемый формат, архив, документ без текста, защищённый паролем, слишком большой, повреждённый), получает вердикт `not_inspected` с причиной. Он не теряется, но и инцидента не создаёт.

Инцидент создаётся, когда оценка не ниже `BG_INCIDENT_MIN_SCORE` (по умолчанию 20). Копии одного файла с одной машины одним пользователем собираются в один инцидент, пока он не закрыт.

В базе хранятся только счётчики и маски (ИИН/БИН — две последние цифры, карта — четыре последние), полные значения не сохраняются.

## Запуск

```bash
barysguard-admin seed-rules   # завести встроенные правила (идемпотентно)
barysguard-admin worker       # запустить воркер (нужны BG_DATABASE_URL, BG_ARTIFACT_PATH, BG_ARTIFACT_MASTER_KEY)
```

На стенде воркер — сервис `worker` в `docker-compose`, правила заводятся при старте. Чтобы увидеть инциденты на стенде, выполните `.\stand.cmd seed`: агентам добавятся события копирования двух демо-файлов (с ИИН и картой, и безобидный). Через несколько секунд:

```
GET /api/v1/incidents
```

## Настройки

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `BG_WORKER_BATCH` | 10 | задач за один захват |
| `BG_WORKER_POLL_SECONDS` | 2 | пауза при пустой очереди |
| `BG_WORKER_LOCK_SECONDS` | 300 | сколько воркер владеет задачей |
| `BG_WORKER_MAX_ATTEMPTS` | 3 | попыток до вердикта «ошибка» |
| `BG_INSPECT_MAX_TEXT_BYTES` | 20971520 | предел извлечённого текста |
| `BG_INSPECT_MAX_UNPACKED_BYTES` | 209715200 | предел распаковки контейнера Office |
| `BG_INSPECT_MAX_ENTRIES` | 10000 | предел элементов контейнера |
| `BG_INSPECT_MAX_RATIO` | 100 | предел степени сжатия (для элементов крупнее 1 МиБ) |
| `BG_INSPECT_TIMEOUT_SECONDS` | 30 | таймаут на файл |
| `BG_INCIDENT_MIN_SCORE` | 20 | порог создания инцидента |

## Словарь грифов

Термины лежат в таблице `dictionary_terms` (словарь `markings`). Регистр и `ё/е` не важны. После добавления термина выполните `barysguard-admin seed-rules`: создаётся новая версия правила, а новые файлы проверяются по обновлённому набору. Старые события заново не оцениваются.

## API

- `GET /api/v1/incidents` — фильтры `status`, `severity`, `agent_id`, курсор.
- `GET /api/v1/incidents/{id}` — вердикт, совпадения (счётчики и маскированные образцы), события.
- `PATCH /api/v1/incidents/{id}` — `{"status": "acknowledged" | "closed"}`, действие пишется в журнал аудита.
- `GET /api/v1/events` — у событий с содержимым есть поле `verdict`.

## Ограничения этой версии

Нет архивов, OCR, YARA, редактора правил и страницы инцидентов в консоли; вердикты старых событий при смене правил не пересчитываются; формат `.doc/.xls/.ppt` (OLE) не поддерживается.
```

В `docs/QUICKSTART.md` добавить (в раздел про переменные окружения и/или запуск; найти место командой `grep -n "BG_ARTIFACT" docs/QUICKSTART.md`) строки: переменные `BG_WORKER_*`, `BG_INSPECT_*`, `BG_INCIDENT_MIN_SCORE` ссылкой на `docs/DLP_WORKER.md`, и команду `barysguard-admin worker`. В `docs/COLLECTORS_MANUAL.md` в конце раздела «Загрузка файлов с флешки на сервер» добавить абзац: «Загруженное содержимое проверяет воркер инспекции и создаёт инциденты — см. `docs/DLP_WORKER.md`.» Также исправить в `docs/QUICKSTART.md` дрейф из бэклога, если он очевиден (`BG_ARTIFACT_CHUNK_BYTES` отсутствует) — добавить переменную с пояснением «размер чанка загрузки, по умолчанию 1 МиБ».

- [ ] **Step 7: Полная проверка**

Из `server/`: весь `pytest` (с `BG_TEST_DATABASE_URL`), `ruff check .`, `ruff format --check .`, `mypy barysguard`. Из `agent/`: `go test ./...` (не затронут, но прогнать). Из `web/`: `npx vitest run`, `npm run typecheck`.
Expected: серверных тестов около 335 + новые, всё зелёное.

Проверка на стенде (по возможности, если Docker запущен): из корня в cmd/PowerShell — `.\stand.cmd up`, `.\smoke.cmd` (все шаги ок), `.\stand.cmd seed`, затем через 10–20 секунд в консоли/через API `GET /api/v1/incidents` — инциденты по числу агентов (чувствительный файл) и ни одного по меню столовой. Если Docker не запущен, сообщить об этом в отчёте: проверка на стенде не выполнена.

- [ ] **Step 8: Обновить спеку, commit**

В `docs/superpowers/specs/2026-10-06-dlp-worker-design.md` строку «Статус» заменить на «реализовано (подпроект 3b / B2)». 

```bash
git add server docs deploy scripts
git commit -m "feat: stand worker service, demo artifacts, e2e test and DLP worker docs

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Раздел 3 (данные) → Task 1 (все таблицы, `verdict_id`, индекс `ix_events_artifact_sha256`, частичный уникальный индекс инцидентов).
- Раздел 4 (очередь, три точки, захват, повторы) → Task 5 (+ `fail_exhausted` и вердикт `error` в Task 7).
- Раздел 5 (воркер, цикл, остановка) → Task 7.
- Раздел 6 (извлечение, пределы) → Task 3.
- Раздел 7 (детекторы, оценка, пороги, `not_inspected`) → Task 2 (детекторы, оценка), Task 7 (`not_inspected`).
- Раздел 3.3 (версии правил, `seed-rules`) → Task 4.
- Раздел 8 (инциденты) → Task 6.
- Раздел 9 (настройки) → Task 1.
- Раздел 10 (API) → Task 8.
- Раздел 11 (стенд) → Task 9.
- Раздел 12 (безопасность) → тесты приватности (Task 3, 7, 8), память без диска (Task 3), аудит PATCH (Task 8).
- Разделы 13–14 (тесты, документация) → тесты в каждой задаче, `docs/DLP_WORKER.md`, QUICKSTART (Task 9).
- Отклонения от спеки, принятые при планировании, уже внесены в спеку: БИН (цифры 3–4 и 5-я), чтение в память целиком, страховка по свежим артефактам, индекс `ix_events_artifact_sha256`. Дополнительно: `error` в статусе скана (повреждённый файл, таймаут) — финальный исход без повторов; повторяются только сбои инфраструктуры (БД, чтение хранилища).

**Placeholder scan:** шаги содержат полный код; единственные «найти и заполнить по месту» — места правки `docs/QUICKSTART.md` и `scripts/smoke.ps1` (указано, как найти и что вставить) и тест CLI в `test_cli.py` (стиль — по соседним тестам).

**Type consistency:** `QueueTask` (Task 5) используется в Task 7 (`process_task`, `fail_exhausted`); `Ruleset.detectors()/weights()/version_ids()` (Task 4) — в Task 7; `ScanOutcome.findings` (Task 3) без `rule_version_id`, который добавляет воркер (Task 7), `evaluate` (Task 2) читает `count`/`samples`; `write_verdict(...)` одинаковые аргументы в Tasks 6–8; `apply_verdict(session, event, verdict)` одинаков в Tasks 6–8; `IncidentSummary.hostname` строится в `_summary` Task 8.
