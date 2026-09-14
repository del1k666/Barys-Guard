# BarysGuard DLP — план 1B (сервер): конфигурация агентов, heartbeat и очередь команд

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать зарегистрированному агенту возможность сообщать о себе, получать настройки, наследуемые по дереву групп, и забирать команды оператора.

**Architecture:** Конфигурация хранится строками с областью видимости `global` либо `group` и собирается слиянием по цепочке групп от корня к группе агента. Версия конфигурации — детерминированный хеш эффективного документа, поэтому правка корневой строки не пишет ни одной строки в `agents`. Heartbeat отдаёт версию и команды; сам документ забирается отдельным запросом с поддержкой `ETag`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async), asyncpg, Alembic, pydantic v2, pytest + pytest-asyncio + testcontainers.

**Spec:** `docs/superpowers/specs/2026-09-14-agent-config-and-commands-design.md`

## Global Constraints

- Все команды выполняются из каталога `server/`. Интерпретатор — `.venv/Scripts/python.exe` на Windows, `.venv/bin/python` на Linux. Далее в плане пишется `python`.
- Тесты требуют запущенного Docker: PostgreSQL поднимается через testcontainers. Проверить до начала работы: `docker version`.
- Все переменные окружения имеют префикс `BG_`.
- Код и комментарии — на русском, как в существующих модулях. Комментарий объясняет **почему**, а не **что**.
- `ruff check .`, `ruff format --check .` и `mypy barysguard` обязаны быть чистыми перед каждым коммитом.
  Проверка типов охватывает только пакет: тесты под `strict` не аннотированы, и так же поступал план 1A.
- Правило `S108` ruff считает строку `"/tmp"` работой с временным каталогом. В тестовых данных
  политик брать образцы путей вида `/srv/docs`, а не глушить правило.
- Каждое сообщение коммита завершается двумя строками:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MAF1f9G1a37myu2hUGLDRk
  ```
  Далее в плане они не повторяются в каждом блоке — добавлять их нужно всегда.
- Миграции генерируются через `alembic revision --autogenerate`, вручную не пишутся, кроме миграций данных.
- **Перечисления хранятся именами членов в верхнем регистре.** При `native_enum=False` SQLAlchemy пишет в колонку `GLOBAL`, а не `global` — так уже устроены `agent_status` и `user_role`. Любое условие в `CheckConstraint` и в `postgresql_where` обязано сравнивать с именем: `scope = 'GLOBAL'`, `status = 'QUEUED'`. Сравнение со значением молча не сработает: частичный индекс не покроет ни одной строки, а `CHECK` отвергнет все.
- Лицензии зависимостей — только пермиссивные. Новых зависимостей этот план не вводит.

## Структура файлов

| Файл | Ответственность |
|---|---|
| `barysguard/db/models/config.py` (создать) | Таблица `agent_configs`, перечисление `ConfigScope` |
| `barysguard/db/models/command.py` (создать) | Таблица `commands`, перечисления `CommandType` и `CommandStatus` |
| `barysguard/services/config.py` (создать) | Форма документа, слияние, версия-хеш, сборка эффективного конфига |
| `barysguard/services/commands.py` (создать) | Постановка в очередь, выдача, истечение, приём результата |
| `barysguard/gateway/schemas.py` (изменить) | Схемы heartbeat, конфига и результата команды |
| `barysguard/gateway/router.py` (изменить) | Три новых эндпоинта, правка `enroll` |
| `barysguard/api/schemas.py` (изменить) | Схемы операторского API |
| `barysguard/api/router.py` (изменить) | Операторские эндпоинты конфигурации и команд |
| `barysguard/db/models/__init__.py` (изменить) | Регистрация моделей для Alembic |
| `tests/conftest.py` (изменить) | Очистка новых таблиц между тестами |

Разделение сервисов и маршрутизаторов повторяет сложившееся в 1A: обработчик занимается HTTP, вся логика — в `services/`, потому что её нужно тестировать без клиента.

---

## Задача 1: Модель конфигурации, слияние и версия

Сервисный слой конфигурации целиком: форма документа, рекурсивное слияние, хеш-версия и сборка по цепочке групп. Эндпоинтов здесь нет — они в задаче 2.

**Files:**
- Create: `server/barysguard/db/models/config.py`
- Create: `server/barysguard/services/config.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Modify: `server/barysguard/core/errors.py`
- Modify: `server/tests/conftest.py`
- Test: `server/tests/test_agent_config.py`

**Interfaces:**
- Consumes: `Base` из `barysguard.db.base`, `Agent` и `AgentGroup` из `barysguard.db.models.agent`.
- Produces:
  - `ConfigScope` (StrEnum: `GLOBAL`, `GROUP`), `AgentConfig` (модель)
  - `AgentConfigDocument` (pydantic-модель документа)
  - `merge_documents(base: dict, overlay: dict) -> dict`
  - `compute_config_version(document: dict) -> int`
  - `effective_document(session: AsyncSession, group_id: uuid.UUID | None) -> dict[str, Any]`
  - `effective_config_for_agent(session, agent: Agent) -> tuple[dict[str, Any], int]`
  - `global_heartbeat_interval(session) -> int`
  - `ConfigTreeError` из `barysguard.core.errors`

- [x] **Шаг 1: Написать падающий тест**

Создать `server/tests/test_agent_config.py`:

```python
import uuid

import pytest

from barysguard.core.errors import ConfigTreeError
from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.config import AgentConfig, ConfigScope
from barysguard.services.config import (
    AgentConfigDocument,
    compute_config_version,
    effective_config_for_agent,
    effective_document,
    global_heartbeat_interval,
    merge_documents,
)


def test_defaults_match_specification():
    document = AgentConfigDocument().model_dump(mode="json")
    assert document["transport"]["heartbeat_interval_seconds"] == 30
    assert document["transport"]["event_batch_max"] == 500
    assert document["buffer"]["max_bytes"] == 500 * 1024 * 1024
    assert document["buffer"]["max_age_days"] == 7
    assert document["policies"] == {}


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError):
        AgentConfigDocument.model_validate({"transport": {"heartbeat_intervall": 30}})


def test_merge_is_recursive_for_dictionaries():
    base = {"transport": {"a": 1, "b": 2}, "logging": {"level": "info"}}
    overlay = {"transport": {"b": 3}}
    assert merge_documents(base, overlay) == {
        "transport": {"a": 1, "b": 3},
        "logging": {"level": "info"},
    }


def test_merge_replaces_lists_entirely():
    # Дополнение списков сделало бы невыразимым снятие унаследованного пути.
    base = {"policies": {"excluded": ["/srv/docs", "/srv/reports"]}}
    overlay = {"policies": {"excluded": ["/srv/docs"]}}
    assert merge_documents(base, overlay)["policies"]["excluded"] == ["/srv/docs"]


def test_version_ignores_key_order():
    first = compute_config_version({"a": 1, "b": {"c": 2, "d": 3}})
    second = compute_config_version({"b": {"d": 3, "c": 2}, "a": 1})
    assert first == second


def test_version_is_positive_int32():
    version = compute_config_version(AgentConfigDocument().model_dump(mode="json"))
    assert 0 <= version <= 0x7FFFFFFF


def test_version_changes_with_document():
    base = AgentConfigDocument().model_dump(mode="json")
    changed = merge_documents(base, {"transport": {"heartbeat_interval_seconds": 15}})
    assert compute_config_version(base) != compute_config_version(changed)


async def test_effective_document_without_rows_returns_defaults(session):
    document = await effective_document(session, None)
    assert document == AgentConfigDocument().model_dump(mode="json")


async def test_group_chain_merges_from_root_to_leaf(session):
    root = AgentGroup(name="root")
    middle = AgentGroup(name="middle")
    leaf = AgentGroup(name="leaf")
    session.add(root)
    await session.flush()
    middle.parent_id = root.id
    session.add(middle)
    await session.flush()
    leaf.parent_id = middle.id
    session.add(leaf)
    await session.flush()

    session.add_all(
        [
            AgentConfig(scope=ConfigScope.GLOBAL, document={"logging": {"level": "info"}}),
            AgentConfig(
                scope=ConfigScope.GROUP,
                group_id=root.id,
                document={"transport": {"heartbeat_interval_seconds": 60}},
            ),
            AgentConfig(
                scope=ConfigScope.GROUP,
                group_id=leaf.id,
                document={"logging": {"level": "debug"}},
            ),
        ]
    )
    await session.flush()

    document = await effective_document(session, leaf.id)
    # Значение корня наследуется, значение листа перекрывает глобальное.
    assert document["transport"]["heartbeat_interval_seconds"] == 60
    assert document["logging"]["level"] == "debug"


async def test_cycle_in_group_tree_is_rejected(session):
    first = AgentGroup(name="first")
    second = AgentGroup(name="second")
    session.add_all([first, second])
    await session.flush()
    first.parent_id = second.id
    second.parent_id = first.id
    await session.flush()

    with pytest.raises(ConfigTreeError):
        await effective_document(session, first.id)


async def test_effective_config_for_agent_uses_its_group(session):
    group = AgentGroup(name="sales")
    session.add(group)
    await session.flush()
    session.add(
        AgentConfig(
            scope=ConfigScope.GROUP,
            group_id=group.id,
            document={"transport": {"heartbeat_interval_seconds": 5}},
        )
    )
    agent = Agent(
        machine_id=f"machine-{uuid.uuid4().hex}",
        hostname="ws-1",
        os="windows",
        os_version="11",
        arch="amd64",
        agent_version="0.1.0",
        group_id=group.id,
    )
    session.add(agent)
    await session.flush()

    document, version = await effective_config_for_agent(session, agent)
    assert document["transport"]["heartbeat_interval_seconds"] == 5
    assert version == compute_config_version(document)


async def test_global_heartbeat_interval_reads_global_row(session):
    session.add(
        AgentConfig(
            scope=ConfigScope.GLOBAL,
            document={"transport": {"heartbeat_interval_seconds": 45}},
        )
    )
    await session.flush()
    assert await global_heartbeat_interval(session) == 45
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
python -m pytest tests/test_agent_config.py -q
```

Expected: `ModuleNotFoundError: No module named 'barysguard.db.models.config'`

- [x] **Шаг 3: Добавить исключение в `server/barysguard/core/errors.py`**

Дописать в конец файла:

```python
class ConfigTreeError(Exception):
    """Дерево групп не даёт построить цепочку наследования.

    Возникает при цикле parent_id либо при чрезмерной глубине. Дерево строят
    операторы, а ON DELETE SET NULL не исключает цикл полностью; бесконечный
    обход в обработчике запроса недопустим.
    """
```

- [x] **Шаг 4: Создать `server/barysguard/db/models/config.py`**

```python
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class ConfigScope(enum.StrEnum):
    GLOBAL = "global"
    GROUP = "group"


class AgentConfig(Base):
    """Строка конфигурации: либо одна глобальная, либо переопределение группы."""

    __tablename__ = "agent_configs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope: Mapped[ConfigScope] = mapped_column(
        Enum(ConfigScope, name="config_scope", native_enum=False, length=32)
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="CASCADE")
    )
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "(scope = 'GLOBAL' AND group_id IS NULL) "
            "OR (scope = 'GROUP' AND group_id IS NOT NULL)",
            # Соглашение об именовании подставит префикс ck_agent_configs_ само.
            name="scope_group",
        ),
        # Две глобальные строки сделали бы эффективный конфиг зависящим от
        # порядка выборки. Такая ошибка проявляется не при записи, а спустя
        # месяцы и на одном агенте из тысячи.
        Index(
            "uq_agent_configs_global",
            "scope",
            unique=True,
            postgresql_where=text("scope = 'GLOBAL'"),
        ),
        Index(
            "uq_agent_configs_group",
            "group_id",
            unique=True,
            postgresql_where=text("scope = 'GROUP'"),
        ),
    )
```

- [x] **Шаг 5: Создать `server/barysguard/services/config.py`**

```python
import hashlib
import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.errors import ConfigTreeError
from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.config import AgentConfig, ConfigScope

# Дерево групп строят операторы, и цикл parent_id не исключён полностью.
MAX_GROUP_DEPTH = 32


class TransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heartbeat_interval_seconds: int = Field(default=30, ge=5, le=3600)
    event_batch_max: int = Field(default=500, ge=1, le=10000)
    event_batch_max_bytes: int = Field(default=4 * 1024 * 1024, ge=64 * 1024, le=64 * 1024 * 1024)
    backoff_base_seconds: int = Field(default=1, ge=1, le=60)
    backoff_max_seconds: int = Field(default=300, ge=1, le=3600)


class BufferConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_bytes: int = Field(default=500 * 1024 * 1024, ge=1024 * 1024)
    max_age_days: int = Field(default=7, ge=1, le=365)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["debug", "info", "warn", "error"] = "info"


class AgentConfigDocument(BaseModel):
    """Полная форма конфигурации агента.

    extra="forbid" на каждом уровне обязателен: опечатка вроде
    heartbeat_intervall иначе молча не применится, и разбор такого
    инцидента занимает часы.
    """

    model_config = ConfigDict(extra="forbid")

    transport: TransportConfig = TransportConfig()
    buffer: BufferConfig = BufferConfig()
    logging: LoggingConfig = LoggingConfig()
    # Наполняется подпроектом 3. Зарезервирован пустым, чтобы добавление
    # политик не меняло версию контракта.
    policies: dict[str, Any] = Field(default_factory=dict)


def merge_documents(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Слияние вложенных словарей. Списки заменяются целиком.

    Дополнение списков сделало бы невыразимой операцию «убрать один путь
    из унаследованных исключений»: дочерняя группа могла бы только добавлять.
    """
    result = dict(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = merge_documents(current, value)
        else:
            result[key] = value
    return result


def compute_config_version(document: dict[str, Any]) -> int:
    """Версия — хеш документа, а не счётчик.

    Счётчик потребовал бы UPDATE по всем агентам при правке корневой строки.
    При хеше правка не пишет в agents ни одной строки: агент обнаруживает
    расхождение сам на ближайшем heartbeat.
    """
    material = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


async def group_chain(session: AsyncSession, group_id: uuid.UUID) -> list[uuid.UUID]:
    """Цепочка групп от корня дерева к указанной."""
    chain: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    current: uuid.UUID | None = group_id

    while current is not None:
        if current in seen:
            raise ConfigTreeError(f"cycle in agent group tree at {current}")
        if len(chain) >= MAX_GROUP_DEPTH:
            raise ConfigTreeError(f"agent group tree deeper than {MAX_GROUP_DEPTH}")
        seen.add(current)
        chain.append(current)
        current = (
            await session.execute(select(AgentGroup.parent_id).where(AgentGroup.id == current))
        ).scalar_one_or_none()

    chain.reverse()
    return chain


async def effective_document(
    session: AsyncSession, group_id: uuid.UUID | None
) -> dict[str, Any]:
    """Документ агента: значения по умолчанию, глобальная строка, затем группы.

    Отсчёт от значений по умолчанию, а не от глобальной строки, намеренный:
    сервер обязан отдавать осмысленный конфиг и на пустой базе.
    """
    document = AgentConfigDocument().model_dump(mode="json")

    global_row = (
        await session.execute(select(AgentConfig).where(AgentConfig.scope == ConfigScope.GLOBAL))
    ).scalar_one_or_none()
    if global_row is not None:
        document = merge_documents(document, global_row.document)

    if group_id is None:
        return document

    chain = await group_chain(session, group_id)
    rows = (
        (await session.execute(select(AgentConfig).where(AgentConfig.group_id.in_(chain))))
        .scalars()
        .all()
    )
    by_group = {row.group_id: row for row in rows}
    for node in chain:
        row = by_group.get(node)
        if row is not None:
            document = merge_documents(document, row.document)

    return document


async def effective_config_for_agent(
    session: AsyncSession, agent: Agent
) -> tuple[dict[str, Any], int]:
    document = await effective_document(session, agent.group_id)
    return document, compute_config_version(document)


async def global_heartbeat_interval(session: AsyncSession) -> int:
    """Интервал из глобальной конфигурации.

    Используется там, где эффективный документ на каждого агента обошёлся бы
    обходом дерева групп на каждой строке списка.
    """
    document = await effective_document(session, None)
    return int(document["transport"]["heartbeat_interval_seconds"])
```

- [x] **Шаг 6: Зарегистрировать модель в `server/barysguard/db/models/__init__.py`**

Добавить строку импорта рядом с существующими (точную форму файла посмотреть перед правкой — импорты перечислены списком, и порядок поддерживается `ruff`):

```python
from barysguard.db.models.config import AgentConfig, ConfigScope
```

и дописать `"AgentConfig"`, `"ConfigScope"` в `__all__`.

- [x] **Шаг 7: Добавить очистку таблицы в `server/tests/conftest.py`**

В фикстуре `app_client` в строке `TRUNCATE` добавить `agent_configs` первым элементом:

```python
                "TRUNCATE agent_configs, agent_certificates, enrollment_tokens, agents, "
                "agent_groups, audit_log, users RESTART IDENTITY CASCADE"
```

- [x] **Шаг 8: Сгенерировать миграцию**

```bash
alembic revision --autogenerate -m "agent configs"
```

Открыть созданный файл и убедиться, что в нём есть `create_table("agent_configs", ...)`, `ck_agent_configs_scope_group` и оба частичных индекса с `postgresql_where`. Если частичные индексы не попали — дописать их вручную:

```python
    op.create_index(
        "uq_agent_configs_global",
        "agent_configs",
        ["scope"],
        unique=True,
        postgresql_where=sa.text("scope = 'GLOBAL'"),
    )
    op.create_index(
        "uq_agent_configs_group",
        "agent_configs",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'GROUP'"),
    )
```

- [x] **Шаг 9: Запустить тесты**

```bash
python -m pytest tests/test_agent_config.py -q
```

Expected: PASS, 12 тестов.

- [x] **Шаг 10: Проверить линт и типы, зафиксировать**

```bash
ruff check . && ruff format --check . && mypy barysguard
git add barysguard/db/models/config.py barysguard/services/config.py \
        barysguard/db/models/__init__.py barysguard/core/errors.py \
        tests/conftest.py tests/test_agent_config.py alembic/versions/
git commit -m "feat: agent configuration inherited across the group tree"
```

---

## Задача 2: Эндпоинт GET /config и правка регистрации

Агент получает возможность забрать документ и не получать его повторно, если версия не менялась. Регистрация начинает отдавать настоящую версию вместо нуля.

**Files:**
- Create: `server/tests/helpers.py`
- Modify: `server/tests/test_mtls_identity.py`
- Modify: `server/barysguard/gateway/schemas.py`
- Modify: `server/barysguard/gateway/router.py`
- Test: `server/tests/test_config_endpoint.py`

**Interfaces:**
- Consumes: `effective_config_for_agent` из задачи 1, `current_agent` из `barysguard.gateway.deps`.
- Produces:
  - `AgentConfigResponse` (поля `version: int`, `document: dict[str, Any]`), эндпоинт `GET /gateway/v1/config`
  - `tests/helpers.py`: `build_csr() -> str`, `EnrolledAgent` (поля `agent_id: uuid.UUID`, `serial: str`, `headers: dict[str, str]`, `body: dict`), `enroll_agent(app_client, session, machine_id, group_id=None) -> EnrolledAgent`

- [x] **Шаг 1: Вынести помощники регистрации в `server/tests/helpers.py`**

Функции `_csr` и `_enroll` уже есть в `server/tests/test_mtls_identity.py`, но каждому следующему тестовому модулю нужны и заголовки mTLS, и группа агента. Выносим их один раз.

```python
import uuid
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession


def build_csr() -> str:
    """Запрос на сертификат. Субъект сервером игнорируется, имя произвольно."""
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


@dataclass(frozen=True)
class EnrolledAgent:
    agent_id: uuid.UUID
    serial: str
    headers: dict[str, str]
    body: dict


async def enroll_agent(
    app_client,
    session: AsyncSession,
    machine_id: str,
    group_id: uuid.UUID | None = None,
) -> EnrolledAgent:
    """Регистрирует агента через настоящий эндпоинт и отдаёт заголовки mTLS.

    Заголовки формируются так же, как их проставляет nginx после проверки
    клиентского сертификата.
    """
    from barysguard.services.enrollment import create_enrollment_token

    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=group_id, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": raw,
            "csr_pem": build_csr(),
            "host": {
                "machine_id": machine_id,
                "hostname": "ws-1",
                "os": "linux",
                "os_version": "6.8.0",
                "arch": "amd64",
                "agent_version": "0.1.0",
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    certificate = x509.load_pem_x509_certificate(body["certificate_pem"].encode())
    serial = format(certificate.serial_number, "x")

    return EnrolledAgent(
        agent_id=uuid.UUID(body["agent_id"]),
        serial=serial,
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
        body=body,
    )
```

Затем в `server/tests/test_mtls_identity.py` удалить локальные `_csr` и `_enroll`, импортировать `enroll_agent` из `tests.helpers` и заменить вызовы. Прогнать `python -m pytest tests/test_mtls_identity.py -q` — восемь тестов должны остаться зелёными. Это подтверждает, что помощник эквивалентен вынесенному коду, до того как на нём будут построены новые тесты.

- [x] **Шаг 2: Написать падающий тест**

Создать `server/tests/test_config_endpoint.py`:

```python
import pytest

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.config import AgentConfig, ConfigScope
from tests.helpers import enroll_agent


@pytest.mark.asyncio
async def test_config_returns_document_and_version(app_client, session):
    agent = await enroll_agent(app_client, session, "config-basic")

    response = await app_client.get("/gateway/v1/config", headers=agent.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["transport"]["heartbeat_interval_seconds"] == 30
    assert body["version"] > 0
    assert response.headers["ETag"] == f'"{body["version"]}"'


@pytest.mark.asyncio
async def test_matching_etag_returns_304(app_client, session):
    agent = await enroll_agent(app_client, session, "config-etag")

    first = await app_client.get("/gateway/v1/config", headers=agent.headers)
    etag = first.headers["ETag"]

    second = await app_client.get(
        "/gateway/v1/config", headers={**agent.headers, "If-None-Match": etag}
    )

    assert second.status_code == 304
    assert second.content == b""


@pytest.mark.asyncio
async def test_group_override_reaches_its_agent_only(app_client, session):
    group = AgentGroup(name="sales")
    session.add(group)
    await session.flush()
    session.add(
        AgentConfig(
            scope=ConfigScope.GROUP,
            group_id=group.id,
            document={"logging": {"level": "debug"}},
        )
    )
    await session.commit()

    inside = await enroll_agent(app_client, session, "config-inside", group_id=group.id)
    outside = await enroll_agent(app_client, session, "config-outside")

    mine = await app_client.get("/gateway/v1/config", headers=inside.headers)
    theirs = await app_client.get("/gateway/v1/config", headers=outside.headers)

    assert mine.json()["document"]["logging"]["level"] == "debug"
    assert theirs.json()["document"]["logging"]["level"] == "info"


@pytest.mark.asyncio
async def test_config_requires_client_certificate(app_client):
    response = await app_client.get("/gateway/v1/config")

    assert response.status_code == 403
```

- [x] **Шаг 3: Запустить тест и убедиться, что он падает**

```bash
python -m pytest tests/test_config_endpoint.py -q
```

Expected: FAIL, `404 Not Found` на `/gateway/v1/config`.

- [x] **Шаг 4: Добавить схему в `server/barysguard/gateway/schemas.py`**

```python
class AgentConfigResponse(BaseModel):
    version: int
    document: dict[str, Any]
```

Дописать `from typing import Any` в импорты файла.

- [x] **Шаг 5: Добавить эндпоинт в `server/barysguard/gateway/router.py`**

```python
@router.get("/config", response_model=AgentConfigResponse)
async def get_agent_config(
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> Response:
    document, version = await effective_config_for_agent(session, agent)
    etag = f'"{version}"'

    # Агенты опрашивают конфиг редко, но после перезапуска сервера делают это
    # одновременно. Пустой ответ на совпавшую версию дешевле полного документа.
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    return JSONResponse(
        content={"version": version, "document": document},
        headers={"ETag": etag},
    )
```

Добавить импорты: `from fastapi.responses import JSONResponse`, `from barysguard.services.config import effective_config_for_agent`, `AgentConfigResponse` в существующий импорт схем.

- [x] **Шаг 6: Поправить `enroll` в том же файле**

Заменить вычисление двух полей ответа. Было:

```python
    return EnrollResponse(
        agent_id=agent.id,
        certificate_pem=certificate_pem.decode("ascii"),
        ca_pem=ca.certificate_pem.decode("ascii"),
        config_version=agent.config_version,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
    )
```

Стало:

```python
    # Агент, получивший версию 0, обнаружил бы расхождение на первом же
    # heartbeat и сходил за конфигом лишний раз. При массовом развёртывании
    # это заметная лишняя волна.
    document, config_version = await effective_config_for_agent(session, agent)

    return EnrollResponse(
        agent_id=agent.id,
        certificate_pem=certificate_pem.decode("ascii"),
        ca_pem=ca.certificate_pem.decode("ascii"),
        config_version=config_version,
        heartbeat_interval_seconds=document["transport"]["heartbeat_interval_seconds"],
    )
```

Параметр `settings` в сигнатуре `enroll` остаётся: он используется для `settings.agent_cert_days`.

- [x] **Шаг 7: Дописать тест на регистрацию**

В `server/tests/test_enroll_endpoint.py` добавить:

```python
@pytest.mark.asyncio
async def test_enroll_returns_effective_config_version(app_client, session):
    from barysguard.services.config import AgentConfigDocument, compute_config_version
    from tests.helpers import enroll_agent

    agent = await enroll_agent(app_client, session, "enroll-config-version")

    # Версия 0 заставила бы агента сходить за конфигом лишний раз
    # на первом же heartbeat.
    expected = compute_config_version(AgentConfigDocument().model_dump(mode="json"))
    assert agent.body["config_version"] == expected
    assert agent.body["heartbeat_interval_seconds"] == 30
```

- [x] **Шаг 8: Запустить тесты**

```bash
python -m pytest tests/test_config_endpoint.py tests/test_enroll_endpoint.py -q
```

Expected: PASS.

- [x] **Шаг 9: Проверить линт и типы, зафиксировать**

```bash
ruff check . && ruff format --check . && mypy barysguard
git add barysguard/gateway/ tests/
git commit -m "feat: agent config endpoint with etag revalidation"
```

---

## Задача 3: Модель и служба команд

Очередь команд целиком в сервисном слое: постановка, истечение, выдача под конкурентным доступом, приём результата. HTTP появится в задачах 4 и 5.

**Files:**
- Create: `server/barysguard/db/models/command.py`
- Create: `server/barysguard/services/commands.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Modify: `server/tests/conftest.py`
- Test: `server/tests/test_commands_service.py`

**Interfaces:**
- Consumes: `Agent` из `barysguard.db.models.agent`.
- Produces:
  - `CommandType` (StrEnum: `PING`, `REFRESH_CONFIG`, `COLLECT_DIAGNOSTICS`)
  - `CommandStatus` (StrEnum: `QUEUED`, `SENT`, `RUNNING`, `DONE`, `FAILED`, `EXPIRED`)
  - `Command` (модель)
  - `MAX_COMMANDS_PER_HEARTBEAT: int = 50`, `MAX_RESULT_BYTES: int = 65536`
  - `queue_command(session, *, agent_id, command_type, payload, created_by, ttl_seconds) -> Command`
  - `expire_stale_commands(session, agent_id) -> int`
  - `dequeue_commands(session, agent_id, limit) -> list[Command]`
  - `record_command_result(session, *, agent_id, command_id, status, result) -> tuple[Command, bool] | None`
    — `None` означает «нет такой команды у этого агента»; второй элемент пары равен
    `False`, если это повтор по уже завершённой команде и состояние не менялось.
    Пара нужна обработчику из задачи 5, чтобы различить `202` и `200`, не гадая
    по содержимому полей.
  - `CommandNotDelivered` из `barysguard.core.errors`

- [x] **Шаг 1: Написать падающий тест**

Создать `server/tests/test_commands_service.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from barysguard.core.errors import CommandNotDelivered
from barysguard.db.models.agent import Agent
from barysguard.db.models.command import Command, CommandStatus, CommandType
from barysguard.services.commands import (
    dequeue_commands,
    expire_stale_commands,
    queue_command,
    record_command_result,
)


async def _agent(session) -> Agent:
    agent = Agent(
        machine_id=f"machine-{uuid.uuid4().hex}",
        hostname="ws-1",
        os="linux",
        os_version="24.04",
        arch="amd64",
        agent_version="0.1.0",
    )
    session.add(agent)
    await session.flush()
    return agent


async def test_queued_command_has_expiry(session):
    agent = await _agent(session)
    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=CommandType.PING,
        payload={},
        created_by=None,
        ttl_seconds=3600,
    )
    assert command.status == CommandStatus.QUEUED
    assert command.expires_at > datetime.now(UTC)


async def test_dequeue_marks_commands_sent(session):
    agent = await _agent(session)
    await queue_command(
        session, agent_id=agent.id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )

    first = await dequeue_commands(session, agent.id, 50)
    assert [command.status for command in first] == [CommandStatus.SENT]

    # Повторный heartbeat не должен выдать ту же команду второй раз.
    second = await dequeue_commands(session, agent.id, 50)
    assert second == []


async def test_dequeue_never_crosses_agents(session):
    mine = await _agent(session)
    theirs = await _agent(session)
    await queue_command(
        session, agent_id=theirs.id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    assert await dequeue_commands(session, mine.id, 50) == []


async def test_expired_command_is_not_delivered(session):
    agent = await _agent(session)
    command = await queue_command(
        session, agent_id=agent.id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    command.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    assert await expire_stale_commands(session, agent.id) == 1
    await session.refresh(command)
    assert command.status == CommandStatus.EXPIRED
    assert await dequeue_commands(session, agent.id, 50) == []


async def test_dequeue_respects_limit_and_order(session):
    agent = await _agent(session)
    for _ in range(3):
        await queue_command(
            session, agent_id=agent.id, command_type=CommandType.PING,
            payload={}, created_by=None, ttl_seconds=3600,
        )
    batch = await dequeue_commands(session, agent.id, 2)
    assert len(batch) == 2
    assert batch[0].created_at <= batch[1].created_at


async def test_result_is_recorded(session):
    agent = await _agent(session)
    command = await queue_command(
        session, agent_id=agent.id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await dequeue_commands(session, agent.id, 50)

    outcome = await record_command_result(
        session, agent_id=agent.id, command_id=command.id,
        status=CommandStatus.DONE, result={"pong": True},
    )
    assert outcome is not None
    updated, accepted = outcome
    assert accepted is True
    assert updated.status == CommandStatus.DONE
    assert updated.result == {"pong": True}
    assert updated.completed_at is not None


async def test_repeated_result_does_not_overwrite(session):
    agent = await _agent(session)
    command = await queue_command(
        session, agent_id=agent.id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await dequeue_commands(session, agent.id, 50)
    await record_command_result(
        session, agent_id=agent.id, command_id=command.id,
        status=CommandStatus.DONE, result={"first": True},
    )

    # Агент вправе повторить запрос после обрыва связи. Повтор не должен
    # затирать сохранённый результат.
    outcome = await record_command_result(
        session, agent_id=agent.id, command_id=command.id,
        status=CommandStatus.FAILED, result={"second": True},
    )
    assert outcome is not None
    again, accepted = outcome
    assert accepted is False
    assert again.status == CommandStatus.DONE
    assert again.result == {"first": True}


async def test_result_for_foreign_command_is_not_found(session):
    mine = await _agent(session)
    theirs = await _agent(session)
    command = await queue_command(
        session, agent_id=theirs.id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    outcome = await record_command_result(
        session, agent_id=mine.id, command_id=command.id,
        status=CommandStatus.DONE, result={},
    )
    assert outcome is None


async def test_result_for_undelivered_command_is_rejected(session):
    agent = await _agent(session)
    command = await queue_command(
        session, agent_id=agent.id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    with pytest.raises(CommandNotDelivered):
        await record_command_result(
            session, agent_id=agent.id, command_id=command.id,
            status=CommandStatus.DONE, result={},
        )


async def test_concurrent_dequeue_delivers_each_command_once(migrated_database_url, session):
    """Два heartbeat подряд могут попасть в разные воркеры uvicorn.

    Без SKIP LOCKED второй запрос ждал бы первый, а без FOR UPDATE обе
    транзакции выдали бы агенту одну и ту же команду дважды.
    """
    import asyncio

    from barysguard.db.session import create_engine_from_url, session_factory

    agent = await _agent(session)
    for _ in range(4):
        await queue_command(
            session, agent_id=agent.id, command_type=CommandType.PING,
            payload={}, created_by=None, ttl_seconds=3600,
        )
    await session.commit()

    engine = create_engine_from_url(migrated_database_url)
    maker = session_factory(engine)

    async def take() -> list[uuid.UUID]:
        async with maker() as other:
            commands = await dequeue_commands(other, agent.id, 4)
            identifiers = [command.id for command in commands]
            await other.commit()
            return identifiers

    try:
        first, second = await asyncio.gather(take(), take())
    finally:
        await engine.dispose()

    assert set(first) & set(second) == set()
    assert len(first) + len(second) == 4
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
python -m pytest tests/test_commands_service.py -q
```

Expected: `ModuleNotFoundError: No module named 'barysguard.db.models.command'`

- [x] **Шаг 3: Добавить исключение в `server/barysguard/core/errors.py`**

```python
class CommandNotDelivered(Exception):
    """Результат прислан на команду, которая агенту не выдавалась.

    Молчаливое принятие такого результата означало бы, что подделанный
    идентификатор закрывает команду, всё ещё ожидающую доставки.
    """
```

- [x] **Шаг 4: Создать `server/barysguard/db/models/command.py`**

```python
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class CommandType(enum.StrEnum):
    PING = "ping"
    REFRESH_CONFIG = "refresh_config"
    COLLECT_DIAGNOSTICS = "collect_diagnostics"


class CommandStatus(enum.StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


class Command(Base):
    """Указание оператора отдельному агенту.

    Срок годности обязателен: команда, доставленная через три недели после
    закрытия инцидента, — это авария, а не запоздалая реакция.
    """

    __tablename__ = "commands"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[CommandType] = mapped_column(
        Enum(CommandType, name="command_type", native_enum=False, length=32)
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[CommandStatus] = mapped_column(
        Enum(CommandStatus, name="command_status", native_enum=False, length=32),
        default=CommandStatus.QUEUED,
        index=True,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Выборка на heartbeat идёт только по очереди конкретного агента.
        Index(
            "ix_commands_queued",
            "agent_id",
            "created_at",
            postgresql_where=text("status = 'QUEUED'"),
        ),
    )
```

- [x] **Шаг 5: Создать `server/barysguard/services/commands.py`**

```python
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.errors import CommandNotDelivered
from barysguard.db.models.command import Command, CommandStatus, CommandType

# Больше полусотни указаний за один цикл опроса означает не работу оператора,
# а ошибку автоматики. Остаток уйдёт следующим heartbeat.
MAX_COMMANDS_PER_HEARTBEAT = 50

# Диагностика агента не является каналом передачи артефактов: для этого
# предусмотрен POST /artifacts.
MAX_RESULT_BYTES = 64 * 1024

TERMINAL_STATUSES = (CommandStatus.DONE, CommandStatus.FAILED, CommandStatus.EXPIRED)
DELIVERED_STATUSES = (CommandStatus.SENT, CommandStatus.RUNNING)


async def queue_command(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    command_type: CommandType,
    payload: dict[str, Any],
    created_by: uuid.UUID | None,
    ttl_seconds: int,
) -> Command:
    command = Command(
        agent_id=agent_id,
        type=command_type,
        payload=payload,
        created_by=created_by,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    session.add(command)
    await session.flush()
    return command


async def expire_stale_commands(session: AsyncSession, agent_id: uuid.UUID) -> int:
    """Просроченные команды агента переводятся в expired до выборки."""
    now = datetime.now(UTC)
    result = await session.execute(
        update(Command)
        .where(
            Command.agent_id == agent_id,
            Command.status.in_((CommandStatus.QUEUED, CommandStatus.SENT)),
            Command.expires_at <= now,
        )
        .values(status=CommandStatus.EXPIRED, completed_at=now)
    )
    return int(result.rowcount or 0)


async def dequeue_commands(
    session: AsyncSession, agent_id: uuid.UUID, limit: int
) -> list[Command]:
    """Выдать команды агенту и пометить их отправленными.

    SKIP LOCKED нужен потому, что агент может прислать два heartbeat подряд
    в разные воркеры uvicorn: без него второй запрос ждал бы первый, а не
    прошёл мимо занятых строк.
    """
    now = datetime.now(UTC)
    commands = (
        (
            await session.execute(
                select(Command)
                .where(
                    Command.agent_id == agent_id,
                    Command.status == CommandStatus.QUEUED,
                    Command.expires_at > now,
                )
                .order_by(Command.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    for command in commands:
        command.status = CommandStatus.SENT
        command.sent_at = now

    await session.flush()
    return list(commands)


async def record_command_result(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    command_id: uuid.UUID,
    status: CommandStatus,
    result: dict[str, Any],
) -> tuple[Command, bool] | None:
    """Сохранить результат команды.

    None означает «нет такой команды у этого агента». Второй элемент пары —
    признак того, что состояние действительно изменилось: обработчику нужно
    отличить принятие от повтора, и выводить это из содержимого полей значило
    бы угадывать.
    """
    command = (
        await session.execute(
            select(Command).where(Command.id == command_id, Command.agent_id == agent_id)
        )
    ).scalar_one_or_none()

    if command is None:
        return None

    # Повтор после обрыва связи не должен затирать сохранённое.
    if command.status in TERMINAL_STATUSES:
        return command, False

    if command.status not in DELIVERED_STATUSES:
        raise CommandNotDelivered(str(command_id))

    command.status = status
    command.result = result
    command.completed_at = datetime.now(UTC)
    await session.flush()
    return command, True
```

- [x] **Шаг 6: Зарегистрировать модель и очистку таблицы**

В `server/barysguard/db/models/__init__.py`:

```python
from barysguard.db.models.command import Command, CommandStatus, CommandType
```

и добавить три имени в `__all__`.

В `server/tests/conftest.py` в строке `TRUNCATE` добавить `commands`:

```python
                "TRUNCATE commands, agent_configs, agent_certificates, enrollment_tokens, "
                "agents, agent_groups, audit_log, users RESTART IDENTITY CASCADE"
```

- [x] **Шаг 7: Сгенерировать миграцию**

```bash
alembic revision --autogenerate -m "commands"
```

Проверить наличие `create_table("commands", ...)` и частичного индекса `ix_commands_queued` с `postgresql_where=sa.text("status = 'QUEUED'")`; дописать индекс вручную, если автогенерация его не внесла.

- [x] **Шаг 8: Запустить тесты**

```bash
python -m pytest tests/test_commands_service.py -q
```

Expected: PASS, 10 тестов.

- [x] **Шаг 9: Проверить линт и типы, зафиксировать**

```bash
ruff check . && ruff format --check . && mypy barysguard
git add barysguard/db/models/command.py barysguard/services/commands.py \
        barysguard/db/models/__init__.py barysguard/core/errors.py \
        tests/conftest.py tests/test_commands_service.py alembic/versions/
git commit -m "feat: command queue with expiry and skip-locked delivery"
```

---

## Задача 4: Эндпоинт POST /heartbeat

**Files:**
- Modify: `server/barysguard/gateway/schemas.py`
- Modify: `server/barysguard/gateway/router.py`
- Test: `server/tests/test_heartbeat_endpoint.py`

**Interfaces:**
- Consumes: `effective_config_for_agent` (задача 1), `expire_stale_commands`, `dequeue_commands`, `MAX_COMMANDS_PER_HEARTBEAT` (задача 3), `current_agent`.
- Produces: `HeartbeatRequest`, `HeartbeatResponse`, `QueuedCommand`; эндпоинт `POST /gateway/v1/heartbeat`.

- [x] **Шаг 1: Написать падающий тест**

Создать `server/tests/test_heartbeat_endpoint.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.models.certificate import AgentCertificate
from barysguard.db.models.command import CommandType
from barysguard.services.commands import queue_command
from tests.helpers import enroll_agent


def _body(**overrides) -> dict:
    payload = {
        "agent_version": "0.1.0",
        "config_version": 0,
        "sent_at": datetime.now(UTC).isoformat(),
        "buffered_events": 0,
        "buffer_bytes": 0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_heartbeat_returns_server_time_and_version(app_client, session):
    agent = await enroll_agent(app_client, session, "hb-basic")

    response = await app_client.post(
        "/gateway/v1/heartbeat", headers=agent.headers, json=_body()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["config_version"] > 0
    assert body["heartbeat_interval_seconds"] == 30
    assert body["commands"] == []


@pytest.mark.asyncio
async def test_heartbeat_updates_agent_record(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-record")

    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body(agent_version="0.2.0")
    )

    agent = await session.get(Agent, enrolled.agent_id)
    await session.refresh(agent)
    assert agent.last_heartbeat_at is not None
    assert agent.agent_version == "0.2.0"
    assert agent.status == AgentStatus.ACTIVE


@pytest.mark.asyncio
async def test_clock_skew_is_recorded_in_both_directions(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-skew")

    ahead = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body(sent_at=ahead)
    )
    agent = await session.get(Agent, enrolled.agent_id)
    await session.refresh(agent)
    assert agent.clock_skew_ms > 20_000

    behind = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body(sent_at=behind)
    )
    await session.refresh(agent)
    assert agent.clock_skew_ms < -20_000


@pytest.mark.asyncio
async def test_reported_config_version_does_not_affect_server_answer(app_client, session):
    # Версию считает сервер. Присланное агентом значение только записывается.
    agent = await enroll_agent(app_client, session, "hb-forged-version")

    honest = await app_client.post(
        "/gateway/v1/heartbeat", headers=agent.headers, json=_body()
    )
    forged = await app_client.post(
        "/gateway/v1/heartbeat", headers=agent.headers, json=_body(config_version=123456)
    )

    assert honest.json()["config_version"] == forged.json()["config_version"]


@pytest.mark.asyncio
async def test_queued_command_is_delivered_once(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-command-once")
    await queue_command(
        session, agent_id=enrolled.agent_id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await session.commit()

    first = await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body()
    )
    assert [command["type"] for command in first.json()["commands"]] == ["ping"]

    second = await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body()
    )
    assert second.json()["commands"] == []


@pytest.mark.asyncio
async def test_expired_command_is_not_delivered(app_client, session):
    enrolled = await enroll_agent(app_client, session, "hb-command-expired")
    command = await queue_command(
        session, agent_id=enrolled.agent_id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    command.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body()
    )

    assert response.json()["commands"] == []


@pytest.mark.asyncio
async def test_foreign_command_is_not_delivered(app_client, session):
    mine = await enroll_agent(app_client, session, "hb-command-mine")
    theirs = await enroll_agent(app_client, session, "hb-command-theirs")
    await queue_command(
        session, agent_id=theirs.agent_id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/heartbeat", headers=mine.headers, json=_body()
    )

    assert response.json()["commands"] == []


@pytest.mark.asyncio
async def test_revoked_certificate_cannot_heartbeat(app_client, session):
    # Отзыв обязан прекращать обслуживание немедленно, а не к следующему циклу.
    enrolled = await enroll_agent(app_client, session, "hb-revoked")
    await session.execute(
        update(AgentCertificate)
        .where(AgentCertificate.serial == enrolled.serial)
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=_body()
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_heartbeat_requires_client_certificate(app_client):
    response = await app_client.post("/gateway/v1/heartbeat", json=_body())

    assert response.status_code == 403
```

Серийный номер в `agent_certificates` хранится нормализованным (см. `normalize_serial` в `barysguard/pki/service.py`). Если сравнение в тесте отзыва не находит строку, свериться с тем, как серийный номер записывается при выпуске, и привести значение к той же форме — придумывать своё преобразование не нужно.

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
python -m pytest tests/test_heartbeat_endpoint.py -q
```

Expected: FAIL, `404 Not Found`.

- [x] **Шаг 3: Добавить схемы в `server/barysguard/gateway/schemas.py`**

```python
class HeartbeatRequest(BaseModel):
    agent_version: str = Field(max_length=32)
    config_version: int = Field(ge=0)
    sent_at: datetime
    buffered_events: int = Field(default=0, ge=0)
    buffer_bytes: int = Field(default=0, ge=0)


class QueuedCommand(BaseModel):
    id: uuid.UUID
    type: str
    payload: dict[str, Any]
    expires_at: datetime


class HeartbeatResponse(BaseModel):
    server_time: datetime
    config_version: int
    heartbeat_interval_seconds: int
    commands: list[QueuedCommand]
```

- [x] **Шаг 4: Добавить эндпоинт в `server/barysguard/gateway/router.py`**

```python
@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    payload: HeartbeatRequest,
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> HeartbeatResponse:
    now = datetime.now(UTC)

    # Часы агента могут прислать наивную отметку. Приводим к UTC явно:
    # иначе вычитание datetime бросит TypeError уже в боевой эксплуатации.
    sent_at = payload.sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)

    agent.last_heartbeat_at = now
    agent.agent_version = payload.agent_version
    agent.config_version = payload.config_version
    agent.clock_skew_ms = int((sent_at - now).total_seconds() * 1000)

    client_ip = request.headers.get(CLIENT_IP_HEADER)
    if client_ip:
        try:
            # Колонка типа INET: мусор в заголовке иначе уронит транзакцию.
            ipaddress.ip_address(client_ip)
        except ValueError:
            pass
        else:
            agent.last_ip = client_ip

    # Карантин и отзыв снимает только оператор: heartbeat их не отменяет.
    if agent.status in (AgentStatus.PENDING, AgentStatus.OFFLINE):
        agent.status = AgentStatus.ACTIVE

    document, config_version = await effective_config_for_agent(session, agent)

    await expire_stale_commands(session, agent.id)
    commands = await dequeue_commands(session, agent.id, MAX_COMMANDS_PER_HEARTBEAT)

    return HeartbeatResponse(
        server_time=now,
        config_version=config_version,
        heartbeat_interval_seconds=document["transport"]["heartbeat_interval_seconds"],
        commands=[
            QueuedCommand(
                id=command.id,
                type=command.type.value,
                payload=command.payload,
                expires_at=command.expires_at,
            )
            for command in commands
        ],
    )
```

Добавить в начало файла: `import ipaddress`, `from datetime import UTC, datetime`, импорты схем и служб. Рядом с `SERIAL_HEADER` в `barysguard/gateway/deps.py` объявить и экспортировать:

```python
# Адрес из соединения для приложения всегда является адресом обратного прокси.
CLIENT_IP_HEADER = "X-Real-IP"
```

- [x] **Шаг 5: Запустить тесты**

```bash
python -m pytest tests/test_heartbeat_endpoint.py -q
```

Expected: PASS, 9 тестов.

- [x] **Шаг 6: Проверить линт и типы, зафиксировать**

```bash
ruff check . && ruff format --check . && mypy barysguard
git add barysguard/gateway/ tests/test_heartbeat_endpoint.py
git commit -m "feat: agent heartbeat with clock skew and command delivery"
```

---

## Задача 5: Эндпоинт POST /commands/{id}/result

**Files:**
- Modify: `server/barysguard/gateway/schemas.py`
- Modify: `server/barysguard/gateway/router.py`
- Test: `server/tests/test_command_result_endpoint.py`

**Interfaces:**
- Consumes: `record_command_result`, `MAX_RESULT_BYTES`, `CommandNotDelivered` (задача 3).
- Produces: `CommandResultRequest`; эндпоинт `POST /gateway/v1/commands/{command_id}/result`.

- [x] **Шаг 1: Написать падающий тест**

Создать `server/tests/test_command_result_endpoint.py`:

```python
import uuid

import pytest

from barysguard.db.models.command import CommandStatus, CommandType
from barysguard.services.commands import queue_command
from tests.helpers import enroll_agent
from tests.test_heartbeat_endpoint import _body as heartbeat_body


@pytest.mark.asyncio
async def test_result_is_accepted_after_delivery(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-happy")
    command = await queue_command(
        session, agent_id=enrolled.agent_id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await session.commit()
    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=heartbeat_body()
    )

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {"pong": True}},
    )

    assert response.status_code == 202
    await session.refresh(command)
    assert command.status == CommandStatus.DONE
    assert command.result == {"pong": True}


@pytest.mark.asyncio
async def test_repeated_result_returns_200_and_keeps_first(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-repeat")
    command = await queue_command(
        session, agent_id=enrolled.agent_id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await session.commit()
    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=heartbeat_body()
    )
    await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {"first": True}},
    )

    repeat = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "failed", "result": {"second": True}},
    )

    assert repeat.status_code == 200
    await session.refresh(command)
    assert command.status == CommandStatus.DONE
    assert command.result == {"first": True}


@pytest.mark.asyncio
async def test_result_for_foreign_command_is_404(app_client, session):
    # 403 позволил бы перебором идентификаторов выяснять, какие команды есть.
    mine = await enroll_agent(app_client, session, "result-mine")
    theirs = await enroll_agent(app_client, session, "result-theirs")
    command = await queue_command(
        session, agent_id=theirs.agent_id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await session.commit()

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=mine.headers,
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_result_for_unknown_command_is_404(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-unknown")

    response = await app_client.post(
        f"/gateway/v1/commands/{uuid.uuid4()}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_result_before_delivery_is_409(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-undelivered")
    command = await queue_command(
        session, agent_id=enrolled.agent_id, command_type=CommandType.PING,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await session.commit()

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_oversized_result_is_rejected(app_client, session):
    enrolled = await enroll_agent(app_client, session, "result-oversized")
    command = await queue_command(
        session, agent_id=enrolled.agent_id, command_type=CommandType.COLLECT_DIAGNOSTICS,
        payload={}, created_by=None, ttl_seconds=3600,
    )
    await session.commit()
    await app_client.post(
        "/gateway/v1/heartbeat", headers=enrolled.headers, json=heartbeat_body()
    )

    response = await app_client.post(
        f"/gateway/v1/commands/{command.id}/result",
        headers=enrolled.headers,
        json={"status": "done", "result": {"blob": "x" * 70000}},
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_result_requires_client_certificate(app_client):
    response = await app_client.post(
        f"/gateway/v1/commands/{uuid.uuid4()}/result",
        json={"status": "done", "result": {}},
    )

    assert response.status_code == 403
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
python -m pytest tests/test_command_result_endpoint.py -q
```

Expected: FAIL, `404 Not Found` на эндпоинте.

- [x] **Шаг 3: Добавить схему в `server/barysguard/gateway/schemas.py`**

```python
class CommandResultRequest(BaseModel):
    status: Literal["done", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)
```

Дописать `from typing import Any, Literal` в импорты.

- [x] **Шаг 4: Добавить эндпоинт в `server/barysguard/gateway/router.py`**

```python
@router.post("/commands/{command_id}/result")
async def submit_command_result(
    command_id: uuid.UUID,
    payload: CommandResultRequest,
    response: Response,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    encoded = json.dumps(payload.result, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "command result too large"
        )

    try:
        outcome = await record_command_result(
            session,
            agent_id=agent.id,
            command_id=command_id,
            status=CommandStatus(payload.status),
            result=payload.result,
        )
    except CommandNotDelivered as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "command was not delivered") from exc

    if outcome is None:
        # Не 403: различие в ответах позволило бы перебором идентификаторов
        # выяснять, какие команды существуют в системе.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "command not found")

    command, accepted = outcome
    # Повтор после обрыва связи — не ошибка, но и не новое принятие.
    response.status_code = (
        status.HTTP_202_ACCEPTED if accepted else status.HTTP_200_OK
    )
    return {"status": command.status.value}
```

Добавить импорты: `import json`, `import uuid`, `CommandStatus`, `record_command_result`, `MAX_RESULT_BYTES`, `CommandNotDelivered`, `CommandResultRequest`.

- [x] **Шаг 5: Запустить тесты**

```bash
python -m pytest tests/test_command_result_endpoint.py tests/test_commands_service.py -q
```

Expected: PASS.

- [x] **Шаг 6: Проверить линт и типы, зафиксировать**

```bash
ruff check . && ruff format --check . && mypy barysguard
git add barysguard/ tests/
git commit -m "feat: idempotent command result endpoint"
```

---

## Задача 6: Операторский API конфигурации

**Files:**
- Modify: `server/barysguard/api/schemas.py`
- Modify: `server/barysguard/api/router.py`
- Test: `server/tests/test_operator_config_api.py`

**Interfaces:**
- Consumes: `current_user`, `require_role` из `barysguard.api.deps`; `AgentConfigDocument`, `effective_document`, `effective_config_for_agent`, `compute_config_version` (задача 1); `record_audit`.
- Produces: `ConfigResponse`, `EffectiveConfigResponse`; эндпоинты `GET|PUT /api/v1/config`, `GET|PUT|DELETE /api/v1/groups/{group_id}/config`, `GET /api/v1/agents/{agent_id}/config`.

- [x] **Шаг 1: Написать падающий тест**

Создать `server/tests/test_operator_config_api.py`. Пользователи создаются через `create_user`, как в `server/tests/test_operator_api.py`; полный документ для `PUT` берётся из `AgentConfigDocument`, потому что замена полная, а не частичная.

```python
import pytest
from sqlalchemy import select

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.config import AgentConfigDocument, merge_documents
from barysguard.services.users import create_user
from tests.helpers import enroll_agent


def _document(**overrides) -> dict:
    """Полный документ с точечной правкой: PUT заменяет целиком."""
    return merge_documents(AgentConfigDocument().model_dump(mode="json"), overrides)


async def _key(session, username: str, role: UserRole, scope_group_id=None) -> str:
    raw_key, user = await create_user(session, username=username, role=role)
    if scope_group_id is not None:
        user.scope_group_id = scope_group_id
    await session.commit()
    return raw_key


@pytest.mark.asyncio
async def test_admin_replaces_global_config(app_client, session):
    key = await _key(session, "cfg-admin", UserRole.ADMIN)
    headers = {"X-Api-Key": key}

    response = await app_client.put(
        "/api/v1/config",
        headers=headers,
        json={"document": _document(transport={"heartbeat_interval_seconds": 45})},
    )

    assert response.status_code == 200
    assert response.json()["document"]["transport"]["heartbeat_interval_seconds"] == 45

    read_back = await app_client.get("/api/v1/config", headers=headers)
    assert read_back.json()["document"]["transport"]["heartbeat_interval_seconds"] == 45


@pytest.mark.asyncio
async def test_operator_cannot_change_config(app_client, session):
    # Интервал в 86400 секунд бесшумно ослепляет весь флот: это изменение
    # того же веса, что отзыв сертификата.
    key = await _key(session, "cfg-operator", UserRole.OPERATOR)

    response = await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": _document(transport={"heartbeat_interval_seconds": 45})},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unknown_key_is_rejected(app_client, session):
    key = await _key(session, "cfg-typo", UserRole.ADMIN)

    response = await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": {"transport": {"heartbeat_intervall": 45}}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_out_of_range_interval_is_rejected(app_client, session):
    key = await _key(session, "cfg-range", UserRole.ADMIN)

    response = await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": _document(transport={"heartbeat_interval_seconds": 86400})},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_group_override_is_stored_and_removed(app_client, session):
    group = AgentGroup(name="branch")
    session.add(group)
    await session.flush()
    key = await _key(session, "cfg-group", UserRole.ADMIN)
    headers = {"X-Api-Key": key}

    put = await app_client.put(
        f"/api/v1/groups/{group.id}/config",
        headers=headers,
        json={"document": _document(logging={"level": "debug"})},
    )
    assert put.status_code == 200

    removed = await app_client.delete(f"/api/v1/groups/{group.id}/config", headers=headers)
    assert removed.status_code == 204

    after = await app_client.get(f"/api/v1/groups/{group.id}/config", headers=headers)
    assert after.status_code == 404


@pytest.mark.asyncio
async def test_effective_config_for_agent_is_visible(app_client, session):
    agent = await enroll_agent(app_client, session, "cfg-effective")
    key = await _key(session, "cfg-reader", UserRole.OPERATOR)

    response = await app_client.get(
        f"/api/v1/agents/{agent.agent_id}/config", headers={"X-Api-Key": key}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] > 0
    assert body["document"]["transport"]["heartbeat_interval_seconds"] == 30


@pytest.mark.asyncio
async def test_config_change_is_audited(app_client, session):
    key = await _key(session, "cfg-audited", UserRole.ADMIN)

    await app_client.put(
        "/api/v1/config",
        headers={"X-Api-Key": key},
        json={"document": _document(logging={"level": "warn"})},
    )

    entries = (await session.execute(select(AuditLog))).scalars().all()
    assert any(entry.action == "config.update" for entry in entries)


@pytest.mark.asyncio
async def test_scoped_operator_cannot_read_foreign_agent_config(app_client, session):
    own_group = AgentGroup(name="own")
    other_group = AgentGroup(name="other")
    session.add_all([own_group, other_group])
    await session.flush()

    foreign = await enroll_agent(app_client, session, "cfg-foreign", group_id=other_group.id)
    key = await _key(session, "cfg-scoped", UserRole.OPERATOR, scope_group_id=own_group.id)

    response = await app_client.get(
        f"/api/v1/agents/{foreign.agent_id}/config", headers={"X-Api-Key": key}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_config_requires_api_key(app_client):
    response = await app_client.get("/api/v1/config")

    assert response.status_code == 401
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
python -m pytest tests/test_operator_config_api.py -q
```

Expected: FAIL, `404 Not Found`.

- [x] **Шаг 3: Добавить схемы в `server/barysguard/api/schemas.py`**

```python
class ConfigUpdateRequest(BaseModel):
    document: AgentConfigDocument


class ConfigResponse(BaseModel):
    scope: str
    group_id: uuid.UUID | None
    document: dict[str, Any]
    version: int
    updated_at: datetime | None


class EffectiveConfigResponse(BaseModel):
    agent_id: uuid.UUID
    document: dict[str, Any]
    version: int
    applied_version: int
```

`applied_version` — то, что агент сообщил в последнем heartbeat. Расхождение с `version` показывает оператору, кто отстал.

- [x] **Шаг 4: Добавить вспомогательную функцию области видимости в `server/barysguard/api/deps.py`**

```python
async def agent_in_scope(
    session: AsyncSession, user: User, agent_id: uuid.UUID
) -> Agent | None:
    """Агент, видимый этому оператору. None означает «нет либо не виден».

    Отсутствие и невидимость отдаются одинаково: иначе перебор
    идентификаторов раскрыл бы состав чужого филиала.
    """
    agent = (
        await session.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        return None
    if user.scope_group_id is not None and agent.group_id != user.scope_group_id:
        return None
    return agent
```

Сравнение плоское, а не по поддереву: ровно так область видимости уже применяется в существующем `GET /api/v1/agents`. Расширение до поддерева — подпроект 4, и делать его здесь в одном месте из двух значило бы развести поведение.

- [x] **Шаг 5: Добавить эндпоинты в `server/barysguard/api/router.py`**

```python
async def _load_config_row(
    session: AsyncSession, scope: ConfigScope, group_id: uuid.UUID | None
) -> AgentConfig | None:
    statement = select(AgentConfig).where(AgentConfig.scope == scope)
    if group_id is not None:
        statement = statement.where(AgentConfig.group_id == group_id)
    return (await session.execute(statement)).scalar_one_or_none()


async def _store_config(
    session: AsyncSession,
    *,
    scope: ConfigScope,
    group_id: uuid.UUID | None,
    document: dict[str, Any],
    user: User,
) -> AgentConfig:
    row = await _load_config_row(session, scope, group_id)
    if row is None:
        row = AgentConfig(scope=scope, group_id=group_id)
        session.add(row)
    row.document = document
    row.updated_by = user.id
    await session.flush()

    await record_audit(
        session,
        user_id=user.id,
        action="config.update",
        target_type="agent_config",
        target_id=row.id,
        payload={"scope": scope.value, "group_id": str(group_id) if group_id else None},
    )
    return row


@router.get("/config", response_model=ConfigResponse)
async def read_global_config(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    row = await _load_config_row(session, ConfigScope.GLOBAL, None)
    # На пустой базе отдаём значения по умолчанию, а не 404: конфигурация
    # существует всегда, строка в таблице — лишь её переопределение.
    document = row.document if row else AgentConfigDocument().model_dump(mode="json")
    return ConfigResponse(
        scope=ConfigScope.GLOBAL.value,
        group_id=None,
        document=document,
        version=compute_config_version(document),
        updated_at=row.updated_at if row else None,
    )


@router.put("/config", response_model=ConfigResponse)
async def replace_global_config(
    payload: ConfigUpdateRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    document = payload.document.model_dump(mode="json")
    row = await _store_config(
        session, scope=ConfigScope.GLOBAL, group_id=None, document=document, user=user
    )
    return ConfigResponse(
        scope=ConfigScope.GLOBAL.value,
        group_id=None,
        document=document,
        version=compute_config_version(document),
        updated_at=row.updated_at,
    )


@router.get("/groups/{group_id}/config", response_model=ConfigResponse)
async def read_group_config(
    group_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    row = await _load_config_row(session, ConfigScope.GROUP, group_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group has no config override")
    return ConfigResponse(
        scope=ConfigScope.GROUP.value,
        group_id=group_id,
        document=row.document,
        version=compute_config_version(row.document),
        updated_at=row.updated_at,
    )


@router.put("/groups/{group_id}/config", response_model=ConfigResponse)
async def replace_group_config(
    group_id: uuid.UUID,
    payload: ConfigUpdateRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    group = (
        await session.execute(select(AgentGroup).where(AgentGroup.id == group_id))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")

    document = payload.document.model_dump(mode="json")
    row = await _store_config(
        session, scope=ConfigScope.GROUP, group_id=group_id, document=document, user=user
    )
    return ConfigResponse(
        scope=ConfigScope.GROUP.value,
        group_id=group_id,
        document=document,
        version=compute_config_version(document),
        updated_at=row.updated_at,
    )


@router.delete("/groups/{group_id}/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group_config(
    group_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await _load_config_row(session, ConfigScope.GROUP, group_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group has no config override")

    await session.delete(row)
    await record_audit(
        session,
        user_id=user.id,
        action="config.delete",
        target_type="agent_config",
        target_id=row.id,
        payload={"scope": ConfigScope.GROUP.value, "group_id": str(group_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents/{agent_id}/config", response_model=EffectiveConfigResponse)
async def read_effective_agent_config(
    agent_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EffectiveConfigResponse:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    document, version = await effective_config_for_agent(session, agent)
    return EffectiveConfigResponse(
        agent_id=agent.id,
        document=document,
        version=version,
        applied_version=agent.config_version,
    )
```

Обратить внимание: полная замена документа (`PUT` без частичного слияния) намеренна. Частичное обновление на уровне HTTP плюс слияние по дереву групп дало бы два разных механизма слияния в одном контуре, и разбираться, какой применился, пришлось бы по исходникам.

- [x] **Шаг 6: Запустить тесты**

```bash
python -m pytest tests/test_operator_config_api.py -q
```

Expected: PASS, 9 тестов.

- [x] **Шаг 7: Проверить линт и типы, зафиксировать**

```bash
ruff check . && ruff format --check . && mypy barysguard
git add barysguard/api/ tests/test_operator_config_api.py
git commit -m "feat: operator api for agent configuration"
```

---

## Задача 7: Операторский API команд и вычисляемый статус offline

**Files:**
- Modify: `server/barysguard/api/schemas.py`
- Modify: `server/barysguard/api/router.py`
- Create: `server/barysguard/services/presence.py`
- Test: `server/tests/test_operator_commands_api.py`
- Test: `server/tests/test_agent_presence.py`

**Interfaces:**
- Consumes: `queue_command`, `CommandType`, `CommandStatus` (задача 3); `agent_in_scope` (задача 6); `global_heartbeat_interval` (задача 1).
- Produces:
  - `OFFLINE_MISSED_INTERVALS: int = 3`
  - `derive_status(agent: Agent, interval_seconds: int, now: datetime) -> str`
  - `CreateCommandRequest`, `CommandResponse`; эндпоинты `POST|GET /api/v1/agents/{agent_id}/commands`.

- [x] **Шаг 1: Написать падающий тест на присутствие**

Создать `server/tests/test_agent_presence.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.services.presence import OFFLINE_MISSED_INTERVALS, derive_status


def _agent(**overrides) -> Agent:
    agent = Agent(
        machine_id=f"machine-{uuid.uuid4().hex}",
        hostname="ws-1",
        os="linux",
        os_version="24.04",
        arch="amd64",
        agent_version="0.1.0",
        status=AgentStatus.ACTIVE,
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


def test_agent_without_heartbeat_is_pending():
    # Зарегистрировался, но ещё не выходил на связь — это не то же самое,
    # что пропавший.
    now = datetime.now(UTC)
    assert derive_status(_agent(last_heartbeat_at=None), 30, now) == "pending"


def test_recent_heartbeat_keeps_active():
    now = datetime.now(UTC)
    agent = _agent(last_heartbeat_at=now - timedelta(seconds=10))
    assert derive_status(agent, 30, now) == "active"


def test_missed_intervals_make_agent_offline():
    now = datetime.now(UTC)
    gap = timedelta(seconds=30 * OFFLINE_MISSED_INTERVALS + 1)
    agent = _agent(last_heartbeat_at=now - gap)
    assert derive_status(agent, 30, now) == "offline"


def test_quarantine_survives_silence():
    # Карантин снимает только оператор: молчание его не отменяет.
    now = datetime.now(UTC)
    agent = _agent(
        status=AgentStatus.QUARANTINED,
        last_heartbeat_at=now - timedelta(days=1),
    )
    assert derive_status(agent, 30, now) == "quarantined"


def test_revoked_survives_silence():
    now = datetime.now(UTC)
    agent = _agent(status=AgentStatus.REVOKED, last_heartbeat_at=now - timedelta(days=1))
    assert derive_status(agent, 30, now) == "revoked"
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
python -m pytest tests/test_agent_presence.py -q
```

Expected: `ModuleNotFoundError: No module named 'barysguard.services.presence'`

- [x] **Шаг 3: Создать `server/barysguard/services/presence.py`**

```python
from datetime import datetime, timedelta

from barysguard.db.models.agent import Agent, AgentStatus

# Один пропущенный heartbeat — сетевая икота, три подряд — уже молчание.
OFFLINE_MISSED_INTERVALS = 3

# Статусы, которые снимает только оператор. Молчание агента их не меняет.
OPERATOR_CONTROLLED = (AgentStatus.QUARANTINED, AgentStatus.REVOKED)


def derive_status(agent: Agent, interval_seconds: int, now: datetime) -> str:
    """Статус агента с поправкой на давность последнего heartbeat.

    Вычисляется при чтении, а не хранится: единственным источником истины
    остаётся last_heartbeat_at, и порог живёт в одном месте. Фоновый сторож,
    порождающий событие безопасности о пропаже, появится в плане 1C.
    """
    if agent.status in OPERATOR_CONTROLLED:
        return agent.status.value

    if agent.last_heartbeat_at is None:
        return AgentStatus.PENDING.value

    deadline = timedelta(seconds=interval_seconds * OFFLINE_MISSED_INTERVALS)
    if now - agent.last_heartbeat_at > deadline:
        return AgentStatus.OFFLINE.value

    return agent.status.value
```

- [x] **Шаг 4: Написать падающий тест на операторский API команд**

Создать `server/tests/test_operator_commands_api.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.users import create_user
from tests.helpers import enroll_agent


async def _key(session, username: str, role: UserRole, scope_group_id=None) -> str:
    raw_key, user = await create_user(session, username=username, role=role)
    if scope_group_id is not None:
        user.scope_group_id = scope_group_id
    await session.commit()
    return raw_key


@pytest.mark.asyncio
async def test_operator_queues_command(app_client, session):
    agent = await enroll_agent(app_client, session, "cmd-queue")
    key = await _key(session, "cmd-operator", UserRole.OPERATOR)

    response = await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["type"] == "ping"


@pytest.mark.asyncio
async def test_unknown_command_type_is_rejected(app_client, session):
    # Строгое перечисление: опечатка в очереди обнаружилась бы иначе только
    # по ошибке от агента через полминуты.
    agent = await enroll_agent(app_client, session, "cmd-typo")
    key = await _key(session, "cmd-typo-operator", UserRole.OPERATOR)

    response = await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "reboot", "payload": {}, "ttl_seconds": 3600},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_command_creation_is_audited(app_client, session):
    agent = await enroll_agent(app_client, session, "cmd-audit")
    key = await _key(session, "cmd-audit-operator", UserRole.OPERATOR)

    await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    entries = (await session.execute(select(AuditLog))).scalars().all()
    assert any(entry.action == "command.create" for entry in entries)


@pytest.mark.asyncio
async def test_scoped_operator_cannot_command_foreign_agent(app_client, session):
    own_group = AgentGroup(name="own")
    other_group = AgentGroup(name="other")
    session.add_all([own_group, other_group])
    await session.flush()

    foreign = await enroll_agent(app_client, session, "cmd-foreign", group_id=other_group.id)
    key = await _key(session, "cmd-scoped", UserRole.OPERATOR, scope_group_id=own_group.id)

    response = await app_client.post(
        f"/api/v1/agents/{foreign.agent_id}/commands",
        headers={"X-Api-Key": key},
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_command_history_is_listed(app_client, session):
    agent = await enroll_agent(app_client, session, "cmd-history")
    key = await _key(session, "cmd-history-operator", UserRole.OPERATOR)
    headers = {"X-Api-Key": key}

    await app_client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        headers=headers,
        json={"type": "ping", "payload": {}, "ttl_seconds": 3600},
    )

    response = await app_client.get(
        f"/api/v1/agents/{agent.agent_id}/commands", headers=headers
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_silent_agent_is_listed_offline(app_client, session):
    enrolled = await enroll_agent(app_client, session, "cmd-silent")
    key = await _key(session, "cmd-silent-operator", UserRole.OPERATOR)

    agent = await session.get(Agent, enrolled.agent_id)
    # Три пропущенных интервала по 30 секунд.
    agent.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=200)
    await session.commit()

    response = await app_client.get("/api/v1/agents", headers={"X-Api-Key": key})

    entry = next(
        item for item in response.json() if item["id"] == str(enrolled.agent_id)
    )
    assert entry["status"] == "offline"
```

- [x] **Шаг 5: Добавить схемы в `server/barysguard/api/schemas.py`**

```python
class CreateCommandRequest(BaseModel):
    type: CommandType
    payload: dict[str, Any] = Field(default_factory=dict)
    # Час по умолчанию, неделя максимум: команда со сроком годности в месяц
    # равносильна команде без срока годности.
    ttl_seconds: int = Field(default=3600, ge=60, le=604800)


class CommandResponse(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    created_at: datetime
    sent_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime
```

- [x] **Шаг 6: Добавить эндпоинты и правку списка агентов в `server/barysguard/api/router.py`**

```python
def _command_response(command: Command) -> CommandResponse:
    return CommandResponse(
        id=command.id,
        type=command.type.value,
        status=command.status.value,
        payload=command.payload,
        result=command.result,
        created_at=command.created_at,
        sent_at=command.sent_at,
        completed_at=command.completed_at,
        expires_at=command.expires_at,
    )


@router.post(
    "/agents/{agent_id}/commands",
    response_model=CommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_command(
    agent_id: uuid.UUID,
    payload: CreateCommandRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CommandResponse:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    command = await queue_command(
        session,
        agent_id=agent.id,
        command_type=payload.type,
        payload=payload.payload,
        created_by=user.id,
        ttl_seconds=payload.ttl_seconds,
    )

    await record_audit(
        session,
        user_id=user.id,
        action="command.create",
        target_type="command",
        target_id=command.id,
        payload={"agent_id": str(agent.id), "type": payload.type.value},
    )
    return _command_response(command)


@router.get("/agents/{agent_id}/commands", response_model=list[CommandResponse])
async def list_commands(
    agent_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CommandResponse]:
    agent = await agent_in_scope(session, user, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    commands = (
        (
            await session.execute(
                select(Command)
                .where(Command.agent_id == agent.id)
                .order_by(Command.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [_command_response(command) for command in commands]
```

В существующем `list_agents` заменить `status=agent.status.value` на вычисляемый:

```python
    # Интервал берётся из глобальной конфигурации: собирать эффективный
    # документ на каждую строку списка значило бы обходить дерево групп
    # тысячи раз ради сдвига границы на десятки секунд.
    interval = await global_heartbeat_interval(session)
    now = datetime.now(UTC)

    agents = (await session.execute(statement)).scalars().all()
    return [
        AgentSummary(
            id=agent.id,
            hostname=agent.hostname,
            os=agent.os,
            agent_version=agent.agent_version,
            status=derive_status(agent, interval, now),
            last_heartbeat_at=agent.last_heartbeat_at,
        )
        for agent in agents
    ]
```

- [x] **Шаг 7: Запустить тесты**

```bash
python -m pytest tests/test_agent_presence.py tests/test_operator_commands_api.py -q
```

Expected: PASS, 11 тестов.

- [x] **Шаг 8: Проверить линт и типы, зафиксировать**

```bash
ruff check . && ruff format --check . && mypy barysguard
git add barysguard/api/ barysguard/services/presence.py tests/
git commit -m "feat: operator command api and derived offline status"
```

---

## Задача 8: Миграция данных, контракт и сквозная проверка

**Files:**
- Create: миграция данных в `server/alembic/versions/`
- Modify: `api/gateway-v1.yaml`
- Modify: `docs/QUICKSTART.md`
- Test: весь набор

- [x] **Шаг 1: Создать миграцию с начальной глобальной конфигурацией**

```bash
alembic revision -m "seed global agent config"
```

В созданном файле:

```python
import json
import uuid

import sqlalchemy as sa
from alembic import op

# Значения дублируются здесь намеренно: миграция обязана оставаться
# воспроизводимой, а импорт из приложения привязал бы её к текущей
# редакции AgentConfigDocument.
DEFAULT_DOCUMENT = {
    "transport": {
        "heartbeat_interval_seconds": 30,
        "event_batch_max": 500,
        "event_batch_max_bytes": 4194304,
        "backoff_base_seconds": 1,
        "backoff_max_seconds": 300,
    },
    "buffer": {"max_bytes": 524288000, "max_age_days": 7},
    "logging": {"level": "info"},
    "policies": {},
}


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO agent_configs (id, scope, group_id, document, updated_at) "
            "VALUES (:id, 'GLOBAL', NULL, CAST(:document AS jsonb), now())"
        ).bindparams(id=str(uuid.uuid4()), document=json.dumps(DEFAULT_DOCUMENT))
    )


def downgrade() -> None:
    op.execute("DELETE FROM agent_configs WHERE scope = 'GLOBAL'")
```

- [x] **Шаг 2: Прогнать весь набор тестов**

```bash
python -m pytest -q
```

Expected: PASS. Ожидаемое количество — 58 прежних плюс примерно 55 новых.

Если падают тесты 1A, разбираться по существу: наиболее вероятная причина — новые таблицы не попали в `TRUNCATE` фикстуры `app_client`, и данные текут между тестами.

- [x] **Шаг 3: Проверить, что миграции накатываются и откатываются**

```bash
alembic upgrade head
alembic downgrade -3
alembic upgrade head
```

Expected: без ошибок. Откат частичных индексов и `CHECK` требует явных имён — они заданы в моделях, поэтому должен пройти.

- [x] **Шаг 4: Перевыгрузить контракт OpenAPI**

Посмотреть, как это делалось в шаге 9 задачи 7 плана 1A (`docs/superpowers/plans/2026-09-08-server-foundation-pki.md`), и повторить той же командой, чтобы формат файла не разошёлся.

```bash
python -c "import json, yaml; from barysguard.main import create_app; \
print(yaml.safe_dump(create_app().openapi(), allow_unicode=True, sort_keys=False))" \
  > ../api/gateway-v1.yaml
```

Убедиться, что в файле появились `/gateway/v1/heartbeat`, `/gateway/v1/config` и `/gateway/v1/commands/{command_id}/result`.

- [x] **Шаг 5: Дописать раздел в `docs/QUICKSTART.md`**

После существующей проверки регистрации добавить:

````markdown
## Проверка heartbeat и команд

```bash
# Постановка команды (подставить agent_id из ответа на регистрацию)
curl -s -X POST localhost:8000/api/v1/agents/<agent_id>/commands \
     -H "X-Api-Key: <ключ оператора>" -H "Content-Type: application/json" \
     -d '{"type": "ping", "payload": {}, "ttl_seconds": 3600}'

# Heartbeat агента: команда должна прийти ровно один раз
curl -s -X POST localhost:8000/gateway/v1/heartbeat \
     -H "X-Client-Verify: SUCCESS" -H "X-Client-Serial: <серийный номер>" \
     -H "Content-Type: application/json" \
     -d '{"agent_version": "0.1.0", "config_version": 0,
          "sent_at": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
          "buffered_events": 0, "buffer_bytes": 0}'

# Повторный heartbeat: список команд должен быть пуст
```

## Проверка наследования конфигурации

```bash
# Правка глобальной конфигурации меняет версию для всех агентов
curl -s -X PUT localhost:8000/api/v1/config \
     -H "X-Api-Key: <ключ администратора>" -H "Content-Type: application/json" \
     -d '{"document": {"transport": {"heartbeat_interval_seconds": 15},
                       "buffer": {}, "logging": {}, "policies": {}}}'

# Агент видит новую версию и забирает документ
curl -s localhost:8000/gateway/v1/config \
     -H "X-Client-Verify: SUCCESS" -H "X-Client-Serial: <серийный номер>"
```
````

- [x] **Шаг 6: Выполнить проверку из QUICKSTART целиком**

Поднять стек, пройти инструкцию от создания токена до повторного heartbeat. Убедиться глазами: команда приходит один раз, второй heartbeat отдаёт пустой список, правка конфигурации меняет `config_version` в ответе.

- [x] **Шаг 7: Финальная проверка и фиксация**

```bash
ruff check . && ruff format --check . && mypy barysguard && python -m pytest -q
git add ../api/gateway-v1.yaml ../docs/QUICKSTART.md alembic/versions/
git commit -m "docs: contract and quickstart for heartbeat, config and commands"
```

---

## Что остаётся за пределами этого плана

| Что | Где |
|---|---|
| Go-агент: регистрация, heartbeat, offline-буфер | План 1B-agent |
| `POST /events`, партиционирование, воркер, артефакты | План 1C |
| Фоновый сторож пропавших heartbeat, событие канала `agent` | План 1C |
| Метрики Prometheus, нагрузочный тест на 5000 агентов, CI | План 1D |
| Боевые команды: изоляция хоста, завершение процесса | Подпроект 2 |
| Содержимое `policies` | Подпроект 3 |
