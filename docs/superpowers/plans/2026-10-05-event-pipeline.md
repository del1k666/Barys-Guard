# Конвейер событий (подпроект 2a) — план реализации

> **Для исполнителя:** ОБЯЗАТЕЛЬНЫЙ поднавык — `superpowers:subagent-driven-development` (рекомендуется) или `superpowers:executing-plans`. Шаги отмечаются чекбоксами (`- [ ]`).

**Цель:** события агента доезжают до сервера через шифрованный offline-буфер, сохраняются в партиционированной таблице и читаются оператором через API.

**Архитектура:** агент пишет событие в локальный bbolt-буфер (AES-256-GCM) и только потом отправляет пакетами NDJSON на `POST /gateway/v1/events`; сервер валидирует каждую строку отдельно, идентичность берёт из сертификата и вставляет идемпотентно в таблицу `events`, партиционированную по месяцам. Оператор читает `GET /api/v1/events`. Сборщики подключаются через интерфейс `events.Collector`; в этом цикле единственный сборщик — жизненный цикл агента (канал `agent`).

**Стек:** Python 3.12, FastAPI, SQLAlchemy 2 (asyncpg), Alembic, PostgreSQL 16; Go 1.23, `go.etcd.io/bbolt` (MIT), стандартная библиотека.

**Спека:** `docs/superpowers/specs/2026-10-05-event-pipeline-design.md` (опора — разделы 9 и 10 и блок 2 схемы БД в `2026-09-08-dlp-transport-core-design.md`).

## Глобальные ограничения

- Python 3.12, `ruff` с `line-length = 100`, правила `E,F,I,N,UP,B,S,ASYNC`; новых зависимостей Python нет.
- Агент — отдельный модуль Go (`agent/go.mod`, директива `go 1.23`); единственная новая зависимость — `go.etcd.io/bbolt` версии `v1.3.11` (лицензия MIT).
- Комментарии и сообщения журнала — на русском, в стиле окружающего кода: объясняют **почему**, а не что.
- `agent_id` события берётся только из клиентского сертификата, из тела запроса — никогда.
- Размер одного события — не более 64 КиБ; пакет — не более `transport.event_batch_max` (500) событий и `transport.event_batch_max_bytes` (4 МиБ).
- `schema_version` = 1; каналы: `file | usb | clipboard | network | print | process | agent`; критичности: `info | low | medium | high | critical`.
- Буфер агента: AES-256-GCM, 500 МиБ и 7 суток по умолчанию, события `critical` не вытесняются и не удаляются по возрасту.
- Каждый коммит завершается строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (вторым аргументом `-m`).
- Серверные тесты идут на настоящем PostgreSQL через testcontainers (нужен запущенный Docker) либо `BG_TEST_DATABASE_URL`. Команды ниже выполняются из Git Bash в корне репозитория.

## Review Focus

Входы, о которых спека молчит, но которые встретятся в работе. Тест на каждый добавлен в задачу-владельца:

1. **Время события:** без часового пояса — отклоняется `invalid_event`; из 2001 года или далёкого будущего — принимается и попадает в `events_default`, а при создании раздела на этот месяц переезжает в него без потерь (задачи 1, 2).
2. **Формат NDJSON:** пустые строки и окончания `\r\n` не ломают разбор, номера строк в `rejected` остаются номерами строк тела (задача 2).
3. **Огромное событие:** 70 КБ в одной строке отклоняется `too_large`, соседние принимаются (задача 2).
4. **Буфер, забитый `critical`:** новые события отбрасываются с `ErrFull`, ни одно `critical` не теряется; событие низкой критичности не вытесняет более важные (задача 6).
5. **Повреждённая запись и потеря ключа буфера:** запись, не прошедшая проверку подлинности, удаляется и учитывается как потерянная, очередь не блокируется; пропажа файла ключа пересоздаёт буфер пустым и порождает `agent/buffer_reset` (задачи 6, 7).

## Карта файлов

Сервер:

| Файл | Ответственность |
|---|---|
| `server/barysguard/db/models/event.py` (новый) | ORM-модель `Event` |
| `server/alembic/versions/20261005_1200_events.py` (новый) | таблица `events`, `events_default`, индексы, первые партиции |
| `server/barysguard/services/event_partitions.py` (новый) | `ensure_event_partitions` |
| `server/barysguard/gateway/event_schemas.py` (новый) | `EventEnvelope`, `Channel`, `Severity` |
| `server/barysguard/services/events.py` (новый) | разбор пакета NDJSON, вставка |
| `server/barysguard/gateway/router.py` | `POST /gateway/v1/events` |
| `server/barysguard/gateway/schemas.py` | `EventsResult`, `RejectedLine` |
| `server/barysguard/api/events.py` (новый) | `GET /api/v1/events` |
| `server/barysguard/api/schemas.py`, `api/overview.py` | `EventSummary`, `EventPage`, `events_24h` |
| `server/barysguard/cli.py`, `main.py` | `ensure-partitions`, старт, дополнение OpenAPI |
| `api/gateway-v1.yaml` | контракт (генерируется) |

Агент:

| Файл | Ответственность |
|---|---|
| `agent/internal/events/envelope.go` (новый) | `Envelope`, константы, `NewEnvelope`, `MarshalLine` |
| `agent/internal/events/uuid7.go` (новый) | `NewUUIDv7` |
| `agent/internal/events/queue.go` (новый) | `Queue` (неблокирующий `Emit`, `Drain`) |
| `agent/internal/events/collector.go` (новый) | интерфейс `Collector` |
| `agent/internal/collectors/lifecycle/lifecycle.go` (новый) | сборщик канала `agent` |
| `agent/internal/buffer/buffer.go`, `key.go`, `limits.go` (новые) | шифрованный буфер, ключ, лимиты |
| `agent/internal/config/layout.go` | `BufferPath`, `BufferKeyPath` |
| `agent/internal/transport/types.go`, `methods.go` | `EventsResult`, `SendEvents` |
| `agent/internal/runner/agent.go`, `events.go` (новый) | отправка, сборщики, статистика в heartbeat |
| `agent/cmd/barysguard-agent/main.go` | открытие буфера и сборщиков |

Прочее: `scripts/smoke.ps1`, `deploy/stand/README.md`, `docs/QUICKSTART.md`, спека (статус).

---

### Task 1: Таблица `events` и менеджер партиций

**Files:**
- Create: `server/barysguard/db/models/event.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Create: `server/alembic/versions/20261005_1200_events.py`
- Create: `server/barysguard/services/event_partitions.py`
- Modify: `server/tests/conftest.py` (список TRUNCATE)
- Test: `server/tests/test_event_partitions.py`

**Interfaces:**
- Produces: `Event` (ORM) с колонками `occurred_at, event_id, agent_id, schema_version, channel, action, received_at, actor, process, subject, labels, artifact_sha256, severity`; `async def ensure_event_partitions(session: AsyncSession, months_ahead: int = 2, today: date | None = None) -> list[str]` — имена созданных разделов.

- [ ] **Step 1: Модель `server/barysguard/db/models/event.py`**

```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class Event(Base):
    """Событие, присланное агентом.

    Таблица партиционирована по месяцам (RANGE по occurred_at), поэтому первичный
    ключ обязан включать occurred_at, а уникальность одного event_id база не
    обеспечивает. Идемпотентность строится на паре (occurred_at, event_id).
    Сама таблица и разделы создаются миграцией: Alembic партиционирование
    по модели не генерирует.
    """

    __tablename__ = "events"

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    channel: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    process: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    subject: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    artifact_sha256: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, server_default="info")
```

Добавить в `server/barysguard/db/models/__init__.py` импорт `from barysguard.db.models.event import Event` (после `enrollment`) и `"Event"` в `__all__` (по алфавиту, после `"EnrollmentToken"`).

- [ ] **Step 2: Миграция `server/alembic/versions/20261005_1200_events.py`**

```python
"""events

Revision ID: e7a1c3b94d20
Revises: 3263ff47aa7a
Create Date: 2026-10-05 12:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'e7a1c3b94d20'
down_revision: str | None = '3263ff47aa7a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Границы разделов считаются по UTC независимо от пояса сессии.
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute(
        """
        CREATE TABLE events (
            event_id uuid NOT NULL,
            agent_id uuid NOT NULL,
            schema_version smallint NOT NULL,
            channel text NOT NULL,
            action text NOT NULL,
            occurred_at timestamptz NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            actor jsonb NOT NULL DEFAULT '{}',
            process jsonb NOT NULL DEFAULT '{}',
            subject jsonb NOT NULL DEFAULT '{}',
            labels jsonb NOT NULL DEFAULT '{}',
            artifact_sha256 text,
            severity text NOT NULL DEFAULT 'info',
            CONSTRAINT pk_events PRIMARY KEY (occurred_at, event_id),
            CONSTRAINT fk_events_agent_id_agents FOREIGN KEY (agent_id)
                REFERENCES agents (id) ON DELETE CASCADE
        ) PARTITION BY RANGE (occurred_at)
        """
    )
    # Страховка: событие с отметкой вне созданных разделов (часы агента неверны)
    # не должно теряться и не должно ронять приём всего пакета.
    op.execute("CREATE TABLE events_default PARTITION OF events DEFAULT")
    op.execute("CREATE INDEX ix_events_agent_occurred ON events (agent_id, occurred_at DESC)")
    op.execute("CREATE INDEX ix_events_channel_occurred ON events (channel, occurred_at DESC)")

    # Текущий месяц и два следующих. Дальше разделы создаёт приложение при старте
    # и команда barysguard-admin ensure-partitions.
    op.execute(
        """
        DO $$
        DECLARE
            month_start date;
        BEGIN
            FOR i IN 0..2 LOOP
                month_start := (date_trunc('month', now() AT TIME ZONE 'UTC')
                                + make_interval(months => i))::date;
                EXECUTE format(
                    'CREATE TABLE events_%s PARTITION OF events '
                    'FOR VALUES FROM (%L) TO (%L)',
                    to_char(month_start, 'YYYY_MM'),
                    month_start::timestamptz,
                    (month_start + interval '1 month')::timestamptz
                );
            END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE events CASCADE")
```

- [ ] **Step 3: Написать падающий тест `server/tests/test_event_partitions.py`**

```python
"""Менеджер партиций таблицы events."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import text

from barysguard.services.event_partitions import ensure_event_partitions
from tests.helpers import enroll_agent


async def test_ensure_creates_three_months_and_is_idempotent(session) -> None:
    # 2031 год не пересекается с разделами, которые миграция создала от текущей даты.
    created = await ensure_event_partitions(session, today=date(2031, 1, 15))

    assert created == ["events_2031_01", "events_2031_02", "events_2031_03"]
    assert await ensure_event_partitions(session, today=date(2031, 1, 15)) == []


async def test_ensure_handles_year_boundary(session) -> None:
    created = await ensure_event_partitions(session, months_ahead=2, today=date(2033, 11, 30))

    assert created == ["events_2033_11", "events_2033_12", "events_2034_01"]


async def test_event_outside_partitions_lands_in_default_and_moves_on_ensure(
    app_client, session
) -> None:
    agent = await enroll_agent(app_client, session, "partition-move")
    event_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO events (event_id, agent_id, schema_version, channel, action, "
            "occurred_at) VALUES (:id, :agent, 1, 'agent', 'start', :at)"
        ),
        {"id": event_id, "agent": agent.agent_id, "at": datetime(2036, 5, 10, tzinfo=UTC)},
    )

    where = text("SELECT tableoid::regclass::text FROM events WHERE event_id = :id")
    assert (await session.execute(where, {"id": event_id})).scalar_one() == "events_default"

    # Раздел на месяц события нельзя просто создать: база откажет, пока в
    # default лежат строки из его диапазона. Менеджер обязан перенести их.
    await ensure_event_partitions(session, today=date(2036, 5, 1))

    assert (await session.execute(where, {"id": event_id})).scalar_one() == "events_2036_05"
    total = (
        await session.execute(text("SELECT count(*) FROM events WHERE event_id = :id"), {"id": event_id})
    ).scalar_one()
    assert total == 1
```

- [ ] **Step 4: Запустить, убедиться, что падает**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_event_partitions.py -v`
Expected: FAIL (`ModuleNotFoundError: barysguard.services.event_partitions`).

- [ ] **Step 5: Реализация `server/barysguard/services/event_partitions.py`**

```python
"""Помесячные разделы таблицы events."""

from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _add_months(first_of_month: date, months: int) -> date:
    index = first_of_month.year * 12 + (first_of_month.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _bound(day: date) -> str:
    return f"{day.isoformat()} 00:00:00+00"


async def _create_partition(session: AsyncSession, name: str, start: date, end: date) -> None:
    # Имя и границы строятся из объектов date, а не из пользовательского ввода:
    # DDL не принимает параметры привязки.
    lower, upper = _bound(start), _bound(end)

    # Если в events_default уже лежат строки из этого диапазона (часы агента
    # убежали вперёд), база откажется создавать раздел. Строки временно
    # убираются и возвращаются уже в новый раздел.
    await session.execute(text("CREATE TEMP TABLE _events_move (LIKE events_default) ON COMMIT DROP"))
    await session.execute(
        text(
            "WITH moved AS (DELETE FROM events_default "  # noqa: S608
            f"WHERE occurred_at >= '{lower}' AND occurred_at < '{upper}' RETURNING *) "
            "INSERT INTO _events_move SELECT * FROM moved"
        )
    )
    await session.execute(
        text(f"CREATE TABLE {name} PARTITION OF events FOR VALUES FROM ('{lower}') TO ('{upper}')")  # noqa: S608
    )
    await session.execute(text("INSERT INTO events SELECT * FROM _events_move"))
    await session.execute(text("DROP TABLE _events_move"))


async def ensure_event_partitions(
    session: AsyncSession, months_ahead: int = 2, today: date | None = None
) -> list[str]:
    """Создаёт недостающие разделы от текущего месяца на months_ahead месяцев вперёд.

    Возвращает имена созданных разделов. Повторный вызов ничего не делает.
    """
    first = (today or datetime.now(UTC).date()).replace(day=1)
    created: list[str] = []

    for offset in range(months_ahead + 1):
        start = _add_months(first, offset)
        end = _add_months(first, offset + 1)
        name = f"events_{start:%Y_%m}"

        exists = (
            await session.execute(text("SELECT to_regclass(:name)"), {"name": name})
        ).scalar_one()
        if exists is not None:
            continue

        await _create_partition(session, name, start, end)
        created.append(name)

    return created
```

- [ ] **Step 6: Добавить `events` в TRUNCATE фикстуры `app_client`**

В `server/tests/conftest.py` строку `"TRUNCATE commands, agent_configs, ..."` дополнить: `"TRUNCATE events, commands, agent_configs, agent_certificates, enrollment_tokens, "`.

- [ ] **Step 7: Запустить тесты**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_event_partitions.py tests/test_migrations.py -v`
Expected: PASS. Если `CREATE TEMP TABLE ... (LIKE events_default)` или перенос строк падает — это дефект реализации, а не теста: разберите по `superpowers:systematic-debugging`.

- [ ] **Step 8: Линтеры и коммит**

```bash
cd server && .venv/Scripts/python -m ruff check barysguard tests && .venv/Scripts/python -m ruff format barysguard tests
git add server/barysguard/db/models/event.py server/barysguard/db/models/__init__.py server/alembic/versions/20261005_1200_events.py server/barysguard/services/event_partitions.py server/tests/conftest.py server/tests/test_event_partitions.py
git commit -m "feat(server): partitioned events table and partition manager" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Приём событий `POST /gateway/v1/events`

**Files:**
- Create: `server/barysguard/gateway/event_schemas.py`
- Create: `server/barysguard/services/events.py`
- Modify: `server/barysguard/gateway/schemas.py` (в конец)
- Modify: `server/barysguard/gateway/router.py`
- Test: `server/tests/test_event_envelope.py`, `server/tests/test_events_ingest.py`

**Interfaces:**
- Consumes: `Event`, `ensure_event_partitions` (задача 1); `current_agent`, `effective_config_for_agent` (существуют).
- Produces: `EventEnvelope` (pydantic); `parse_batch(body: bytes, max_lines: int) -> ParsedBatch` с полями `events: list[tuple[int, EventEnvelope]]`, `rejected: list[RejectedLine]`; исключения `BatchUnreadable`, `BatchTooLarge`; `async def store_events(session, agent_id, envelopes, received_at) -> int` — число вставленных строк; ответ `EventsResult{accepted:int, duplicates:int, rejected:list[RejectedLine{line:int, reason:str}]}`.

- [ ] **Step 1: Схема конверта `server/barysguard/gateway/event_schemas.py`**

```python
import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 64 * 1024


class Channel(enum.StrEnum):
    FILE = "file"
    USB = "usb"
    CLIPBOARD = "clipboard"
    NETWORK = "network"
    PRINT = "print"
    PROCESS = "process"
    AGENT = "agent"


class Severity(enum.StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EventArtifact(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    uploaded: bool = False


class EventEnvelope(BaseModel):
    """Конверт события, раздел 9 основной спеки.

    Поля, которых здесь нет, игнорируются (extra="ignore"). Это сознательно
    касается agent_id: личность агента определяет сертификат, а присланное
    в теле значение позволило бы скомпрометированному агенту писать события
    от чужого имени.
    """

    model_config = ConfigDict(extra="ignore")

    event_id: uuid.UUID
    schema_version: int
    occurred_at: datetime
    channel: Channel
    action: str = Field(min_length=1, max_length=64)
    severity_hint: Severity = Severity.INFO
    actor: dict[str, Any] = Field(default_factory=dict)
    process: dict[str, Any] = Field(default_factory=dict)
    subject: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)
    artifact: EventArtifact | None = None

    @field_validator("occurred_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        # Время без пояса база трактует по поясу сессии, и событие уезжает
        # в чужой месяц и чужой раздел.
        if value.tzinfo is None:
            raise ValueError("occurred_at must carry a timezone")
        return value
```

- [ ] **Step 2: Написать падающий юнит-тест `server/tests/test_event_envelope.py`**

```python
"""Разбор пакета NDJSON без базы данных."""

import json
import uuid

import pytest

from barysguard.services.events import BatchTooLarge, BatchUnreadable, parse_batch


def _event(**overrides) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": "2026-10-05T10:00:00+06:00",
        "channel": "agent",
        "action": "start",
    }
    event.update(overrides)
    return event


def _body(*lines: str) -> bytes:
    return "\n".join(lines).encode("utf-8")


def test_valid_event_is_parsed_with_defaults() -> None:
    parsed = parse_batch(_body(json.dumps(_event())), max_lines=10)

    assert len(parsed.events) == 1
    line, envelope = parsed.events[0]
    assert line == 1
    assert envelope.severity_hint.value == "info"
    assert envelope.subject == {}
    assert parsed.rejected == []


def test_each_defect_is_reported_with_its_line_number() -> None:
    body = _body(
        "{не json",
        json.dumps([1, 2]),
        json.dumps(_event(schema_version=2)),
        json.dumps(_event(channel="telepathy")),
        json.dumps(_event(occurred_at="2026-10-05T10:00:00")),
        json.dumps(_event(action="")),
        json.dumps(_event(labels="не объект")),
        json.dumps(_event()),
    )

    parsed = parse_batch(body, max_lines=100)

    assert [(r.line, r.reason) for r in parsed.rejected] == [
        (1, "invalid_json"),
        (2, "not_an_object"),
        (3, "unsupported_schema"),
        (4, "invalid_event"),
        (5, "invalid_event"),
        (6, "invalid_event"),
        (7, "invalid_event"),
    ]
    assert [line for line, _ in parsed.events] == [8]


def test_blank_lines_and_crlf_are_tolerated_and_keep_line_numbers() -> None:
    body = (json.dumps(_event()) + "\r\n\r\n" + json.dumps(_event(channel="x")) + "\r\n").encode()

    parsed = parse_batch(body, max_lines=10)

    assert [line for line, _ in parsed.events] == [1]
    assert [(r.line, r.reason) for r in parsed.rejected] == [(3, "invalid_event")]


def test_oversized_event_is_rejected_without_hurting_neighbours() -> None:
    huge = _event(labels={"blob": "x" * 70_000})

    parsed = parse_batch(_body(json.dumps(huge), json.dumps(_event())), max_lines=10)

    assert [(r.line, r.reason) for r in parsed.rejected] == [(1, "too_large")]
    assert [line for line, _ in parsed.events] == [2]


def test_agent_id_in_body_is_ignored() -> None:
    parsed = parse_batch(_body(json.dumps(_event(agent_id=str(uuid.uuid4())))), max_lines=10)

    assert not hasattr(parsed.events[0][1], "agent_id")


def test_too_many_lines_is_refused() -> None:
    with pytest.raises(BatchTooLarge):
        parse_batch(_body(*[json.dumps(_event()) for _ in range(3)]), max_lines=2)


def test_undecodable_or_empty_body_is_unreadable() -> None:
    with pytest.raises(BatchUnreadable):
        parse_batch(b"\xff\xfe\x00", max_lines=10)
    with pytest.raises(BatchUnreadable):
        parse_batch(b"\n  \n", max_lines=10)
```

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_event_envelope.py -v`
Expected: FAIL (`ModuleNotFoundError: barysguard.services.events`).

- [ ] **Step 4: Реализация `server/barysguard/services/events.py`**

```python
"""Разбор пакета событий и запись в таблицу events."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.event import Event
from barysguard.gateway.event_schemas import MAX_EVENT_BYTES, SCHEMA_VERSION, EventEnvelope

# Параметров привязки у asyncpg не больше 32767: пакет в 10000 событий по 13
# колонок в один INSERT не поместится, поэтому вставка идёт порциями.
INSERT_CHUNK = 1000


class BatchUnreadable(Exception):
    """Тело не разбирается как NDJSON: не UTF-8 или нет ни одной строки."""


class BatchTooLarge(Exception):
    """В пакете больше событий, чем разрешает конфигурация агента."""


@dataclass(frozen=True)
class RejectedLine:
    line: int
    reason: str


@dataclass
class ParsedBatch:
    events: list[tuple[int, EventEnvelope]] = field(default_factory=list)
    rejected: list[RejectedLine] = field(default_factory=list)


def _parse_line(raw: str) -> tuple[EventEnvelope | None, str | None]:
    if len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
        return None, "too_large"

    try:
        document: Any = json.loads(raw)
    except ValueError:
        return None, "invalid_json"

    if not isinstance(document, dict):
        return None, "not_an_object"

    # Версия проверяется до полной валидации: событие будущей схемы нельзя
    # разбирать по правилам схемы 1, а причина отказа должна быть понятной.
    if document.get("schema_version") not in (None, SCHEMA_VERSION):
        return None, "unsupported_schema"

    try:
        return EventEnvelope.model_validate(document), None
    except ValidationError:
        return None, "invalid_event"


def parse_batch(body: bytes, max_lines: int) -> ParsedBatch:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BatchUnreadable("body is not UTF-8") from exc

    numbered = [
        (number, line.strip())
        for number, line in enumerate(text.split("\n"), start=1)
        if line.strip()
    ]
    if not numbered:
        raise BatchUnreadable("empty batch")
    if len(numbered) > max_lines:
        raise BatchTooLarge(f"{len(numbered)} events, limit {max_lines}")

    parsed = ParsedBatch()
    for number, raw in numbered:
        envelope, reason = _parse_line(raw)
        if envelope is None:
            parsed.rejected.append(RejectedLine(number, reason or "invalid_event"))
        else:
            parsed.events.append((number, envelope))
    return parsed


async def store_events(
    session: AsyncSession,
    agent_id: uuid.UUID,
    envelopes: list[EventEnvelope],
    received_at: datetime,
) -> int:
    """Вставляет события идемпотентно. Возвращает число реально вставленных строк."""
    rows = [
        {
            "event_id": envelope.event_id,
            "agent_id": agent_id,
            "schema_version": envelope.schema_version,
            "channel": envelope.channel.value,
            "action": envelope.action,
            "occurred_at": envelope.occurred_at,
            "received_at": received_at,
            "actor": envelope.actor,
            "process": envelope.process,
            "subject": envelope.subject,
            "labels": envelope.labels,
            "artifact_sha256": envelope.artifact.sha256 if envelope.artifact else None,
            "severity": envelope.severity_hint.value,
        }
        for envelope in envelopes
    ]

    inserted = 0
    for start in range(0, len(rows), INSERT_CHUNK):
        statement = (
            pg_insert(Event)
            .values(rows[start : start + INSERT_CHUNK])
            .on_conflict_do_nothing(index_elements=["occurred_at", "event_id"])
            .returning(Event.event_id)
        )
        inserted += len((await session.execute(statement)).all())
    return inserted
```

- [ ] **Step 5: Запустить юнит-тест**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_event_envelope.py -v`
Expected: PASS (7 тестов).

- [ ] **Step 6: Схема ответа в конец `server/barysguard/gateway/schemas.py`**

```python
class RejectedLine(BaseModel):
    line: int
    reason: str


class EventsResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[RejectedLine]
```

- [ ] **Step 7: Написать падающие интеграционные тесты `server/tests/test_events_ingest.py`**

```python
"""POST /gateway/v1/events."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from tests.helpers import enroll_agent, set_global_config

NDJSON = {"Content-Type": "application/x-ndjson"}


def _event(**overrides) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "channel": "agent",
        "action": "start",
        "subject": {"component": "agent", "detail": "0.1.0"},
    }
    event.update(overrides)
    return event


def _body(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode()


async def _post(app_client, agent, *events: dict, raw: bytes | None = None):
    return await app_client.post(
        "/gateway/v1/events",
        headers={**agent.headers, **NDJSON},
        content=raw if raw is not None else _body(*events),
    )


async def _count(session, agent_id) -> int:
    return (
        await session.execute(
            text("SELECT count(*) FROM events WHERE agent_id = :a"), {"a": agent_id}
        )
    ).scalar_one()


async def test_events_are_accepted_and_stored(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-basic")

    response = await _post(app_client, agent, _event(), _event(action="stop"))

    assert response.status_code == 202, response.text
    assert response.json() == {"accepted": 2, "duplicates": 0, "rejected": []}
    assert await _count(session, agent.agent_id) == 2


async def test_resending_the_same_batch_reports_duplicates(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-dup")
    batch = [_event(), _event()]

    await _post(app_client, agent, *batch)
    again = await _post(app_client, agent, *batch)

    assert again.status_code == 202
    assert again.json()["accepted"] == 0
    assert again.json()["duplicates"] == 2
    assert await _count(session, agent.agent_id) == 2


async def test_same_event_twice_in_one_batch_is_stored_once(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-dup-inline")
    event = _event()

    response = await _post(app_client, agent, event, event)

    assert response.json()["accepted"] == 1
    assert response.json()["duplicates"] == 1


async def test_bad_events_are_rejected_individually(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-reject")

    response = await _post(
        app_client,
        agent,
        _event(),
        _event(schema_version=7),
        _event(channel="telepathy"),
        _event(occurred_at="2026-10-05T10:00:00"),
        _event(labels={"blob": "x" * 70_000}),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 1
    assert [(r["line"], r["reason"]) for r in body["rejected"]] == [
        (2, "unsupported_schema"),
        (3, "invalid_event"),
        (4, "invalid_event"),
        (5, "too_large"),
    ]


async def test_crlf_and_blank_lines_are_accepted(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-crlf")
    raw = (json.dumps(_event()) + "\r\n\r\n" + json.dumps(_event()) + "\r\n").encode()

    response = await _post(app_client, agent, raw=raw)

    assert response.json()["accepted"] == 2


async def test_agent_id_comes_from_the_certificate_not_the_body(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-identity")
    other = await enroll_agent(app_client, session, "ingest-victim")

    await _post(app_client, agent, _event(agent_id=str(other.agent_id)))

    assert await _count(session, agent.agent_id) == 1
    assert await _count(session, other.agent_id) == 0


async def test_unauthenticated_request_is_refused(app_client) -> None:
    response = await app_client.post(
        "/gateway/v1/events", headers=NDJSON, content=_body(_event())
    )

    assert response.status_code == 403


async def test_too_many_events_gives_413(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-413-count")
    await set_global_config(session, {"transport": {"event_batch_max": 1}})
    await session.commit()

    response = await _post(app_client, agent, _event(), _event())

    assert response.status_code == 413


async def test_oversized_body_gives_413(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-413-bytes")

    response = await _post(app_client, agent, raw=b"x" * (4 * 1024 * 1024 + 1))

    assert response.status_code == 413


async def test_unreadable_body_gives_400(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-400")

    assert (await _post(app_client, agent, raw=b"\xff\xfe\x00")).status_code == 400
    assert (await _post(app_client, agent, raw=b"\n\n")).status_code == 400


async def test_event_with_far_past_time_is_kept_in_default_partition(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-default")
    event = _event(occurred_at="2001-01-01T00:00:00+00:00")

    response = await _post(app_client, agent, event)

    assert response.json()["accepted"] == 1
    table = (
        await session.execute(
            text("SELECT tableoid::regclass::text FROM events WHERE event_id = :id"),
            {"id": uuid.UUID(event["event_id"])},
        )
    ).scalar_one()
    assert table == "events_default"
```

- [ ] **Step 8: Запустить, убедиться, что падает**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_events_ingest.py -v`
Expected: FAIL (404 вместо 202: маршрута нет).

- [ ] **Step 9: Маршрут в `server/barysguard/gateway/router.py`**

Импорты: добавить `from barysguard.gateway.schemas import EventsResult, RejectedLine` (в существующий блок импорта схем) и
`from barysguard.services.events import BatchTooLarge, BatchUnreadable, parse_batch, store_events`.
В конец файла:

```python
@router.post(
    "/events",
    response_model=EventsResult,
    status_code=status.HTTP_202_ACCEPTED,
    # Тело — NDJSON, FastAPI его не описывает: контракт задаётся вручную.
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/x-ndjson": {"schema": {"$ref": "#/components/schemas/EventEnvelope"}}
            },
        }
    },
)
async def ingest_events(
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> EventsResult:
    """Пакет событий в NDJSON, одна строка — одно событие. Идемпотентно."""
    document, _ = await effective_config_for_agent(session, agent)
    max_events = document["transport"]["event_batch_max"]
    max_bytes = document["transport"]["event_batch_max_bytes"]

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "batch too large")

    # Тело читается потоком и обрывается на пределе: заголовок
    # Content-Length можно не прислать вовсе.
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "batch too large")
        chunks.append(chunk)

    try:
        parsed = parse_batch(b"".join(chunks), max_events)
    except BatchTooLarge as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "batch too large") from exc
    except BatchUnreadable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unreadable batch") from exc

    inserted = await store_events(
        session, agent.id, [envelope for _, envelope in parsed.events], datetime.now(UTC)
    )

    return EventsResult(
        accepted=inserted,
        duplicates=len(parsed.events) - inserted,
        rejected=[RejectedLine(line=r.line, reason=r.reason) for r in parsed.rejected],
    )
```

`EventEnvelope` попадёт в components в задаче 3 (дополнение OpenAPI); пока ссылка висит только в схеме, тестов она не касается.

- [ ] **Step 10: Запустить тесты**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_events_ingest.py tests/test_event_envelope.py tests/test_heartbeat_endpoint.py -v`
Expected: PASS.

- [ ] **Step 11: Линтеры и коммит**

```bash
cd server && .venv/Scripts/python -m ruff check barysguard tests && .venv/Scripts/python -m ruff format barysguard tests
git add server/barysguard/gateway server/barysguard/services/events.py server/tests/test_event_envelope.py server/tests/test_events_ingest.py
git commit -m "feat(server): event ingest endpoint with per-event validation" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Чтение событий, сводка, CLI, старт и контракт

**Files:**
- Create: `server/barysguard/api/events.py`
- Modify: `server/barysguard/api/schemas.py`, `server/barysguard/api/overview.py`, `server/barysguard/main.py`, `server/barysguard/cli.py`
- Regenerate: `api/gateway-v1.yaml`
- Test: `server/tests/test_console_events_api.py`, `server/tests/test_openapi_contract.py`, дополнить `server/tests/test_console_overview_api.py`, `server/tests/test_cli.py`

**Interfaces:**
- Consumes: `Event`, `ensure_event_partitions`, `EventEnvelope`, `scope_group_ids`, `current_user`.
- Produces: `GET /api/v1/events` → `EventPage{items: list[EventSummary], next_cursor: str | None}`; `Overview.events_24h: int`; команда `barysguard-admin ensure-partitions [--months-ahead N]`.

- [ ] **Step 1: Падающие тесты `server/tests/test_console_events_api.py`**

```python
"""GET /api/v1/events."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.user import UserRole
from tests.helpers import enroll_agent, login_as

NDJSON = {"Content-Type": "application/x-ndjson"}


def _event(*, at: datetime, channel: str = "agent", action: str = "start", **extra) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": at.isoformat(),
        "channel": channel,
        "action": action,
        **extra,
    }


async def _send(app_client, agent, *events: dict) -> None:
    body = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
    response = await app_client.post(
        "/gateway/v1/events", headers={**agent.headers, **NDJSON}, content=body
    )
    assert response.status_code == 202, response.text


async def test_events_are_listed_newest_first_with_hostname(app_client, session) -> None:
    await login_as(app_client, session, username="ev-admin", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-list")
    now = datetime.now(UTC)
    await _send(
        app_client,
        agent,
        _event(at=now - timedelta(minutes=2), action="start"),
        _event(at=now - timedelta(minutes=1), action="stop"),
    )

    response = await app_client.get("/api/v1/events")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["action"] for item in items] == ["stop", "start"]
    assert items[0]["hostname"] == "ws-1"
    assert items[0]["agent_id"] == str(agent.agent_id)
    assert items[0]["severity"] == "info"
    assert response.json()["next_cursor"] is None


async def test_filters_by_channel_action_severity_and_time(app_client, session) -> None:
    await login_as(app_client, session, username="ev-filter", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-filter-agent")
    now = datetime.now(UTC)
    await _send(
        app_client,
        agent,
        _event(at=now - timedelta(hours=3), channel="file", action="copy", severity_hint="high"),
        _event(at=now - timedelta(minutes=5), channel="agent", action="start"),
    )

    by_channel = (await app_client.get("/api/v1/events", params={"channel": "file"})).json()
    by_action = (await app_client.get("/api/v1/events", params={"action": "start"})).json()
    by_severity = (await app_client.get("/api/v1/events", params={"severity": "high"})).json()
    recent = (
        await app_client.get(
            "/api/v1/events", params={"since": (now - timedelta(hours=1)).isoformat()}
        )
    ).json()
    naive_until = (
        await app_client.get(
            "/api/v1/events",
            params={"until": (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()},
        )
    ).json()

    assert [i["channel"] for i in by_channel["items"]] == ["file"]
    assert [i["action"] for i in by_action["items"]] == ["start"]
    assert [i["severity"] for i in by_severity["items"]] == ["high"]
    assert [i["action"] for i in recent["items"]] == ["start"]
    assert [i["action"] for i in naive_until["items"]] == ["copy"]


async def test_filter_by_agent(app_client, session) -> None:
    await login_as(app_client, session, username="ev-agent-filter", role=UserRole.ADMIN)
    first = await enroll_agent(app_client, session, "ev-a1")
    second = await enroll_agent(app_client, session, "ev-a2")
    now = datetime.now(UTC)
    await _send(app_client, first, _event(at=now))
    await _send(app_client, second, _event(at=now))

    response = await app_client.get("/api/v1/events", params={"agent_id": str(second.agent_id)})

    assert [i["agent_id"] for i in response.json()["items"]] == [str(second.agent_id)]


async def test_cursor_pagination_walks_every_event_once(app_client, session) -> None:
    await login_as(app_client, session, username="ev-page", role=UserRole.ADMIN)
    agent = await enroll_agent(app_client, session, "ev-page-agent")
    now = datetime.now(UTC)
    await _send(
        app_client, agent, *[_event(at=now - timedelta(seconds=i)) for i in range(5)]
    )

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await app_client.get("/api/v1/events", params=params)).json()
        seen.extend(item["event_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_bad_cursor_gives_400(app_client, session) -> None:
    await login_as(app_client, session, username="ev-badcursor", role=UserRole.ADMIN)

    response = await app_client.get("/api/v1/events", params={"cursor": "not-a-cursor"})

    assert response.status_code == 400


async def test_operator_sees_only_events_of_their_subtree(app_client, session) -> None:
    mine = AgentGroup(name="Филиал А")
    theirs = AgentGroup(name="Филиал Б")
    session.add_all([mine, theirs])
    await session.flush()
    await login_as(
        app_client, session, username="ev-scoped", role=UserRole.OPERATOR, scope_group_id=mine.id
    )
    near = await enroll_agent(app_client, session, "ev-near", group_id=mine.id)
    far = await enroll_agent(app_client, session, "ev-far", group_id=theirs.id)
    now = datetime.now(UTC)
    await _send(app_client, near, _event(at=now))
    await _send(app_client, far, _event(at=now))

    items = (await app_client.get("/api/v1/events")).json()["items"]

    assert [i["agent_id"] for i in items] == [str(near.agent_id)]


async def test_unauthenticated_request_is_refused(app_client) -> None:
    assert (await app_client.get("/api/v1/events")).status_code == 401
```

- [ ] **Step 2: Падающий тест сводки** — добавить в конец `server/tests/test_console_overview_api.py`:

```python
async def test_overview_counts_events_of_the_last_day(app_client, session) -> None:
    import json
    import uuid

    await login_as(app_client, session, username="dash-events", role=UserRole.ADMIN)
    enrolled = await enroll_agent(app_client, session, "machine-events")
    now = datetime.now(UTC)

    def line(at: datetime) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "schema_version": 1,
            "occurred_at": at.isoformat(),
            "channel": "agent",
            "action": "start",
        }

    body = "\n".join(json.dumps(line(at)) for at in (now, now - timedelta(days=3))) + "\n"
    await app_client.post(
        "/gateway/v1/events",
        headers={**enrolled.headers, "Content-Type": "application/x-ndjson"},
        content=body.encode(),
    )

    assert (await app_client.get("/api/v1/overview")).json()["events_24h"] == 1
```

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_console_events_api.py tests/test_console_overview_api.py -v`
Expected: FAIL (404 на `/api/v1/events`, нет `events_24h`).

- [ ] **Step 4: Схемы в `server/barysguard/api/schemas.py`**

В конец файла (импорт `Any` и `uuid`, `datetime` в файле уже есть; при отсутствии добавить):

```python
class EventSummary(BaseModel):
    event_id: uuid.UUID
    agent_id: uuid.UUID
    hostname: str
    occurred_at: datetime
    received_at: datetime
    channel: str
    action: str
    severity: str
    actor: dict[str, Any]
    process: dict[str, Any]
    subject: dict[str, Any]
    labels: dict[str, Any]
    artifact_sha256: str | None


class EventPage(BaseModel):
    items: list[EventSummary]
    next_cursor: str | None
```

В класс `Overview` добавить поле `events_24h: int` (после `commands`).

- [ ] **Step 5: `server/barysguard/api/events.py`**

```python
"""Чтение событий оператором."""

import base64
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import current_user
from barysguard.api.schemas import EventPage, EventSummary
from barysguard.db.models.agent import Agent
from barysguard.db.models.event import Event
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.scope import scope_group_ids

router = APIRouter(prefix="/api/v1", tags=["api"])


def _aware(value: datetime) -> datetime:
    # Время без пояса из строки запроса база не примет: считаем его UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _encode_cursor(occurred_at: datetime, event_id: uuid.UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{event_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        stamp, identifier = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(stamp), uuid.UUID(identifier)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from None


@router.get("/events", response_model=EventPage)
async def list_events(
    agent_id: uuid.UUID | None = None,
    channel: str | None = None,
    action: str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EventPage:
    """События от новых к старым. Оператор видит только свою область."""
    conditions: list[ColumnElement[bool]] = []

    visible = await scope_group_ids(session, user)
    if visible is not None:
        conditions.append(Agent.group_id.in_(visible))

    if agent_id is not None:
        conditions.append(Event.agent_id == agent_id)
    if channel:
        conditions.append(Event.channel == channel)
    if action:
        conditions.append(Event.action == action)
    if severity:
        conditions.append(Event.severity == severity)
    if since is not None:
        conditions.append(Event.occurred_at >= _aware(since))
    if until is not None:
        conditions.append(Event.occurred_at <= _aware(until))
    if cursor:
        at, identifier = _decode_cursor(cursor)
        conditions.append(
            tuple_(Event.occurred_at, Event.event_id) < tuple_(at, identifier)
        )

    rows = (
        await session.execute(
            select(Event, Agent.hostname)
            .join(Agent, Agent.id == Event.agent_id)
            .where(*conditions)
            .order_by(Event.occurred_at.desc(), Event.event_id.desc())
            .limit(limit + 1)
        )
    ).all()

    page = rows[:limit]
    # Курсор выдаётся только когда за страницей есть ещё строки: иначе
    # оператор увидит «дальше», за которым ничего нет.
    next_cursor = (
        _encode_cursor(page[-1][0].occurred_at, page[-1][0].event_id)
        if len(rows) > limit
        else None
    )

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
            )
            for event, hostname in page
        ],
        next_cursor=next_cursor,
    )
```

- [ ] **Step 6: Сводка в `server/barysguard/api/overview.py`**

Добавить импорт `from barysguard.db.models.event import Event`. В `read_overview` после вычисления `failed`:

```python
    events_24h = (
        await session.execute(
            select(func.count())
            .select_from(Event)
            .join(Agent, Agent.id == Event.agent_id)
            .where(*conditions, Event.occurred_at > now - timedelta(hours=24))
        )
    ).scalar_one()
```

и в конструктор `Overview(...)` передать `events_24h=events_24h,`.

- [ ] **Step 7: `server/barysguard/main.py`: роутер, старт, дополнение OpenAPI**

Заменить файл целиком по смыслу правок: добавить в начало импорты `logging`, `from contextlib import asynccontextmanager`; перед `create_app` —

```python
logger = logging.getLogger(__name__)


async def _ensure_partitions_on_startup() -> None:
    """Создаёт разделы events на месяц вперёд.

    Недоступная при старте база не должна мешать процессу подняться:
    /ready сообщит о ней отдельно, а страховочный раздел сохранит события.
    """
    from barysguard.db.session import _get_sessionmaker
    from barysguard.services.event_partitions import ensure_event_partitions

    try:
        async with _get_sessionmaker()() as session:
            created = await ensure_event_partitions(session)
            await session.commit()
        if created:
            logger.info("созданы разделы events: %s", ", ".join(created))
    except Exception:
        logger.warning("не удалось создать разделы events при старте", exc_info=True)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _ensure_partitions_on_startup()
    yield
```

В `FastAPI(...)` добавить `lifespan=_lifespan`. После `app.include_router(overview_router)` добавить `from barysguard.api.events import router as events_router` (в блок импортов функции) и `app.include_router(events_router)`. Перед `return app`:

```python
    from barysguard.gateway.event_schemas import EventEnvelope

    original_openapi = app.openapi

    def openapi_with_envelope() -> dict:
        # Тело /gateway/v1/events — NDJSON, FastAPI его схему не знает:
        # описание конверта добавляется в components вручную.
        schema = original_openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        if "EventEnvelope" not in components:
            envelope = EventEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")
            components.update(envelope.pop("$defs", {}))
            components["EventEnvelope"] = envelope
        return schema

    app.openapi = openapi_with_envelope
```

- [ ] **Step 8: CLI `server/barysguard/cli.py`**

Импорт: `from barysguard.services.event_partitions import ensure_event_partitions`. Новая функция рядом с `_bootstrap_dev`:

```python
async def _ensure_partitions(months_ahead: int) -> int:
    engine = create_engine_from_url(get_settings().database_url)
    try:
        async with session_factory(engine)() as session:
            created = await ensure_event_partitions(session, months_ahead)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"создано разделов: {len(created)}")
    for name in created:
        print(f"  {name}")
    return 0
```

В `main()` после `bootstrap-dev`:

```python
    partitions = sub.add_parser("ensure-partitions", help="создать разделы таблицы events")
    partitions.add_argument("--months-ahead", type=int, default=2)
```

и обработчик `if args.command == "ensure-partitions": raise SystemExit(asyncio.run(_ensure_partitions(args.months_ahead)))`.

Тест в конец `server/tests/test_cli.py`:

```python
async def test_cli_ensure_partitions_reports_created_partitions(
    migrated_database_url, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    monkeypatch.setattr(
        "sys.argv", ["barysguard-admin", "ensure-partitions", "--months-ahead", "4"]
    )

    from barysguard.core.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)

    assert exited.value.code == 0
    assert "создано разделов" in capsys.readouterr().out
```

- [ ] **Step 9: Запустить тесты**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_console_events_api.py tests/test_console_overview_api.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 10: Тест дрейфа контракта и регенерация `api/gateway-v1.yaml`**

Тест `server/tests/test_openapi_contract.py`:

```python
"""Файл api/gateway-v1.yaml — выгрузка OpenAPI приложения, общий с агентом."""

import json
from pathlib import Path

import yaml

from barysguard.main import create_app

CONTRACT = Path(__file__).resolve().parents[2] / "api" / "gateway-v1.yaml"


def test_checked_in_contract_matches_the_application() -> None:
    generated = json.loads(json.dumps(create_app().openapi()))
    stored = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))

    assert stored == generated, (
        "api/gateway-v1.yaml разошёлся с приложением; перегенерируйте его: "
        "python -c \"import yaml; from barysguard.main import create_app; "
        "print(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True), end='')\" "
        "> ../api/gateway-v1.yaml"
    )
    assert "EventEnvelope" in generated["components"]["schemas"]
    assert "/gateway/v1/events" in generated["paths"]
```

Run (должен упасть: файл не содержит событий): `cd server && .venv/Scripts/python -m pytest tests/test_openapi_contract.py -v` → FAIL.

Регенерация:

```bash
cd server && .venv/Scripts/python -c "import yaml; from barysguard.main import create_app; print(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True), end='')" > ../api/gateway-v1.yaml
git diff --stat ../api/gateway-v1.yaml
```

Просмотрите `git diff api/gateway-v1.yaml`: изменения должны ограничиваться событиями (`/gateway/v1/events`, `/api/v1/events`, `EventEnvelope`, `EventsResult`, `RejectedLine`, `EventSummary`, `EventPage`, `events_24h`, enum-ы `Channel`, `Severity`, `EventArtifact`). Если дифф шире (файл успел разойтись с кодом раньше), это не ошибка: файл генерируемый; отметьте это в сообщении коммита.

Run: `cd server && .venv/Scripts/python -m pytest tests/test_openapi_contract.py -v` → PASS.

- [ ] **Step 11: Полный прогон сервера, линтеры и коммит**

```bash
cd server && .venv/Scripts/python -m pytest -q && .venv/Scripts/python -m ruff check barysguard tests && .venv/Scripts/python -m ruff format barysguard tests
git add server api/gateway-v1.yaml
git commit -m "feat(server): console events API, overview counter, partition CLI and contract" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Expected: все тесты зелёные.

---

### Task 4: Конверт, UUIDv7, очередь, интерфейс сборщика (агент)

**Files:**
- Modify: `agent/go.mod`, `agent/go.sum` (в задаче 6; здесь не нужно)
- Create: `agent/internal/events/envelope.go`, `uuid7.go`, `queue.go`, `collector.go`
- Test: `agent/internal/events/envelope_test.go`, `uuid7_test.go`, `queue_test.go`

**Interfaces:**
- Produces:
  - `type Envelope struct{ EventID string; SchemaVersion int; OccurredAt time.Time; Channel, Action, SeverityHint string; Actor, Process, Subject, Labels map[string]any; Artifact *Artifact }` (json-теги — имена полей контракта);
  - `func NewEnvelope(channel, action, severity string, subject map[string]any) (Envelope, error)`;
  - `func (e Envelope) MarshalLine() ([]byte, error)` — одна строка JSON без перевода строки, nil-карты заменены на `{}`;
  - `func SeverityRank(severity string) uint8` (`info`=0 … `critical`=4), константа `CriticalRank = 4`;
  - `func NewUUIDv7(at time.Time) (string, error)`;
  - `type Queue`; `NewQueue(capacity int) *Queue`; `(*Queue).Emit(Envelope)`; `(*Queue).Close()`; `(*Queue).Drain(appendEvent func(Envelope) error, lost func() uint64)`;
  - `type Collector interface{ Name() string; Run(ctx context.Context, emit func(Envelope)) error }`.

- [ ] **Step 1: Падающие тесты**

`agent/internal/events/uuid7_test.go`:

```go
package events

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

func formatMillis(at time.Time) string {
	return strings.ToLower(fmt.Sprintf("%012x", uint64(at.UnixMilli())))
}

func TestUUIDv7HasVersionVariantAndTimestamp(t *testing.T) {
	at := time.Date(2026, 10, 5, 10, 0, 0, 0, time.UTC)

	id, err := NewUUIDv7(at)
	if err != nil {
		t.Fatalf("NewUUIDv7: %v", err)
	}

	parts := strings.Split(id, "-")
	if len(parts) != 5 || len(id) != 36 {
		t.Fatalf("не UUID: %q", id)
	}
	if parts[2][0] != '7' {
		t.Errorf("версия %q, ожидалась 7", parts[2][:1])
	}
	if !strings.ContainsRune("89ab", rune(parts[3][0])) {
		t.Errorf("вариант %q вне 8..b", parts[3][:1])
	}
	// Первые 48 бит — миллисекунды с эпохи.
	wantPrefix := strings.ToLower(strings.TrimLeft(formatMillis(at), "0"))
	got := strings.TrimLeft(parts[0]+parts[1], "0")
	if got != wantPrefix {
		t.Errorf("метка времени %s, ожидалась %s", got, wantPrefix)
	}
}

func TestUUIDv7SortsByTime(t *testing.T) {
	earlier, _ := NewUUIDv7(time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	later, _ := NewUUIDv7(time.Date(2026, 1, 1, 0, 0, 1, 0, time.UTC))

	if !(earlier < later) {
		t.Fatalf("%s не меньше %s", earlier, later)
	}
}

func TestUUIDv7IsUnique(t *testing.T) {
	at := time.Now()
	seen := map[string]bool{}
	for i := 0; i < 1000; i++ {
		id, _ := NewUUIDv7(at)
		if seen[id] {
			t.Fatalf("повтор %s", id)
		}
		seen[id] = true
	}
}
```

`agent/internal/events/envelope_test.go`:

```go
package events

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestNewEnvelopeFillsIdentityAndDefaults(t *testing.T) {
	env, err := NewEnvelope(ChannelAgent, "start", SeverityInfo, map[string]any{"component": "agent"})
	if err != nil {
		t.Fatalf("NewEnvelope: %v", err)
	}

	if env.SchemaVersion != SchemaVersion {
		t.Errorf("schema_version = %d", env.SchemaVersion)
	}
	if len(env.EventID) != 36 || env.OccurredAt.IsZero() {
		t.Errorf("не заполнены event_id/occurred_at: %+v", env)
	}
	if env.Channel != "agent" || env.Action != "start" || env.SeverityHint != "info" {
		t.Errorf("поля: %+v", env)
	}
}

func TestMarshalLineIsSingleLineWithObjectsInsteadOfNull(t *testing.T) {
	env, _ := NewEnvelope(ChannelAgent, "start", SeverityInfo, nil)
	env.Subject = map[string]any{"detail": "строка\nс переводом"}

	line, err := env.MarshalLine()
	if err != nil {
		t.Fatalf("MarshalLine: %v", err)
	}

	if bytes.ContainsAny(line, "\n\r") {
		t.Fatalf("в строке есть перевод строки: %q", line)
	}
	var decoded map[string]any
	if err := json.Unmarshal(line, &decoded); err != nil {
		t.Fatalf("не JSON: %v", err)
	}
	// Сервер отвергает null там, где ждёт объект.
	for _, key := range []string{"actor", "process", "subject", "labels"} {
		if _, ok := decoded[key].(map[string]any); !ok {
			t.Errorf("%s = %v, ожидался объект", key, decoded[key])
		}
	}
}

func TestSeverityRankOrdersLevels(t *testing.T) {
	order := []string{SeverityInfo, SeverityLow, SeverityMedium, SeverityHigh, SeverityCritical}
	for i, severity := range order {
		if got := SeverityRank(severity); int(got) != i {
			t.Errorf("SeverityRank(%q) = %d, ожидалось %d", severity, got, i)
		}
	}
	if SeverityRank("что-то") != 0 {
		t.Error("неизвестная критичность должна считаться info")
	}
	if SeverityRank(SeverityCritical) != CriticalRank {
		t.Error("CriticalRank расходится с SeverityRank")
	}
}
```

`agent/internal/events/queue_test.go`:

```go
package events

import (
	"errors"
	"sync"
	"testing"
)

func envelope(t *testing.T, action string) Envelope {
	t.Helper()
	env, err := NewEnvelope(ChannelAgent, action, SeverityInfo, nil)
	if err != nil {
		t.Fatal(err)
	}
	return env
}

func TestDrainDeliversEveryEmittedEventInOrder(t *testing.T) {
	queue := NewQueue(10)
	var got []string
	done := make(chan struct{})
	go func() {
		queue.Drain(func(e Envelope) error { got = append(got, e.Action); return nil }, nil)
		close(done)
	}()

	for _, action := range []string{"a", "b", "c"} {
		queue.Emit(envelope(t, action))
	}
	queue.Close()
	<-done

	if len(got) != 3 || got[0] != "a" || got[1] != "b" || got[2] != "c" {
		t.Fatalf("получено %v", got)
	}
}

func TestEmitNeverBlocksAndOverflowIsReportedAsDroppedEvent(t *testing.T) {
	queue := NewQueue(2)
	// Приёмника нет: очередь переполняется. Emit не должен зависнуть.
	for i := 0; i < 5; i++ {
		queue.Emit(envelope(t, "x"))
	}

	var actions []string
	var counts []any
	queue.Close()
	queue.Drain(func(e Envelope) error {
		actions = append(actions, e.Action)
		if e.Action == "events_dropped" {
			counts = append(counts, e.Subject["count"])
		}
		return nil
	}, nil)

	// Две принятые и ровно один отчёт о трёх потерянных; где именно в потоке
	// окажется отчёт, не оговаривается.
	if len(actions) != 3 {
		t.Fatalf("действия: %v", actions)
	}
	if len(counts) != 1 || counts[0] != uint64(3) {
		t.Fatalf("отчёты о потерях: %v, ожидался один с count=3", counts)
	}
}

func TestEmitAfterCloseDoesNotPanic(t *testing.T) {
	queue := NewQueue(1)
	queue.Close()

	queue.Emit(envelope(t, "late"))
}

func TestAppendFailureAndBufferLossAreBothCounted(t *testing.T) {
	queue := NewQueue(10)
	queue.Emit(envelope(t, "fails"))
	queue.Close()

	var reported []Envelope
	lostOnce := uint64(2)
	queue.Drain(func(e Envelope) error {
		if e.Action == "fails" {
			return errors.New("диск полон")
		}
		reported = append(reported, e)
		return nil
	}, func() uint64 { n := lostOnce; lostOnce = 0; return n })

	if len(reported) != 1 || reported[0].Subject["count"] != uint64(3) {
		t.Fatalf("отчёт: %+v", reported)
	}
}

func TestEmitIsSafeFromManyGoroutines(t *testing.T) {
	queue := NewQueue(1000)
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 20; j++ {
				queue.Emit(envelope(t, "p"))
			}
		}()
	}
	wg.Wait()
	queue.Close()

	count := 0
	queue.Drain(func(Envelope) error { count++; return nil }, nil)
	if count != 400 {
		t.Fatalf("доставлено %d из 400", count)
	}
}
```


- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd agent && go test ./internal/events/...`
Expected: FAIL (пакет без исходников / `undefined: NewUUIDv7`).

- [ ] **Step 3: `agent/internal/events/uuid7.go`**

```go
package events

import (
	"crypto/rand"
	"fmt"
	"time"
)

// NewUUIDv7 строит UUID версии 7 (RFC 9562): 48 бит времени в миллисекундах,
// остальное случайное. Монотонность по времени даёт локальность вставки
// в индекс событий на сервере.
func NewUUIDv7(at time.Time) (string, error) {
	var b [16]byte
	millis := uint64(at.UnixMilli())
	for i := 0; i < 6; i++ {
		b[i] = byte(millis >> (8 * (5 - i)))
	}
	if _, err := rand.Read(b[6:]); err != nil {
		return "", fmt.Errorf("источник случайности: %w", err)
	}
	b[6] = b[6]&0x0f | 0x70
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:]), nil
}
```

- [ ] **Step 4: `agent/internal/events/envelope.go`**

```go
// Package events описывает события агента: конверт, очередь и интерфейс
// сборщика. Остальные пакеты о форме события ничего не знают.
package events

import (
	"encoding/json"
	"time"
)

// SchemaVersion — версия конверта. Сервер принимает только известные версии.
const SchemaVersion = 1

// Каналы перехвата. Список совпадает с контрактом.
const (
	ChannelFile      = "file"
	ChannelUSB       = "usb"
	ChannelClipboard = "clipboard"
	ChannelNetwork   = "network"
	ChannelPrint     = "print"
	ChannelProcess   = "process"
	ChannelAgent     = "agent"
)

// Предварительная оценка критичности; окончательную определяет сервер.
const (
	SeverityInfo     = "info"
	SeverityLow      = "low"
	SeverityMedium   = "medium"
	SeverityHigh     = "high"
	SeverityCritical = "critical"
)

// CriticalRank — ранг, который буфер никогда не вытесняет.
const CriticalRank uint8 = 4

// SeverityRank переводит критичность в число для политики вытеснения.
// Неизвестное значение считается наименее важным: лучше потерять событие
// с опечаткой в критичности, чем вытеснить из-за него настоящее.
func SeverityRank(severity string) uint8 {
	switch severity {
	case SeverityLow:
		return 1
	case SeverityMedium:
		return 2
	case SeverityHigh:
		return 3
	case SeverityCritical:
		return CriticalRank
	}
	return 0
}

type Artifact struct {
	SHA256   string `json:"sha256"`
	Size     int64  `json:"size"`
	Uploaded bool   `json:"uploaded"`
}

// Envelope — конверт события, раздел 9 основной спеки.
// agent_id в нём нет намеренно: личность агента определяет сертификат.
type Envelope struct {
	EventID       string         `json:"event_id"`
	SchemaVersion int            `json:"schema_version"`
	OccurredAt    time.Time      `json:"occurred_at"`
	Channel       string         `json:"channel"`
	Action        string         `json:"action"`
	SeverityHint  string         `json:"severity_hint"`
	Actor         map[string]any `json:"actor"`
	Process       map[string]any `json:"process"`
	Subject       map[string]any `json:"subject"`
	Labels        map[string]any `json:"labels"`
	Artifact      *Artifact      `json:"artifact,omitempty"`
}

// NewEnvelope заполняет идентификатор и время и отдаёт событие без актёра и процесса.
func NewEnvelope(channel, action, severity string, subject map[string]any) (Envelope, error) {
	now := time.Now().UTC()
	id, err := NewUUIDv7(now)
	if err != nil {
		return Envelope{}, err
	}
	return Envelope{
		EventID:       id,
		SchemaVersion: SchemaVersion,
		OccurredAt:    now,
		Channel:       channel,
		Action:        action,
		SeverityHint:  severity,
		Subject:       subject,
	}, nil
}

// MarshalLine кодирует событие одной строкой JSON без перевода строки:
// строка становится записью NDJSON как есть.
//
// Пустые карты заменяются на {}. Сервер отвергает null там, где ждёт объект,
// а карта, которую сборщик не заполнил, в Go по умолчанию равна nil.
func (e Envelope) MarshalLine() ([]byte, error) {
	for _, field := range []*map[string]any{&e.Actor, &e.Process, &e.Subject, &e.Labels} {
		if *field == nil {
			*field = map[string]any{}
		}
	}
	return json.Marshal(e)
}
```

- [ ] **Step 5: `agent/internal/events/collector.go`**

```go
package events

import "context"

// Collector — источник событий: файловая система, USB, буфер обмена и так далее.
// Новый перехват реализует этот интерфейс и подключается в main.
type Collector interface {
	Name() string
	// Run работает до отмены контекста. emit не блокируется: переполнение
	// очереди учитывается и сообщается событием agent/events_dropped.
	Run(ctx context.Context, emit func(Envelope)) error
}
```

- [ ] **Step 6: `agent/internal/events/queue.go`**

```go
package events

import (
	"log/slog"
	"sync"
	"sync/atomic"
)

// Queue отделяет сборщиков от записи на диск: сборщик не должен ждать fsync
// буфера. Ёмкость конечна, при переполнении событие считается потерянным.
type Queue struct {
	ch      chan Envelope
	dropped atomic.Uint64

	mu     sync.RWMutex
	closed bool
}

func NewQueue(capacity int) *Queue {
	return &Queue{ch: make(chan Envelope, capacity)}
}

// Emit не блокируется. Блокировка на чтении защищает от отправки в закрытый
// канал: опоздавший сборщик получает потерянное событие, а не панику.
func (q *Queue) Emit(env Envelope) {
	q.mu.RLock()
	defer q.mu.RUnlock()
	if q.closed {
		q.dropped.Add(1)
		return
	}
	select {
	case q.ch <- env:
	default:
		q.dropped.Add(1)
	}
}

// Close останавливает приём; Drain доработает остаток и вернётся.
func (q *Queue) Close() {
	q.mu.Lock()
	defer q.mu.Unlock()
	if !q.closed {
		q.closed = true
		close(q.ch)
	}
}

// Drain передаёт события приёмнику, пока очередь не закрыта и не пуста.
// lost возвращает число событий, потерянных самим приёмником (вытеснение,
// истечение срока); может быть nil.
//
// Потери не молчат: после каждого события в приёмник добавляется
// agent/events_dropped с количеством. Если и он не помещается, остаётся
// запись в журнале — повторно потери не пересчитываются, иначе при
// полном буфере получился бы бесконечный цикл отчётов.
func (q *Queue) Drain(appendEvent func(Envelope) error, lost func() uint64) {
	for env := range q.ch {
		if err := appendEvent(env); err != nil {
			slog.Warn("событие не записано в буфер", "event_id", env.EventID, "error", err)
			q.dropped.Add(1)
		}
		q.reportLoss(appendEvent, lost)
	}
	q.reportLoss(appendEvent, lost)
}

func (q *Queue) reportLoss(appendEvent func(Envelope) error, lost func() uint64) {
	count := q.dropped.Swap(0)
	if lost != nil {
		count += lost()
	}
	if count == 0 {
		return
	}
	report, err := NewEnvelope(ChannelAgent, "events_dropped", SeverityMedium, map[string]any{
		"component": "buffer",
		"detail":    "события потеряны: очередь или буфер переполнены, либо истёк срок",
		"count":     count,
	})
	if err != nil {
		slog.Error("отчёт о потерях не создан", "error", err)
		return
	}
	if err := appendEvent(report); err != nil {
		slog.Warn("отчёт о потерях не записан", "count", count, "error", err)
	}
}
```

- [ ] **Step 7: Запустить тесты**

Run: `cd agent && go test ./internal/events/... -race`
Expected: PASS. (`-race` на Windows требует cgo/gcc; если недоступен — запустите без `-race`.)

- [ ] **Step 8: Проверка и коммит**

```bash
cd agent && go vet ./internal/events/... && gofmt -l internal/events
git add agent/internal/events
git commit -m "feat(agent): event envelope, UUIDv7, non-blocking queue and collector interface" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Expected: `gofmt -l` ничего не выводит.

---

### Task 5: Транспорт `SendEvents` и сверка контракта

**Files:**
- Modify: `agent/internal/transport/types.go`, `agent/internal/transport/methods.go`
- Test: `agent/internal/transport/methods_test.go`, `agent/internal/transport/contract_test.go`

**Interfaces:**
- Consumes: `events.Envelope` (задача 4), схемы `EventEnvelope`, `EventsResult` в `api/gateway-v1.yaml` (задача 3).
- Produces: `type RejectedEvent struct{ Line int; Reason string }`; `type EventsResult struct{ Accepted, Duplicates int; Rejected []RejectedEvent }`; `func (c *Client) SendEvents(ctx context.Context, ndjson []byte) (EventsResult, error)`.

- [ ] **Step 1: Падающие тесты**

В `agent/internal/transport/methods_test.go` добавить. Тесты пакета поднимают сервер помощниками `newTestCA` и `newTLSServer` (из `testca_test.go`, `client_test.go`); общий помощник `newTestClient` вводится здесь же:

```go
func newTestClient(t *testing.T, handler http.Handler) *transport.Client {
	t.Helper()
	ca := newTestCA(t)
	server := newTLSServer(t, ca, handler)
	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}
	return client
}

func TestSendEventsPostsNDJSONAndParsesResult(t *testing.T) {
	var gotType string
	var gotBody []byte
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotType = r.Header.Get("Content-Type")
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"accepted":2,"duplicates":1,"rejected":[{"line":3,"reason":"invalid_event"}]}`))
	}))

	result, err := client.SendEvents(context.Background(), []byte("{\"a\":1}\n{\"b\":2}\n"))
	if err != nil {
		t.Fatalf("SendEvents: %v", err)
	}

	if gotType != "application/x-ndjson" {
		t.Errorf("Content-Type = %q", gotType)
	}
	if string(gotBody) != "{\"a\":1}\n{\"b\":2}\n" {
		t.Errorf("тело = %q", gotBody)
	}
	if result.Accepted != 2 || result.Duplicates != 1 ||
		len(result.Rejected) != 1 || result.Rejected[0].Line != 3 || result.Rejected[0].Reason != "invalid_event" {
		t.Errorf("результат: %+v", result)
	}
}

func TestSendEventsSurfacesStatusCode(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "too big", http.StatusRequestEntityTooLarge)
	}))

	_, err := client.SendEvents(context.Background(), []byte("{}\n"))

	var statusErr *transport.StatusError
	if !errors.As(err, &statusErr) || statusErr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("ошибка = %v, ожидался StatusError 413", err)
	}
}
```

(добавить в импорты теста `errors` и `io`.)

В `contract_test.go` в оба списка `cases` добавить:

```go
		{"EventEnvelope", events.Envelope{}},
		{"EventsResult", transport.EventsResult{}},
```

и импорт `"github.com/barysguard/agent/internal/events"`.

Run: `cd agent && go test ./internal/transport/...`
Expected: FAIL (`client.SendEvents undefined`).

- [ ] **Step 2: Типы в `types.go`**

```go
// RejectedEvent — строка пакета, которую сервер не принял. Повторять её
// бессмысленно: такое событие не станет верным от повторной отправки.
type RejectedEvent struct {
	Line   int    `json:"line"`
	Reason string `json:"reason"`
}

type EventsResult struct {
	Accepted   int             `json:"accepted"`
	Duplicates int             `json:"duplicates"`
	Rejected   []RejectedEvent `json:"rejected"`
}
```

И заменить комментарий над `HeartbeatRequest`: теперь поля буфера заполняет агент, а не «отправляются нули».

- [ ] **Step 3: Метод в `methods.go`**

Добавить функцию (импорты `bytes`, `encoding/json`, `fmt`, `net/http` в файле уже есть):

```go
// SendEvents отправляет пакет событий (NDJSON, по строке на событие).
//
// Отдельно от request: тело — не JSON, а готовые строки, и Content-Type другой.
func (c *Client) SendEvents(ctx context.Context, ndjson []byte) (EventsResult, error) {
	req, err := http.NewRequest(
		http.MethodPost,
		c.base.JoinPath("gateway", "v1", "events").String(),
		bytes.NewReader(ndjson),
	)
	if err != nil {
		return EventsResult{}, err
	}
	req.Header.Set("Content-Type", "application/x-ndjson")

	resp, err := c.do(ctx, req)
	if err != nil {
		return EventsResult{}, err
	}
	defer resp.Body.Close()

	var out EventsResult
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return EventsResult{}, fmt.Errorf("разбор ответа приёма событий: %w", err)
	}
	return out, nil
}
```

- [ ] **Step 4: Запустить тесты**

Run: `cd agent && go test ./internal/transport/...`
Expected: PASS, включая сверку с `api/gateway-v1.yaml` (схемы `EventEnvelope`, `EventsResult` должны быть в контракте после задачи 3).

- [ ] **Step 5: Коммит**

```bash
cd agent && go vet ./internal/transport/... && gofmt -l internal/transport
git add agent/internal/transport
git commit -m "feat(agent): SendEvents transport method and contract checks for events" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Шифрованный offline-буфер

**Files:**
- Modify: `agent/go.mod`, `agent/go.sum`, `agent/internal/config/layout.go`
- Create: `agent/internal/buffer/buffer.go`, `key.go`, `limits.go`
- Test: `agent/internal/buffer/buffer_test.go`, `key_test.go`, `limits_test.go`

**Interfaces:**
- Consumes: `events.Envelope`, `events.SeverityRank`, `events.CriticalRank` (задача 4); `config.Layout`, `platform.Guard`, `config.WriteAtomic`.
- Produces:
  - `type Options struct{ Path string; Key []byte; MaxBytes int64; MaxAge time.Duration; Now func() time.Time }`; `func Open(Options) (*Buffer, error)`;
  - `(*Buffer).Append(events.Envelope) error` (возвращает `ErrFull`); `NextBatch(maxEvents, maxBytes int) (Batch, error)`; `Ack(Batch) error`; `Stats() (count int, bytes int64)`; `TakeLost() uint64`; `Close() error`;
  - `type Batch struct{ Lines [][]byte; Bytes int }`, `(Batch).Len() int`, `(Batch).NDJSON() []byte`;
  - `var ErrFull`;
  - `type Limits struct{ MaxBytes int64; MaxAge time.Duration }`, `DefaultLimits()`, `LimitsFromDocument(map[string]any) Limits`;
  - `func LoadOrCreateKey(layout config.Layout, guard platform.Guard) (key []byte, created bool, err error)`;
  - `func OpenAt(layout config.Layout, guard platform.Guard, limits Limits) (*Buffer, bool, error)` — второе значение `true`, если буфер пересоздан из-за потери ключа;
  - `config.Layout.BufferPath()` (`<dir>/events.db`), `config.Layout.BufferKeyPath()` (`<dir>/buffer.key`).

- [ ] **Step 1: Зависимость и раскладка**

```bash
cd agent && go get go.etcd.io/bbolt@v1.3.11 && git diff go.mod
```

Убедиться, что директива `go 1.23` в `go.mod` не изменилась (если поднялась — вернуть руками: образ агента собирается на `golang:1.23`).

В `agent/internal/config/layout.go` рядом с остальными путями:

```go
// Буфер событий и его ключ лежат вне каталога pki: ключ буфера не
// удостоверяет личность агента, и подмена сертификата его не затрагивает.
func (l Layout) BufferPath() string    { return filepath.Join(l.Dir, "events.db") }
func (l Layout) BufferKeyPath() string { return filepath.Join(l.Dir, "buffer.key") }
```

- [ ] **Step 2: Падающие тесты буфера `agent/internal/buffer/buffer_test.go`**

```go
package buffer_test

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	bolt "go.etcd.io/bbolt"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/events"
)

var fixedNow = time.Date(2026, 10, 5, 10, 0, 0, 0, time.UTC)

func testKey(fill byte) []byte { return bytes.Repeat([]byte{fill}, 32) }

func open(t *testing.T, mutate func(*buffer.Options)) (*buffer.Buffer, buffer.Options) {
	t.Helper()
	options := buffer.Options{
		Path:     filepath.Join(t.TempDir(), "events.db"),
		Key:      testKey(1),
		MaxBytes: 1 << 30,
		MaxAge:   7 * 24 * time.Hour,
		Now:      func() time.Time { return fixedNow },
	}
	if mutate != nil {
		mutate(&options)
	}
	buf, err := buffer.Open(options)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { buf.Close() })
	return buf, options
}

// event строит событие с фиксированным временем: размер записи тогда не зависит
// от формата дробной части секунд, и тесты лимитов остаются детерминированными.
func event(t *testing.T, action, severity string) events.Envelope {
	t.Helper()
	env, err := events.NewEnvelope(events.ChannelAgent, action, severity, map[string]any{"detail": "x"})
	if err != nil {
		t.Fatal(err)
	}
	env.OccurredAt = fixedNow
	return env
}

func mustAppend(t *testing.T, buf *buffer.Buffer, env events.Envelope) {
	t.Helper()
	if err := buf.Append(env); err != nil {
		t.Fatalf("Append(%s): %v", env.Action, err)
	}
}

func actions(t *testing.T, batch buffer.Batch) []string {
	t.Helper()
	var out []string
	for _, line := range batch.Lines {
		s := string(line)
		i := strings.Index(s, `"action":"`) + len(`"action":"`)
		out = append(out, s[i:i+strings.Index(s[i:], `"`)])
	}
	return out
}

func TestBatchesComeOutInFIFOOrderAndAckRemovesThem(t *testing.T) {
	buf, _ := open(t, nil)
	for _, name := range []string{"a", "b", "c"} {
		mustAppend(t, buf, event(t, name, events.SeverityInfo))
	}

	batch, err := buf.NextBatch(2, 1<<20)
	if err != nil {
		t.Fatalf("NextBatch: %v", err)
	}
	if got := actions(t, batch); len(got) != 2 || got[0] != "a" || got[1] != "b" {
		t.Fatalf("первый пакет: %v", got)
	}

	// До Ack запись не удалена: обрыв связи не должен её терять.
	if count, _ := buf.Stats(); count != 3 {
		t.Fatalf("до Ack в буфере %d, ожидалось 3", count)
	}
	if err := buf.Ack(batch); err != nil {
		t.Fatalf("Ack: %v", err)
	}
	if count, _ := buf.Stats(); count != 1 {
		t.Fatalf("после Ack в буфере %d, ожидалась 1", count)
	}

	next, _ := buf.NextBatch(10, 1<<20)
	if got := actions(t, next); len(got) != 1 || got[0] != "c" {
		t.Fatalf("второй пакет: %v", got)
	}
}

func TestBatchRespectsByteLimitButAlwaysTakesOneEvent(t *testing.T) {
	buf, _ := open(t, nil)
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))

	batch, _ := buf.NextBatch(10, 10) // меньше размера любого события

	if batch.Len() != 1 {
		t.Fatalf("в пакете %d событий, ожидалось ровно одно", batch.Len())
	}
}

func TestBatchNDJSONHasOneLinePerEvent(t *testing.T) {
	buf, _ := open(t, nil)
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))

	batch, _ := buf.NextBatch(10, 1<<20)
	body := batch.NDJSON()

	if bytes.Count(body, []byte("\n")) != 2 || !bytes.HasSuffix(body, []byte("\n")) {
		t.Fatalf("тело: %q", body)
	}
	if batch.Bytes != len(body) {
		t.Fatalf("Bytes = %d, длина тела %d", batch.Bytes, len(body))
	}
}

func TestEventsSurviveReopen(t *testing.T) {
	buf, options := open(t, nil)
	mustAppend(t, buf, event(t, "persisted", events.SeverityInfo))
	buf.Close()

	again, err := buffer.Open(options)
	if err != nil {
		t.Fatalf("Open повторно: %v", err)
	}
	defer again.Close()

	if count, size := again.Stats(); count != 1 || size == 0 {
		t.Fatalf("после перезапуска count=%d bytes=%d", count, size)
	}
}

func TestRecordsAreEncryptedAtRest(t *testing.T) {
	buf, options := open(t, nil)
	env := event(t, "marker", events.SeverityInfo)
	env.Subject = map[string]any{"path": "СЕКРЕТНЫЙ-ДОКУМЕНТ-12345.docx"}
	mustAppend(t, buf, env)
	buf.Close()

	raw, err := os.ReadFile(options.Path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("СЕКРЕТНЫЙ")) || bytes.Contains(raw, []byte("marker")) {
		t.Fatal("содержимое события читается в файле буфера")
	}
}

func TestTamperedRecordIsDroppedAndCountedAsLost(t *testing.T) {
	buf, options := open(t, nil)
	mustAppend(t, buf, event(t, "good-1", events.SeverityInfo))
	mustAppend(t, buf, event(t, "bad", events.SeverityInfo))
	mustAppend(t, buf, event(t, "good-2", events.SeverityInfo))
	buf.Close()

	// Подмена одного байта шифротекста второй записи.
	db, err := bolt.Open(options.Path, 0o600, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket([]byte("events"))
		cursor := bucket.Cursor()
		cursor.First()
		key, value := cursor.Next()
		changed := append([]byte(nil), value...)
		changed[len(changed)-1] ^= 0xff
		return bucket.Put(key, changed)
	}); err != nil {
		t.Fatal(err)
	}
	db.Close()

	again, err := buffer.Open(options)
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()

	batch, err := again.NextBatch(10, 1<<20)
	if err != nil {
		t.Fatalf("NextBatch: %v", err)
	}
	if got := actions(t, batch); len(got) != 2 || got[0] != "good-1" || got[1] != "good-2" {
		t.Fatalf("после подмены: %v", got)
	}
	if lost := again.TakeLost(); lost != 1 {
		t.Fatalf("TakeLost = %d, ожидалась 1", lost)
	}
	if again.TakeLost() != 0 {
		t.Fatal("TakeLost не сбросил счётчик")
	}
}

func TestWrongKeyDropsEverythingInsteadOfBlocking(t *testing.T) {
	buf, options := open(t, nil)
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))
	buf.Close()

	options.Key = testKey(2)
	other, err := buffer.Open(options)
	if err != nil {
		t.Fatal(err)
	}
	defer other.Close()

	batch, err := other.NextBatch(10, 1<<20)
	if err != nil || batch.Len() != 0 {
		t.Fatalf("batch=%d err=%v, ожидался пустой пакет", batch.Len(), err)
	}
	if other.TakeLost() != 2 {
		t.Fatal("потеряно не 2 события")
	}
}

// recordSize измеряет, сколько места занимает одно событие в буфере. Длина action
// меняет размер записи на единицы байт из сотен; запас size/2 в тестах лимитов
// эту разницу переживает.
func recordSize(t *testing.T) int64 {
	buf, _ := open(t, nil)
	mustAppend(t, buf, event(t, "m", events.SeverityInfo))
	_, size := buf.Stats()
	return size
}

func TestFullBufferEvictsOldestLowestSeverityFirst(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 3*size + size/2 })
	mustAppend(t, buf, event(t, "old-info", events.SeverityInfo))
	mustAppend(t, buf, event(t, "high", events.SeverityHigh))
	mustAppend(t, buf, event(t, "new-info", events.SeverityInfo))

	mustAppend(t, buf, event(t, "critical", events.SeverityCritical))

	batch, _ := buf.NextBatch(10, 1<<20)
	got := actions(t, batch)
	if len(got) != 3 || got[0] != "high" || got[1] != "new-info" || got[2] != "critical" {
		t.Fatalf("осталось %v, ожидалось [high new-info critical]", got)
	}
	if buf.TakeLost() != 1 {
		t.Fatal("вытеснение не учтено как потеря")
	}
}

func TestCriticalEventsAreNeverEvicted(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 2*size + size/2 })
	mustAppend(t, buf, event(t, "c1", events.SeverityCritical))
	mustAppend(t, buf, event(t, "c2", events.SeverityCritical))

	for _, severity := range []string{events.SeverityCritical, events.SeverityInfo} {
		if err := buf.Append(event(t, "overflow", severity)); !errors.Is(err, buffer.ErrFull) {
			t.Fatalf("Append(%s) = %v, ожидался ErrFull", severity, err)
		}
	}

	batch, _ := buf.NextBatch(10, 1<<20)
	if got := actions(t, batch); len(got) != 2 || got[0] != "c1" || got[1] != "c2" {
		t.Fatalf("critical потеряны: %v", got)
	}
	if count, _ := buf.Stats(); count != 2 {
		t.Fatalf("счётчик = %d после отказов", count)
	}
}

func TestLowSeverityNeverDisplacesMoreImportantEvents(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 2*size + size/2 })
	mustAppend(t, buf, event(t, "h1", events.SeverityHigh))
	mustAppend(t, buf, event(t, "h2", events.SeverityHigh))

	if err := buf.Append(event(t, "noise", events.SeverityInfo)); !errors.Is(err, buffer.ErrFull) {
		t.Fatalf("Append(info) = %v, ожидался ErrFull", err)
	}
}

func TestOldEventsExpireExceptCritical(t *testing.T) {
	now := fixedNow
	buf, _ := open(t, func(o *buffer.Options) {
		o.MaxAge = time.Hour
		o.Now = func() time.Time { return now }
	})
	mustAppend(t, buf, event(t, "stale-info", events.SeverityInfo))
	mustAppend(t, buf, event(t, "stale-critical", events.SeverityCritical))

	now = fixedNow.Add(2 * time.Hour)
	mustAppend(t, buf, event(t, "fresh", events.SeverityInfo))

	batch, _ := buf.NextBatch(10, 1<<20)
	got := actions(t, batch)
	if len(got) != 2 || got[0] != "stale-critical" || got[1] != "fresh" {
		t.Fatalf("осталось %v", got)
	}
	if buf.TakeLost() != 1 {
		t.Fatal("истёкшее событие не учтено как потеря")
	}
}

func TestAckAfterConcurrentEvictionKeepsCountersConsistent(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 2*size + size/2 })
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))
	batch, _ := buf.NextBatch(10, 1<<20)

	// Пока пакет «в полёте», сборщик вытесняет его первое событие.
	mustAppend(t, buf, event(t, "c", events.SeverityCritical))
	if err := buf.Ack(batch); err != nil {
		t.Fatalf("Ack: %v", err)
	}

	count, bytesLeft := buf.Stats()
	if count != 1 || bytesLeft <= 0 {
		t.Fatalf("count=%d bytes=%d, ожидалась ровно одна запись", count, bytesLeft)
	}
}
```

- [ ] **Step 3: Падающие тесты ключа и лимитов**

`agent/internal/buffer/key_test.go`:

```go
package buffer_test

import (
	"bytes"
	"os"
	"testing"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/platform"
)

func TestKeyIsCreatedOnceAndReused(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	first, created, err := buffer.LoadOrCreateKey(layout, guard)
	if err != nil || !created || len(first) != 32 {
		t.Fatalf("первый вызов: key=%d created=%v err=%v", len(first), created, err)
	}

	second, created, err := buffer.LoadOrCreateKey(layout, guard)
	if err != nil || created || !bytes.Equal(first, second) {
		t.Fatalf("второй вызов: created=%v err=%v равны=%v", created, err, bytes.Equal(first, second))
	}
}

func TestCorruptKeyFileIsTreatedAsLostKey(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if _, _, err := buffer.LoadOrCreateKey(layout, guard); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(layout.BufferKeyPath(), []byte("не ключ"), 0o600); err != nil {
		t.Fatal(err)
	}

	key, created, err := buffer.LoadOrCreateKey(layout, guard)

	if err != nil || !created || len(key) != 32 {
		t.Fatalf("created=%v len=%d err=%v", created, len(key), err)
	}
}

func TestOpenAtResetsBufferWhenKeyIsLost(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	first, reset, err := buffer.OpenAt(layout, guard, buffer.DefaultLimits())
	if err != nil || reset {
		t.Fatalf("первый запуск: reset=%v err=%v", reset, err)
	}
	env, _ := events.NewEnvelope(events.ChannelAgent, "start", events.SeverityInfo, nil)
	if err := first.Append(env); err != nil {
		t.Fatal(err)
	}
	first.Close()

	if err := os.Remove(layout.BufferKeyPath()); err != nil {
		t.Fatal(err)
	}

	second, reset, err := buffer.OpenAt(layout, guard, buffer.DefaultLimits())
	if err != nil {
		t.Fatalf("OpenAt после потери ключа: %v", err)
	}
	defer second.Close()

	if !reset {
		t.Fatal("потеря ключа не сообщена")
	}
	if count, _ := second.Stats(); count != 0 {
		t.Fatalf("буфер не пуст: %d", count)
	}
}
```

`agent/internal/buffer/limits_test.go`:

```go
package buffer_test

import (
	"testing"
	"time"

	"github.com/barysguard/agent/internal/buffer"
)

func TestLimitsDefaultToSpecValues(t *testing.T) {
	limits := buffer.LimitsFromDocument(nil)

	if limits.MaxBytes != 500*1024*1024 || limits.MaxAge != 7*24*time.Hour {
		t.Fatalf("по умолчанию: %+v", limits)
	}
}

func TestLimitsAreReadFromConfigDocument(t *testing.T) {
	document := map[string]any{"buffer": map[string]any{"max_bytes": float64(2_000_000), "max_age_days": float64(2)}}

	limits := buffer.LimitsFromDocument(document)

	if limits.MaxBytes != 2_000_000 || limits.MaxAge != 48*time.Hour {
		t.Fatalf("из документа: %+v", limits)
	}
}

func TestNonsenseInDocumentFallsBackToDefaults(t *testing.T) {
	document := map[string]any{"buffer": map[string]any{"max_bytes": "много", "max_age_days": float64(-1)}}

	limits := buffer.LimitsFromDocument(document)

	if limits != buffer.DefaultLimits() {
		t.Fatalf("мусор в документе: %+v", limits)
	}
}
```

Run: `cd agent && go test ./internal/buffer/...`
Expected: FAIL (пакет без исходников).

- [ ] **Step 4: `agent/internal/buffer/limits.go`**

```go
package buffer

import "time"

// Limits — пределы буфера из раздела 10 основной спеки.
type Limits struct {
	MaxBytes int64
	MaxAge   time.Duration
}

func DefaultLimits() Limits {
	return Limits{MaxBytes: 500 * 1024 * 1024, MaxAge: 7 * 24 * time.Hour}
}

// LimitsFromDocument читает пределы из документа конфигурации агента
// (раздел buffer). Отсутствующее или бессмысленное значение заменяется
// умолчанием: плохой документ не должен оставлять агента без буфера.
func LimitsFromDocument(document map[string]any) Limits {
	limits := DefaultLimits()
	section, _ := document["buffer"].(map[string]any)

	if value, ok := section["max_bytes"].(float64); ok && value > 0 {
		limits.MaxBytes = int64(value)
	}
	if value, ok := section["max_age_days"].(float64); ok && value > 0 {
		limits.MaxAge = time.Duration(value * float64(24*time.Hour))
	}
	return limits
}
```

- [ ] **Step 5: `agent/internal/buffer/key.go`**

```go
package buffer

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
)

const keySize = 32

// LoadOrCreateKey отдаёт ключ шифрования буфера. created = true означает,
// что прежнего ключа нет или он нечитаем, и вместо него создан новый:
// записи, зашифрованные старым ключом, прочитать уже нельзя.
//
// Ключ защищён так же, как ключ сертификата агента: права на файл
// выставляет и проверяет platform.Guard.
func LoadOrCreateKey(layout config.Layout, guard platform.Guard) ([]byte, bool, error) {
	path := layout.BufferKeyPath()

	if _, err := os.Stat(path); err == nil {
		if err := guard.VerifySecure(path); err != nil {
			return nil, false, err
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, false, err
		}
		key, err := hex.DecodeString(strings.TrimSpace(string(raw)))
		if err == nil && len(key) == keySize {
			return key, false, nil
		}
		// Испорченный файл равносилен потерянному ключу.
	} else if !errors.Is(err, fs.ErrNotExist) {
		return nil, false, err
	}

	key := make([]byte, keySize)
	if _, err := rand.Read(key); err != nil {
		return nil, false, fmt.Errorf("источник случайности: %w", err)
	}
	if err := config.WriteAtomic(path, []byte(hex.EncodeToString(key)), guard); err != nil {
		return nil, false, fmt.Errorf("запись ключа буфера: %w", err)
	}
	return key, true, nil
}

// OpenAt открывает буфер в рабочем каталоге агента. Второе значение true,
// если прежний буфер уничтожен из-за потери ключа: без ключа его содержимое
// не прочитать, а держать нечитаемый файл значит копить мусор на диске.
func OpenAt(layout config.Layout, guard platform.Guard, limits Limits) (*Buffer, bool, error) {
	key, created, err := LoadOrCreateKey(layout, guard)
	if err != nil {
		return nil, false, err
	}

	reset := false
	if created {
		if err := os.Remove(layout.BufferPath()); err == nil {
			reset = true
		} else if !errors.Is(err, fs.ErrNotExist) {
			return nil, false, err
		}
	}

	if err := guard.SecureDir(layout.Dir); err != nil {
		return nil, false, err
	}
	buf, err := Open(Options{
		Path:     layout.BufferPath(),
		Key:      key,
		MaxBytes: limits.MaxBytes,
		MaxAge:   limits.MaxAge,
		Now:      time.Now,
	})
	if err != nil {
		return nil, false, err
	}
	if err := guard.SecureFile(layout.BufferPath()); err != nil {
		buf.Close()
		return nil, false, err
	}
	return buf, reset, nil
}
```

- [ ] **Step 6: `agent/internal/buffer/buffer.go`**

```go
// Package buffer — шифрованный offline-буфер событий (раздел 10 основной спеки).
//
// Событие сначала попадает сюда и только потом уходит на сервер: обратный
// порядок теряет события при обрыве связи. Запись удаляется после подтверждения
// сервера, не раньше.
package buffer

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	bolt "go.etcd.io/bbolt"

	"github.com/barysguard/agent/internal/events"
)

var bucketName = []byte("events")

// ErrFull — в буфере нет места, а вытеснять нечего: остались события, не менее
// важные, чем новое. Новое событие отбрасывается, а не вытесняет их.
var ErrFull = errors.New("буфер заполнен событиями не ниже по критичности")

var errCorrupt = errors.New("запись буфера повреждена")

// Запись: ранг (1) | время записи в мс (8) | nonce (12) | шифротекст.
// Ранг и время лежат открыто, чтобы вытеснение и истечение срока не расшифровывали
// сотни мегабайт; от подмены их защищает AAD — запись с изменённым заголовком
// не пройдёт проверку подлинности.
const (
	headerSize = 9
	nonceSize  = 12
)

type Options struct {
	Path     string
	Key      []byte
	MaxBytes int64
	MaxAge   time.Duration
	// Now подменяется в тестах; при nil берётся time.Now.
	Now func() time.Time
}

type Buffer struct {
	db   *bolt.DB
	aead cipher.AEAD
	opts Options

	mu    sync.Mutex
	count int
	bytes int64
	lost  uint64
}

// Batch — пакет записей, ожидающих подтверждения.
type Batch struct {
	keys [][]byte

	// Lines — строки JSON, готовые к отправке в NDJSON.
	Lines [][]byte
	// Bytes — размер тела NDJSON, включая переводы строк.
	Bytes int
}

func (b Batch) Len() int { return len(b.Lines) }

func (b Batch) NDJSON() []byte {
	var body bytes.Buffer
	for _, line := range b.Lines {
		body.Write(line)
		body.WriteByte('\n')
	}
	return body.Bytes()
}

func Open(opts Options) (*Buffer, error) {
	if len(opts.Key) != keySize {
		return nil, fmt.Errorf("ключ буфера: %d байт, нужно %d", len(opts.Key), keySize)
	}
	if opts.Now == nil {
		opts.Now = time.Now
	}

	block, err := aes.NewCipher(opts.Key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}

	db, err := bolt.Open(opts.Path, 0o600, &bolt.Options{Timeout: time.Second})
	if err != nil {
		return nil, fmt.Errorf("открытие буфера: %w", err)
	}

	b := &Buffer{db: db, aead: aead, opts: opts}
	err = db.Update(func(tx *bolt.Tx) error {
		bucket, err := tx.CreateBucketIfNotExists(bucketName)
		if err != nil {
			return err
		}
		return bucket.ForEach(func(_, value []byte) error {
			b.count++
			b.bytes += int64(len(value))
			return nil
		})
	})
	if err != nil {
		db.Close()
		return nil, err
	}
	return b, nil
}

func (b *Buffer) Close() error { return b.db.Close() }

func aad(key, header []byte) []byte {
	return append(append([]byte(nil), key...), header...)
}

func (b *Buffer) seal(key []byte, rank uint8, at time.Time, line []byte) ([]byte, error) {
	value := make([]byte, headerSize+nonceSize, headerSize+nonceSize+len(line)+b.aead.Overhead())
	value[0] = rank
	binary.BigEndian.PutUint64(value[1:headerSize], uint64(at.UnixMilli()))
	nonce := value[headerSize : headerSize+nonceSize]
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return b.aead.Seal(value, nonce, line, aad(key, value[:headerSize])), nil
}

func (b *Buffer) open(key, value []byte) ([]byte, error) {
	if len(value) < headerSize+nonceSize+b.aead.Overhead() {
		return nil, errCorrupt
	}
	nonce := value[headerSize : headerSize+nonceSize]
	return b.aead.Open(nil, nonce, value[headerSize+nonceSize:], aad(key, value[:headerSize]))
}

func appendedAt(value []byte) time.Time {
	return time.UnixMilli(int64(binary.BigEndian.Uint64(value[1:headerSize])))
}

func copyKey(key []byte) []byte { return append([]byte(nil), key...) }

func deleteKeys(bucket *bolt.Bucket, keys [][]byte) error {
	for _, key := range keys {
		if err := bucket.Delete(key); err != nil {
			return err
		}
	}
	return nil
}

// expire удаляет записи старше MaxAge, кроме critical. Записи упорядочены
// по времени, поэтому обход идёт до первой свежей некритичной.
func (b *Buffer) expire(bucket *bolt.Bucket, now time.Time) (int, int64, error) {
	if b.opts.MaxAge <= 0 {
		return 0, 0, nil
	}
	var keys [][]byte
	var freed int64
	cursor := bucket.Cursor()
	for key, value := cursor.First(); key != nil; key, value = cursor.Next() {
		if len(value) < headerSize {
			continue
		}
		if now.Sub(appendedAt(value)) <= b.opts.MaxAge {
			break
		}
		if value[0] >= events.CriticalRank {
			continue
		}
		keys = append(keys, copyKey(key))
		freed += int64(len(value))
	}
	return len(keys), freed, deleteKeys(bucket, keys)
}

// evict освобождает не меньше need байт, удаляя самые старые записи
// наименьшего ранга, но не выше maxRank и никогда не critical.
// Если освободить нужное нельзя, не удаляет ничего и возвращает false.
func evict(bucket *bolt.Bucket, need int64, maxRank uint8) (int, int64, bool, error) {
	var keys [][]byte
	var freed int64
	for rank := uint8(0); rank <= maxRank && rank < events.CriticalRank && freed < need; rank++ {
		cursor := bucket.Cursor()
		for key, value := cursor.First(); key != nil && freed < need; key, value = cursor.Next() {
			if len(value) < headerSize || value[0] != rank {
				continue
			}
			keys = append(keys, copyKey(key))
			freed += int64(len(value))
		}
	}
	if freed < need {
		return 0, 0, false, nil
	}
	return len(keys), freed, true, deleteKeys(bucket, keys)
}

// Append кладёт событие в буфер. При нехватке места вытесняет самые старые
// события низкой критичности; не помещающееся событие отбрасывается с ErrFull.
// Вытесненные и истёкшие записи учитываются в TakeLost.
func (b *Buffer) Append(env events.Envelope) error {
	line, err := env.MarshalLine()
	if err != nil {
		return err
	}
	rank := events.SeverityRank(env.SeverityHint)

	b.mu.Lock()
	defer b.mu.Unlock()
	now := b.opts.Now()

	var removedCount int
	var removedBytes, size int64

	err = b.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(bucketName)

		count, freed, err := b.expire(bucket, now)
		if err != nil {
			return err
		}
		removedCount, removedBytes = count, freed

		seq, err := bucket.NextSequence()
		if err != nil {
			return err
		}
		key := make([]byte, 8)
		binary.BigEndian.PutUint64(key, seq)

		value, err := b.seal(key, rank, now, line)
		if err != nil {
			return err
		}
		size = int64(len(value))

		if b.opts.MaxBytes > 0 {
			if need := b.bytes - removedBytes + size - b.opts.MaxBytes; need > 0 {
				count, freed, ok, err := evict(bucket, need, rank)
				if err != nil {
					return err
				}
				if !ok {
					return ErrFull
				}
				removedCount += count
				removedBytes += freed
			}
		}
		return bucket.Put(key, value)
	})
	if err != nil {
		return err
	}

	b.count += 1 - removedCount
	b.bytes += size - removedBytes
	b.lost += uint64(removedCount)
	return nil
}

// NextBatch отдаёт самые старые записи, не удаляя их. Запись, не прошедшая
// проверку подлинности, удаляется и считается потерянной: повреждённая
// запись в голове очереди иначе блокировала бы отправку всех следующих.
func (b *Buffer) NextBatch(maxEvents, maxBytes int) (Batch, error) {
	b.mu.Lock()
	defer b.mu.Unlock()

	var batch Batch
	var corrupt [][]byte
	var corruptBytes int64

	err := b.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(bucketName)
		cursor := bucket.Cursor()
		for key, value := cursor.First(); key != nil; key, value = cursor.Next() {
			if batch.Len() >= maxEvents {
				break
			}
			line, err := b.open(key, value)
			if err != nil {
				corrupt = append(corrupt, copyKey(key))
				corruptBytes += int64(len(value))
				continue
			}
			// Первое событие берётся всегда: иначе событие больше предела
			// застряло бы в голове очереди навсегда.
			if batch.Len() > 0 && batch.Bytes+len(line)+1 > maxBytes {
				break
			}
			batch.keys = append(batch.keys, copyKey(key))
			batch.Lines = append(batch.Lines, line)
			batch.Bytes += len(line) + 1
		}
		return deleteKeys(bucket, corrupt)
	})
	if err != nil {
		return Batch{}, err
	}

	if len(corrupt) > 0 {
		slog.Warn("записи буфера не прошли проверку и удалены", "count", len(corrupt))
		b.count -= len(corrupt)
		b.bytes -= corruptBytes
		b.lost += uint64(len(corrupt))
	}
	return batch, nil
}

// Ack удаляет подтверждённые записи. Размеры берутся из самой базы, а не из
// пакета: пока пакет был в пути, запись могло вытеснить, и повторное вычитание
// увело бы счётчики в минус.
func (b *Buffer) Ack(batch Batch) error {
	b.mu.Lock()
	defer b.mu.Unlock()

	var removed int
	var freed int64
	err := b.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(bucketName)
		removed, freed = 0, 0
		for _, key := range batch.keys {
			value := bucket.Get(key)
			if value == nil {
				continue
			}
			freed += int64(len(value))
			removed++
			if err := bucket.Delete(key); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return err
	}
	b.count -= removed
	b.bytes -= freed
	return nil
}

func (b *Buffer) Stats() (int, int64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.count, b.bytes
}

// TakeLost возвращает число потерянных с прошлого вызова записей
// (вытеснение, истечение срока, повреждение) и обнуляет счётчик.
func (b *Buffer) TakeLost() uint64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	lost := b.lost
	b.lost = 0
	return lost
}
```

- [ ] **Step 7: Запустить тесты**

Run: `cd agent && go test ./internal/buffer/... ./internal/config/...`
Expected: PASS. Падение теста вытеснения чаще всего означает расхождение размеров записей: проверьте, что `event()` фиксирует `OccurredAt` и что `recordSize` измеряется на тех же полях.

- [ ] **Step 8: Проверка и коммит**

```bash
cd agent && go vet ./... && gofmt -l . && go build ./...
git add agent/go.mod agent/go.sum agent/internal/buffer agent/internal/config/layout.go
git commit -m "feat(agent): encrypted offline event buffer with severity-aware eviction" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Отправка событий в `runner`, сборщик жизненного цикла, сборка агента

**Files:**
- Create: `agent/internal/collectors/lifecycle/lifecycle.go`, `agent/internal/runner/events.go`
- Modify: `agent/internal/runner/agent.go`, `agent/cmd/barysguard-agent/main.go`
- Test: `agent/internal/collectors/lifecycle/lifecycle_test.go`, `agent/internal/runner/events_test.go`

**Interfaces:**
- Consumes: `events.*` (задача 4), `buffer.*` (задача 6), `transport.SendEvents` (задача 5).
- Produces: `lifecycle.New(version string) events.Collector`; `runner.Options{Buffer EventBuffer; Collectors []events.Collector}`; `(*Agent).Emit(events.Envelope)`; `(*Agent).StartEvents(ctx context.Context) (stop func())`; `(*Agent).Close() error`; константа `runner.MaxEventBatchesPerPass = 20`.

- [ ] **Step 1: Падающий тест сборщика `agent/internal/collectors/lifecycle/lifecycle_test.go`**

```go
package lifecycle_test

import (
	"context"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/collectors/lifecycle"
	"github.com/barysguard/agent/internal/events"
)

func TestEmitsStartThenStopOnCancel(t *testing.T) {
	collector := lifecycle.New("1.2.3")
	var got []events.Envelope
	emit := func(e events.Envelope) { got = append(got, e) }

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- collector.Run(ctx, emit) }()

	time.Sleep(50 * time.Millisecond)
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("Run: %v", err)
	}

	if len(got) != 2 || got[0].Action != "start" || got[1].Action != "stop" {
		t.Fatalf("события: %+v", got)
	}
	for _, e := range got {
		if e.Channel != events.ChannelAgent || e.Subject["component"] != "agent" {
			t.Errorf("неверный конверт: %+v", e)
		}
	}
	if got[0].Subject["detail"] != "version 1.2.3" {
		t.Errorf("detail = %v", got[0].Subject["detail"])
	}
	if collector.Name() != "lifecycle" {
		t.Errorf("Name = %q", collector.Name())
	}
}
```

Run: `cd agent && go test ./internal/collectors/...` → FAIL.

- [ ] **Step 2: `agent/internal/collectors/lifecycle/lifecycle.go`**

```go
// Package lifecycle — сборщик канала agent: старт и остановка агента.
package lifecycle

import (
	"context"
	"log/slog"

	"github.com/barysguard/agent/internal/events"
)

type Collector struct{ version string }

func New(version string) *Collector { return &Collector{version: version} }

func (c *Collector) Name() string { return "lifecycle" }

// Run порождает start сразу и stop при отмене контекста. Событие stop
// уходит в очередь, которая закрывается только после возврата всех сборщиков,
// поэтому оно доходит до буфера.
func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	c.emit(emit, "start", "version "+c.version)
	<-ctx.Done()
	c.emit(emit, "stop", "остановка по сигналу")
	return nil
}

func (c *Collector) emit(emit func(events.Envelope), action, detail string) {
	env, err := events.NewEnvelope(events.ChannelAgent, action, events.SeverityInfo, map[string]any{
		"component": "agent",
		"detail":    detail,
	})
	if err != nil {
		slog.Error("событие жизненного цикла не создано", "action", action, "error", err)
		return
	}
	emit(env)
}
```

Run: `cd agent && go test ./internal/collectors/...` → PASS.

- [ ] **Step 3: Падающие тесты отправки `agent/internal/runner/events_test.go`**

Тесты используют помощники `newRunnerCA`, `newRunnerTLSServer` из существующих `testca_test.go`, `testserver_test.go`.

```go
package runner_test

import (
	"bytes"
	"context"
	"encoding/json"
	"math/rand"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

// fakeGateway — шлюз, принимающий heartbeat и события.
type fakeGateway struct {
	mu        sync.Mutex
	down      atomic.Bool
	maxLines  int // при > 0 пакеты длиннее отвергаются кодом 413
	rejectAll bool
	ids       map[string]int
	requests  int
	heartbeat transport.HeartbeatRequest
}

func (g *fakeGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/gateway/v1/heartbeat":
		g.mu.Lock()
		json.NewDecoder(r.Body).Decode(&g.heartbeat)
		g.mu.Unlock()
		json.NewEncoder(w).Encode(transport.HeartbeatResponse{
			ServerTime: time.Now().UTC(), ConfigVersion: 0, HeartbeatIntervalSeconds: 30,
			Commands: []transport.QueuedCommand{},
		})
	case "/gateway/v1/events":
		if g.down.Load() {
			http.Error(w, "недоступен", http.StatusServiceUnavailable)
			return
		}
		raw := new(bytes.Buffer)
		raw.ReadFrom(r.Body)
		lines := strings.Split(strings.TrimSpace(raw.String()), "\n")
		if g.maxLines > 0 && len(lines) > g.maxLines {
			http.Error(w, "велик", http.StatusRequestEntityTooLarge)
			return
		}
		g.mu.Lock()
		g.requests++
		result := transport.EventsResult{Rejected: []transport.RejectedEvent{}}
		for i, line := range lines {
			if g.rejectAll {
				result.Rejected = append(result.Rejected, transport.RejectedEvent{Line: i + 1, Reason: "invalid_event"})
				continue
			}
			var env events.Envelope
			json.Unmarshal([]byte(line), &env)
			if g.ids[env.EventID] > 0 {
				result.Duplicates++
			} else {
				result.Accepted++
			}
			g.ids[env.EventID]++
		}
		g.mu.Unlock()
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(result)
	}
}

func (g *fakeGateway) received() map[string]int {
	g.mu.Lock()
	defer g.mu.Unlock()
	copyOf := map[string]int{}
	for k, v := range g.ids {
		copyOf[k] = v
	}
	return copyOf
}

// newEventAgent собирает агента с настоящим буфером поверх fakeGateway.
func newEventAgent(t *testing.T, gateway *fakeGateway, state config.State) (*runner.Agent, *buffer.Buffer) {
	t.Helper()
	gateway.ids = map[string]int{}

	ca := newRunnerCA(t)
	server := newRunnerTLSServer(t, ca, gateway)
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if err := config.SaveState(layout, state, guard); err != nil {
		t.Fatalf("SaveState: %v", err)
	}
	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}

	buf, err := buffer.Open(buffer.Options{
		Path:     filepath.Join(t.TempDir(), "events.db"),
		Key:      bytes.Repeat([]byte{7}, 32),
		MaxBytes: 1 << 30,
		MaxAge:   time.Hour * 24 * 7,
	})
	if err != nil {
		t.Fatalf("buffer.Open: %v", err)
	}
	t.Cleanup(func() { buf.Close() })

	agent, err := runner.New(runner.Options{
		ServerURL: server.URL, AgentVersion: "0.1.0", Layout: layout, Guard: guard,
		Client: client, Random: rand.NewSource(1), Buffer: buf,
	})
	if err != nil {
		t.Fatalf("runner.New: %v", err)
	}
	return agent, buf
}

func appendEvents(t *testing.T, buf *buffer.Buffer, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		env, err := events.NewEnvelope(events.ChannelAgent, "start", events.SeverityInfo, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err := buf.Append(env); err != nil {
			t.Fatal(err)
		}
	}
}

func TestBufferedEventsAreSentAndAcknowledged(t *testing.T) {
	gateway := &fakeGateway{}
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 3)

	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	if len(gateway.received()) != 3 {
		t.Fatalf("сервер получил %d событий", len(gateway.received()))
	}
	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("в буфере осталось %d после подтверждения", count)
	}
}

func TestHeartbeatReportsBufferState(t *testing.T) {
	gateway := &fakeGateway{}
	gateway.down.Store(true) // события не уйдут, буфер останется
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 2)

	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	gateway.mu.Lock()
	defer gateway.mu.Unlock()
	if gateway.heartbeat.BufferedEvents != 2 || gateway.heartbeat.BufferBytes <= 0 {
		t.Fatalf("heartbeat: %+v", gateway.heartbeat)
	}
}

func TestOutageKeepsEventsAndRecoveryDeliversEachExactlyOnce(t *testing.T) {
	gateway := &fakeGateway{}
	gateway.down.Store(true)
	agent, buf := newEventAgent(t, gateway, config.State{})

	// События идут через очередь, как от настоящего сборщика.
	stop := agent.StartEvents(context.Background())
	for i := 0; i < 25; i++ {
		env, _ := events.NewEnvelope(events.ChannelAgent, "start", events.SeverityInfo, nil)
		agent.Emit(env)
	}
	stop() // очередь доработана: всё в буфере

	for pass := 0; pass < 3; pass++ {
		agent.RunOnce(context.Background())
	}
	if count, _ := buf.Stats(); count != 25 {
		t.Fatalf("во время обрыва в буфере %d, ожидалось 25", count)
	}
	if len(gateway.received()) != 0 {
		t.Fatal("события дошли при недоступном сервере")
	}

	gateway.down.Store(false)
	agent.RunOnce(context.Background())

	received := gateway.received()
	if len(received) != 25 {
		t.Fatalf("после восстановления доставлено %d из 25", len(received))
	}
	for id, times := range received {
		if times != 1 {
			t.Errorf("событие %s доставлено %d раз", id, times)
		}
	}
	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("буфер не пуст: %d", count)
	}
}

func TestRejectedEventsAreAcknowledgedToo(t *testing.T) {
	// Отклонённое событие не станет верным от повторной отправки;
	// иначе оно навсегда заблокировало бы голову очереди.
	gateway := &fakeGateway{rejectAll: true}
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 2)

	agent.RunOnce(context.Background())

	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("отклонённые остались в буфере: %d", count)
	}
}

func TestEntityTooLargeHalvesTheBatch(t *testing.T) {
	gateway := &fakeGateway{maxLines: 2}
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 5)

	agent.RunOnce(context.Background())

	if len(gateway.received()) != 5 {
		t.Fatalf("доставлено %d из 5", len(gateway.received()))
	}
	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("в буфере осталось %d", count)
	}
}

func TestOnePassSendsAtMostTwentyBatches(t *testing.T) {
	gateway := &fakeGateway{}
	state := config.State{Document: map[string]any{
		"transport": map[string]any{"event_batch_max": float64(1)},
	}}
	agent, buf := newEventAgent(t, gateway, state)
	appendEvents(t, buf, 25)

	agent.RunOnce(context.Background())

	if len(gateway.received()) != runner.MaxEventBatchesPerPass {
		t.Fatalf("за проход доставлено %d, предел %d", len(gateway.received()), runner.MaxEventBatchesPerPass)
	}
	if count, _ := buf.Stats(); count != 25-runner.MaxEventBatchesPerPass {
		t.Fatalf("в буфере осталось %d", count)
	}
}
```

Последний тест файла требует от `fakeGateway` ещё две вещи. В структуру добавить поле `configVersion int`, в ответе heartbeat подставлять `ConfigVersion: g.configVersion`, а в `ServeHTTP` добавить ветку:

```go
	case "/gateway/v1/config":
		w.Header().Set("ETag", `"99"`)
		json.NewEncoder(w).Encode(transport.ConfigResponse{Version: 99, Document: map[string]any{}})
```

Сам тест (в конец `events_test.go`):

```go
func TestConfigChangeEmitsConfigAppliedEvent(t *testing.T) {
	gateway := &fakeGateway{configVersion: 99}
	agent, buf := newEventAgent(t, gateway, config.State{ConfigVersion: 1})

	stop := agent.StartEvents(context.Background())
	agent.RunOnce(context.Background()) // версия 99 ≠ 1: документ забирается
	stop()                              // очередь доработана, config_applied в буфере

	batch, err := buf.NextBatch(10, 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, line := range batch.Lines {
		if strings.Contains(string(line), `"action":"config_applied"`) {
			found = true
		}
	}
	if !found {
		t.Fatalf("config_applied не найден среди %d событий", batch.Len())
	}
}
```

Run: `cd agent && go test ./internal/runner/...`
Expected: FAIL (`runner.Options` не имеет поля `Buffer`, нет `StartEvents`, `Emit`, `MaxEventBatchesPerPass`).

- [ ] **Step 4: `agent/internal/runner/events.go`**

```go
package runner

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"sync"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/transport"
)

// За один проход отправляется не больше стольких пакетов: накопившийся за
// неделю обрыва буфер не должен задерживать heartbeat и команды.
const MaxEventBatchesPerPass = 20

const (
	defaultBatchEvents = 500
	defaultBatchBytes  = 4 * 1024 * 1024
	queueCapacity      = 1024
)

// EventBuffer — то, что runner требует от буфера событий.
type EventBuffer interface {
	Append(events.Envelope) error
	NextBatch(maxEvents, maxBytes int) (buffer.Batch, error)
	Ack(buffer.Batch) error
	Stats() (count int, bytes int64)
	TakeLost() uint64
	Close() error
}

// Emit передаёт событие в очередь. Без буфера события не собираются.
func (a *Agent) Emit(env events.Envelope) {
	if a.options.Buffer == nil {
		return
	}
	a.queue.Emit(env)
}

// StartEvents запускает сборщиков и запись очереди в буфер. Возвращённая
// функция останавливает их в правильном порядке: сначала возвращаются
// сборщики (lifecycle успевает породить stop), затем закрывается очередь
// и дорабатывается её остаток.
func (a *Agent) StartEvents(ctx context.Context) (stop func()) {
	if a.options.Buffer == nil {
		return func() {}
	}
	buf := a.options.Buffer

	var collectors sync.WaitGroup
	for _, collector := range a.options.Collectors {
		collectors.Add(1)
		go func() {
			defer collectors.Done()
			if err := collector.Run(ctx, a.queue.Emit); err != nil {
				slog.Error("сборщик остановился с ошибкой", "collector", collector.Name(), "error", err)
			}
		}()
	}

	drained := make(chan struct{})
	go func() {
		defer close(drained)
		a.queue.Drain(buf.Append, buf.TakeLost)
	}()

	return func() {
		collectors.Wait()
		a.queue.Close()
		<-drained
	}
}

// Close освобождает буфер событий.
func (a *Agent) Close() error {
	if a.options.Buffer == nil {
		return nil
	}
	return a.options.Buffer.Close()
}

func (a *Agent) emitConfigApplied(version int) {
	env, err := events.NewEnvelope(events.ChannelAgent, "config_applied", events.SeverityInfo, map[string]any{
		"component": "agent",
		"detail":    fmt.Sprintf("config_version %d", version),
	})
	if err != nil {
		slog.Error("событие смены конфигурации не создано", "error", err)
		return
	}
	a.Emit(env)
}

// batchLimits берёт пределы пакета из сохранённого документа конфигурации.
func (a *Agent) batchLimits() (int, int) {
	maxEvents, maxBytes := defaultBatchEvents, defaultBatchBytes
	section, _ := a.state.Document["transport"].(map[string]any)

	if value, ok := section["event_batch_max"].(float64); ok && value >= 1 {
		maxEvents = int(value)
	}
	if value, ok := section["event_batch_max_bytes"].(float64); ok && value >= 1 {
		maxBytes = int(value)
	}
	// Сервер ответил 413 на такой размер: пока агент не перезапущен, держимся ниже.
	if a.batchCap > 0 && a.batchCap < maxEvents {
		maxEvents = a.batchCap
	}
	return maxEvents, maxBytes
}

// flushEvents досылает накопленное. Ошибка отправки оставляет пакет в буфере:
// следующий heartbeat повторит попытку.
func (a *Agent) flushEvents(ctx context.Context) {
	buf := a.options.Buffer
	if buf == nil {
		return
	}

	for pass := 0; pass < MaxEventBatchesPerPass; pass++ {
		maxEvents, maxBytes := a.batchLimits()
		batch, err := buf.NextBatch(maxEvents, maxBytes)
		if err != nil {
			slog.Error("чтение буфера событий не удалось", "error", err)
			return
		}
		if batch.Len() == 0 {
			return
		}

		result, err := a.options.Client.SendEvents(ctx, batch.NDJSON())
		if err != nil {
			var statusErr *transport.StatusError
			if errors.As(err, &statusErr) && statusErr.Code == http.StatusRequestEntityTooLarge {
				if batch.Len() == 1 {
					// Одно событие, которое сервер не берёт по размеру, не станет
					// меньше от повторов и заблокировало бы очередь.
					slog.Warn("единичное событие слишком велико и удалено")
					if err := buf.Ack(batch); err != nil {
						slog.Error("подтверждение в буфере не удалось", "error", err)
						return
					}
					continue
				}
				a.batchCap = batch.Len() / 2
				continue
			}
			slog.Warn("события не отправлены", "count", batch.Len(), "error", err)
			return
		}

		for _, rejected := range result.Rejected {
			slog.Warn("сервер отклонил событие", "line", rejected.Line, "reason", rejected.Reason)
		}
		if err := buf.Ack(batch); err != nil {
			slog.Error("подтверждение в буфере не удалось", "error", err)
			return
		}
	}
}
```

- [ ] **Step 5: Правки `agent/internal/runner/agent.go`**

1. Импорт `"github.com/barysguard/agent/internal/events"` (если не подтянется другим файлом пакета — он нужен в `Options`).
2. В `Options` добавить:

```go
	// Buffer — шифрованный offline-буфер. При nil события не собираются
	// и не отправляются.
	Buffer EventBuffer
	// Collectors запускаются в Run и в StartEvents.
	Collectors []events.Collector
```

3. В `Agent` добавить поля `queue *events.Queue` и `batchCap int // верхний предел пакета после ответа 413`.
4. В `New` перед `agent.dispatcher = ...` добавить `agent.queue = events.NewQueue(queueCapacity)` (после создания структуры `agent`).
5. В `RunOnce` заменить заполнение буфера в heartbeat:

```go
	buffered, bufferBytes := 0, int64(0)
	if a.options.Buffer != nil {
		buffered, bufferBytes = a.options.Buffer.Stats()
	}
	response, err := a.options.Client.Heartbeat(ctx, transport.HeartbeatRequest{
		AgentVersion:   a.options.AgentVersion,
		ConfigVersion:  a.state.ConfigVersion,
		SentAt:         a.options.Now().UTC(),
		BufferedEvents: buffered,
		BufferBytes:    int(bufferBytes),
	})
```

(удалить комментарий «Буфера в этом плане нет»), а после цикла команд (перед вычислением `interval`) вызвать `a.flushEvents(ctx)`.
6. В `refreshConfig` и `syncConfig` после успешного `SaveState` вызвать `a.emitConfigApplied(response.Version)`; для `syncConfig` это — перед `return config.SaveState(...)` переписать так:

```go
	if err := config.SaveState(a.options.Layout, a.state, a.options.Guard); err != nil {
		return err
	}
	a.emitConfigApplied(response.Version)
	return nil
```

(в `refreshConfig` — аналогично перед `return response.Version, nil`.)
7. В `Run` первой строкой тела:

```go
	stopEvents := a.StartEvents(ctx)
	defer stopEvents()
```

и обновить комментарий над `HeartbeatRequest` в `transport/types.go` (если не сделано в задаче 5).

- [ ] **Step 6: Правки `agent/cmd/barysguard-agent/main.go`**

Импорты: `"github.com/barysguard/agent/internal/buffer"`, `"github.com/barysguard/agent/internal/collectors/lifecycle"`, `"github.com/barysguard/agent/internal/events"`. В `loadAgent` после создания `client`:

```go
	state, err := config.LoadState(layout)
	if err != nil {
		return nil, layout, fmt.Errorf("состояние: %w", err)
	}
	buf, reset, err := buffer.OpenAt(layout, guard, buffer.LimitsFromDocument(state.Document))
	if err != nil {
		return nil, layout, fmt.Errorf("буфер событий: %w", err)
	}
```

В `runner.Options{...}` добавить `Buffer: buf, Collectors: []events.Collector{lifecycle.New(agentVersion)},`. После `runner.New`:

```go
	if err != nil {
		buf.Close()
		return nil, layout, err
	}
	if reset {
		report, envErr := events.NewEnvelope(events.ChannelAgent, "buffer_reset", events.SeverityHigh, map[string]any{
			"component": "buffer",
			"detail":    "ключ буфера утрачен: прежние несданные события потеряны",
		})
		if envErr == nil {
			agent.Emit(report)
		}
	}
	return agent, layout, nil
```

(вместо `return agent, layout, err`). В `commandRun` после `loadAgent`: `defer agent.Close()`.

- [ ] **Step 7: Запустить тесты агента целиком**

Run: `cd agent && go build ./... && go vet ./... && go test ./...`
Expected: PASS, включая `TestOutageKeepsEventsAndRecoveryDeliversEachExactlyOnce`.

- [ ] **Step 8: Коммит**

```bash
cd agent && gofmt -l .
git add agent
git commit -m "feat(agent): send buffered events, lifecycle collector, wire buffer into the agent" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Стенд, документация и итоговая проверка

**Files:**
- Modify: `scripts/smoke.ps1`, `deploy/stand/README.md`, `docs/QUICKSTART.md`, `docs/superpowers/specs/2026-10-05-event-pipeline-design.md`

- [ ] **Step 1: Шаг в `scripts/smoke.ps1`** — после шага «не меньше пяти агентов активны»:

```powershell
Assert-Step 'агенты прислали события start (канал agent)' {
    # Агент пишет start при запуске и отправляет его на ближайшем heartbeat.
    [void](Wait-Until -TimeoutSec 120 -What 'события start от пяти агентов' -Probe {
            $r = Invoke-Api -Method GET -Url "$base/api/v1/events?channel=agent&action=start&limit=200" -Session $session
            $ids = @{}
            if ($r.Status -eq 200) {
                foreach ($event in @($r.Json.items)) { $ids[$event.agent_id] = $true }
            }
            [pscustomobject]@{ Done = ($ids.Count -ge 5); Note = "агентов с событием start: $($ids.Count) (HTTP $($r.Status))" }
        })
}
```

- [ ] **Step 2: Документация**

- `deploy/stand/README.md`: в перечень проверок `smoke.cmd` добавить строку про события `start` от пяти агентов; в раздел «Из чего состоит» — упомянуть, что агенты ведут шифрованный буфер событий в томе данных (`events.db`, `buffer.key`) и `reset` стирает и его.
- `docs/QUICKSTART.md`: раздел «События»: `POST /gateway/v1/events` (NDJSON, 202, `rejected`), `GET /api/v1/events` с фильтрами (`agent_id`, `channel`, `action`, `severity`, `since`, `until`, `cursor`, `limit`), команда `barysguard-admin ensure-partitions [--months-ahead N]`, предупреждение: сервер, работающий без перезапуска через границу месяца дольше чем на `months_ahead` месяцев, пишет в `events_default` (данные не теряются; раздел создаётся при следующем старте или командой).
- Спека `2026-10-05-event-pipeline-design.md`: статус «реализовано (подпроект 2a)»; в разделе 4.2 заменить «ключ — через `platform.Guard` (DPAPI на Windows, файл `0600` на Linux)» точным описанием: ключ лежит в `buffer.key`, права выставляет и проверяет `platform.Guard` (ACL на Windows, `0600` на Linux), как у ключа сертификата; DPAPI вынесен в открытые вопросы подпроекта 2b. В разделе 4.3 уточнить: при ошибке отправки повтор — на следующем heartbeat. Раздел «Открытые вопросы» дополнить строкой про DPAPI.

- [ ] **Step 3: Полный прогон**

```bash
./test.cmd   # из cmd/PowerShell: все наборы (web, agent, server)
```

Expected: все блоки зелёные. Если запускаете из Git Bash: `powershell -NoProfile -File scripts/test.ps1`.

- [ ] **Step 4: Стенд и smoke**

```powershell
.\stand.cmd up      # пересборка образов сервера и агента, первая сборка долгая
.\smoke.cmd
```

Expected: `smoke.cmd` завершается кодом 0, шаг «агенты прислали события start» зелёный. Если стенд поднят со старыми образами — `.\stand.cmd reset` затем `.\stand.cmd up`.

- [ ] **Step 5: Проверка вручную**

В консоли вызвать `GET http://localhost:8080/api/v1/events?channel=agent` залогиненным администратором (или через DevTools) и убедиться, что приходят события `start` от пяти агентов с `hostname`; `overview.events_24h` ≥ 5.

- [ ] **Step 6: Коммит**

```bash
git add scripts/smoke.ps1 deploy/stand/README.md docs/QUICKSTART.md docs/superpowers/specs/2026-10-05-event-pipeline-design.md
git commit -m "docs: events in smoke check, quickstart and stand readme; mark 2a implemented" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Самопроверка плана

**Покрытие спеки.**
- §2 конверт: задачи 2 (серверная валидация, `agent_id` из сертификата, `schema_version`, каналы, 64 КиБ), 4 (`Envelope`, UUIDv7, `MarshalLine`).
- §3.1 таблица и страховочная партиция: задача 1. §3.2 менеджер партиций (старт и CLI): задачи 1, 3. §3.3 приём (NDJSON, 202/400/413, `rejected`, `ON CONFLICT`): задача 2. §3.4 чтение и `events_24h`: задача 3.
- §4.1 `Collector`, неблокирующий `emit`, `events_dropped`, сборщик `agent`: задачи 4, 7. §4.2 буфер (bbolt, AES-GCM, лимиты, вытеснение, потеря ключа, `buffer_reset`): задачи 6, 7. §4.3 отправка (FIFO, 413 вдвое, 20 пакетов, статистика в heartbeat): задача 7. §4.4 `SendEvents`: задача 5.
- §5 проверка: серверные тесты (2, 3), агентские (4–7), сквозной обрыв связи (7), стенд (8), контракт (3, 5).
- Критерий завершения (стенд, smoke, `test.cmd`): задача 8.

**Отклонения от спеки, зафиксированные в плане:** ключ буфера защищён правами файла через `platform.Guard`, а не DPAPI (как ключ сертификата; DPAPI — открытый вопрос 2b); при сбое отправки повтор на следующем heartbeat, а не через backoff heartbeat. Оба пункта вносятся в спеку на шаге 2 задачи 8.

**Согласованность имён.** `ensure_event_partitions`, `parse_batch`, `store_events`, `EventsResult`/`RejectedLine`, `events.Envelope`, `buffer.Open/OpenAt/Batch`, `runner.Options.Buffer/Collectors`, `Agent.Emit/StartEvents/Close`, `MaxEventBatchesPerPass` используются одинаково во всех задачах.
