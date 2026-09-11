# BarysGuard DLP — план 1A: фундамент сервера и PKI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять серверную часть BarysGuard на Linux: FastAPI + PostgreSQL с миграциями, внутренний удостоверяющий центр, регистрация агентов по одноразовым токенам, аутентификация по mTLS, отзыв сертификатов и аудит действий операторов.

**Architecture:** Асинхронный FastAPI поверх SQLAlchemy 2.0 и asyncpg. Внутренний CA на `cryptography` хранит ключ на файловой системе, зашифрованным парольной фразой из окружения. Агент регистрируется одноразовым токеном, присылая CSR; сервер игнорирует субъект CSR, присваивает `agent_id` и выпускает сертификат. Дальнейшие запросы агента аутентифицируются по клиентскому сертификату, данные которого nginx передаёт заголовками; личность определяется серийным номером.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async), asyncpg, Alembic, pydantic-settings, cryptography, pytest + pytest-asyncio + testcontainers, nginx.

**Spec:** `docs/superpowers/specs/2026-09-08-dlp-transport-core-design.md`

## Global Constraints

- Python `>=3.12`. Go в этом плане не используется — он в плане 1B.
- Лицензии зависимостей: только пермиссивные. **GPL и AGPL запрещены** — они лишают возможности распространять продукт закрытым.
- Все переменные окружения имеют префикс `BG_`.
- Секреты (`BG_CA_PASSPHRASE`, `BG_ARTIFACT_MASTER_KEY`, `BG_DATABASE_URL`) поступают только из окружения. В репозиторий не попадают никогда.
- Интеграционные тесты работают **на настоящем PostgreSQL** через testcontainers. SQLite запрещён: партиционирование, `SKIP LOCKED` и `ON CONFLICT` ведут себя на нём иначе и дают ложную уверенность.
- Личность агента определяется **серийным номером сертификата**, никогда — полем в теле запроса.
- Токены регистрации хранятся в БД **только хешем SHA-256**, как пароли.
- Срок сертификата агента — 90 дней. Порог автопродления — 60 дней. TTL токена регистрации — 24 часа.
- Ключ агента генерируется на хосте и по сети не передаётся. Субъект CSR сервером игнорируется.
- Именование в БД: `snake_case`, таблицы во множественном числе.
- Каждая задача завершается коммитом. Формат сообщения: `feat:`, `fix:`, `test:`, `chore:`, `refactor:`.

---

## Структура файлов

```
barysguard/
├── .gitignore                                    Задача 0
├── legacy/
│   ├── console/                                  Задача 0 — заморожено
│   └── agent-python/                             Задача 0 — заморожено
├── api/
│   └── gateway-v1.yaml                           Задача 7 — контракт
├── deploy/
│   └── nginx/barysguard.conf                     Задача 8
└── server/
    ├── pyproject.toml                            Задача 1
    ├── alembic.ini                               Задача 2
    ├── alembic/
    │   ├── env.py                                Задача 2
    │   └── versions/                             Задачи 2,3,4,6,10,11
    ├── barysguard/
    │   ├── main.py                 фабрика приложения          Задача 1
    │   ├── core/
    │   │   ├── config.py           настройки из BG_*           Задача 1
    │   │   ├── logging.py          JSON-журналирование         Задача 1
    │   │   └── errors.py           доменные исключения         Задача 4
    │   ├── db/
    │   │   ├── base.py             DeclarativeBase, соглашения Задача 2
    │   │   ├── session.py          движок, сессии, зависимость Задача 2
    │   │   └── models/
    │   │       ├── agent.py        AgentGroup, Agent           Задача 3
    │   │       ├── enrollment.py   EnrollmentToken             Задача 4
    │   │       ├── certificate.py  AgentCertificate            Задача 6
    │   │       ├── audit.py        AuditLog                    Задача 10
    │   │       └── user.py         User                        Задача 11
    │   ├── pki/
    │   │   ├── ca.py               создание CA, подпись CSR    Задача 5
    │   │   └── service.py          выпуск/отзыв в связке с БД  Задача 6
    │   ├── services/
    │   │   ├── enrollment.py       создание и гашение токенов  Задача 4
    │   │   └── audit.py            запись с цепочкой хешей     Задача 10
    │   ├── gateway/
    │   │   ├── router.py           /gateway/v1/*               Задачи 7,9
    │   │   ├── deps.py             личность агента по mTLS     Задача 8
    │   │   └── schemas.py          модели запросов и ответов   Задача 7
    │   └── api/
    │       ├── router.py           /api/v1/*                   Задача 11
    │       ├── deps.py             личность оператора          Задача 11
    │       └── schemas.py                                      Задача 11
    └── tests/
        ├── conftest.py                                         Задача 2
        ├── test_health.py                                      Задача 1
        ├── test_migrations.py                                  Задача 2
        ├── test_enrollment_service.py                          Задача 4
        ├── test_ca.py                                          Задача 5
        ├── test_certificate_service.py                         Задача 6
        ├── test_enroll_endpoint.py                             Задача 7
        ├── test_mtls_identity.py                               Задача 8
        ├── test_renew_endpoint.py                              Задача 9
        ├── test_audit.py                                       Задача 10
        └── test_operator_api.py                                Задача 11
```

Разделение по ответственности, а не по слою: `pki/` содержит и криптографию, и её связку с БД, потому что они меняются вместе. `services/` — доменные операции, не зависящие от HTTP, чтобы их можно было тестировать без поднятия приложения.

---

## Задача 0: Реструктуризация репозитория и отзыв скомпрометированных ключей

**Files:**
- Create: `.gitignore`
- Move: всё текущее содержимое → `legacy/console/` и `legacy/agent-python/`
- Delete from index: `config.json`, `hosts.json`

**Interfaces:**
- Consumes: ничего
- Produces: чистый корень репозитория, в котором далее создаются `server/`, `agent/`, `api/`, `deploy/`

- [ ] **Шаг 1: Отозвать скомпрометированные ключи API**

Это действие выполняется руками в веб-интерфейсах, автоматизировать его нельзя. Файл `config.json` находится в истории git с действующими ключами, то есть они считаются публично раскрытыми.

1. Зайти на https://www.virustotal.com/gui/my-apikey → перевыпустить ключ.
2. Зайти на https://www.abuseipdb.com/account/api → удалить существующий ключ, создать новый.
3. Новые ключи **никуда не записывать в репозиторий**. Они понадобятся в подпроекте 3 и будут задаваться через `BG_VT_API_KEY` и `BG_ABUSEIPDB_KEY`.

- [ ] **Шаг 2: Убрать файлы с секретами из индекса git**

```bash
git rm --cached config.json hosts.json
```

- [ ] **Шаг 3: Создать `.gitignore`**

```gitignore
# Секреты и локальная конфигурация
.env
config.json
hosts.json
*.pem
*.key

# Python
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Сборка
build/
dist/
*.spec.bak

# Go
agent/bin/

# Среда разработки
.idea/
.vscode/
```

- [ ] **Шаг 4: Перенести существующий код в `legacy/`**

```bash
mkdir -p legacy/console legacy/agent-python

git mv agent/* legacy/agent-python/
rmdir agent

git mv main.py ui workers core styles.py config.py constants.py \
       check.py report.html main.spec build.bat run_debug.bat \
       requirements.txt yara64.exe README.txt legacy/console/
```

Файлы `constants.py`, `core/yara_engine.py` и `core/hash_utils.py` остаются в `legacy/console/`. Они будут скопированы в `server/barysguard/engines/` в подпроекте 3, когда появится движок IoC. Переносить их сейчас незачем — на них ещё нечему опираться.

- [ ] **Шаг 5: Создать каркас новых каталогов**

```bash
mkdir -p server/barysguard/{core,db/models,pki,services,gateway,api}
mkdir -p server/tests server/alembic/versions
mkdir -p api deploy/nginx
touch server/barysguard/__init__.py
touch server/barysguard/{core,db,db/models,pki,services,gateway,api}/__init__.py
```

- [ ] **Шаг 6: Проверить, что замороженное приложение осталось работоспособным**

```bash
cd legacy/console && python -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.py')]" && echo OK
```

Ожидается: `OK`. Это проверка того, что перемещение не сломало синтаксис файлов. Полный запуск приложения требует PyQt6 и здесь не нужен.

- [ ] **Шаг 7: Зафиксировать**

```bash
git add -A
git commit -m "chore: freeze legacy console and agent, prepare monorepo layout

Существующее приложение PyQt6 и Python-агент перенесены в legacy/ и
заморожены. PyQt6 распространяется по GPL v3, что несовместимо с
распространением закрытого продукта.

config.json и hosts.json удалены из индекса; ключи VirusTotal и
AbuseIPDB отозваны и перевыпущены."
```

---

## Задача 1: Каркас сервера, конфигурация, журналирование

**Files:**
- Create: `server/pyproject.toml`
- Create: `server/barysguard/core/config.py`
- Create: `server/barysguard/core/logging.py`
- Create: `server/barysguard/main.py`
- Test: `server/tests/test_health.py`

**Interfaces:**
- Consumes: ничего
- Produces:
  - `Settings` — класс настроек; `get_settings() -> Settings` с кешированием
  - `create_app() -> FastAPI` — фабрика приложения
  - `setup_logging(level: str) -> None`

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_health.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from barysguard.main import create_app


@pytest.mark.asyncio
async def test_health_returns_ok():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_read_environment_with_bg_prefix(monkeypatch):
    from barysguard.core.config import Settings

    monkeypatch.setenv("BG_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("BG_AGENT_CERT_DAYS", "45")

    settings = Settings()

    assert settings.log_level == "DEBUG"
    assert settings.agent_cert_days == 45


def test_settings_defaults_match_spec():
    from barysguard.core.config import Settings

    settings = Settings()

    assert settings.agent_cert_days == 90
    assert settings.agent_cert_renew_after_days == 60
    assert settings.enrollment_token_ttl_hours == 24
    assert settings.heartbeat_interval_seconds == 30
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && python -m pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.main'`

- [ ] **Шаг 3: Создать `server/pyproject.toml`**

```toml
[project]
name = "barysguard-server"
version = "0.1.0"
description = "BarysGuard DLP server"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "cryptography>=44.0",
    "python-json-logger>=3.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.28",
    "testcontainers[postgres]>=4.8",
    "ruff>=0.8",
    "mypy>=1.13",
]

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["barysguard*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "S", "ASYNC"]
ignore = ["S101"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
```

- [ ] **Шаг 4: Создать `server/barysguard/core/config.py`**

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервера. Все переменные окружения имеют префикс BG_."""

    model_config = SettingsConfigDict(
        env_prefix="BG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Подключение к базе данных
    database_url: str = (
        "postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard"
    )

    # Сетевые параметры
    listen_host: str = "127.0.0.1"
    listen_port: int = 8000

    # Журналирование
    log_level: str = "INFO"

    # Удостоверяющий центр
    ca_dir: Path = Path("/var/lib/barysguard/pki")
    ca_passphrase: str = ""
    ca_common_name: str = "BarysGuard Internal CA"
    ca_valid_days: int = 3650

    # Сертификаты агентов
    agent_cert_days: int = 90
    agent_cert_renew_after_days: int = 60

    # Регистрация агентов
    enrollment_token_ttl_hours: int = 24

    # Опрос команд агентом
    heartbeat_interval_seconds: int = 30

    # Хранилище артефактов (используется в плане 1C)
    artifact_path: Path = Path("/var/lib/barysguard/artifacts")
    artifact_master_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Шаг 5: Создать `server/barysguard/core/logging.py`**

```python
import logging
import sys

from pythonjsonlogger.json import JsonFormatter


def setup_logging(level: str = "INFO") -> None:
    """Переключает корневой журнал на структурированный вывод в JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
```

- [ ] **Шаг 6: Создать `server/barysguard/main.py`**

```python
from fastapi import FastAPI

from barysguard.core.config import get_settings
from barysguard.core.logging import setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="BarysGuard DLP Server",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Процесс жив. Не проверяет зависимости — для этого /ready."""
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Шаг 7: Установить зависимости и запустить тесты**

```bash
cd server
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip install -e ".[dev]"
.venv/bin/python -m pytest tests/test_health.py -v
```

Expected: 3 passed

- [ ] **Шаг 8: Зафиксировать**

```bash
git add server/pyproject.toml server/barysguard server/tests
git commit -m "feat: server skeleton with settings and structured logging"
```

---

## Задача 2: Основание базы данных и миграции

**Files:**
- Create: `server/barysguard/db/base.py`
- Create: `server/barysguard/db/session.py`
- Create: `server/alembic.ini`
- Create: `server/alembic/env.py`
- Create: `server/alembic/script.py.mako`
- Create: `server/tests/conftest.py`
- Modify: `server/barysguard/main.py`
- Test: `server/tests/test_migrations.py`

**Interfaces:**
- Consumes: `get_settings()` из задачи 1
- Produces:
  - `Base` — общий `DeclarativeBase` со соглашением об именовании ограничений
  - `create_engine_from_url(url: str) -> AsyncEngine`
  - `session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]`
  - `get_session() -> AsyncIterator[AsyncSession]` — зависимость FastAPI
  - `reset_session_state() -> None` — сброс кешированных движка и фабрики
  - `GET /ready` → `200 {"status": "ok", "database": "ok"}` либо `503`
  - Фикстуры pytest: `database_url` (сессионная), `migrated_database_url` (сессионная), `session` (на тест, с откатом)

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_migrations.py`:

```python
import pytest
from sqlalchemy import text

from barysguard.db.session import create_engine_from_url


@pytest.mark.asyncio
async def test_migrations_apply_and_roll_back(migrated_database_url):
    """Миграции применяются до head. Проверяется по таблице alembic_version."""
    engine = create_engine_from_url(migrated_database_url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar_one()

    assert version is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_is_reachable(session):
    result = await session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_ready_reports_ok_when_database_is_available(migrated_database_url, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from barysguard.core.config import get_settings
    from barysguard.db.session import reset_session_state
    from barysguard.main import create_app

    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    get_settings.cache_clear()
    reset_session_state()

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    get_settings.cache_clear()
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


@pytest.mark.asyncio
async def test_ready_reports_failure_when_database_is_unavailable(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from barysguard.core.config import get_settings
    from barysguard.db.session import reset_session_state
    from barysguard.main import create_app

    monkeypatch.setenv(
        "BG_DATABASE_URL", "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nothing"
    )
    get_settings.cache_clear()
    reset_session_state()

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    get_settings.cache_clear()
    reset_session_state()
    assert response.status_code == 503
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: FAIL — `fixture 'migrated_database_url' not found`

- [ ] **Шаг 3: Создать `server/barysguard/db/base.py`**

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Явное соглашение об именовании обязательно: без него Alembic генерирует
# для ограничений имена, назначенные СУБД, и автоматический откат миграций
# перестаёт работать.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

- [ ] **Шаг 4: Создать `server/barysguard/db/session.py`**

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from barysguard.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine_from_url(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, pool_size=20, max_overflow=10)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_engine_from_url(get_settings().database_url)
        _sessionmaker = session_factory(_engine)
    return _sessionmaker


def reset_session_state() -> None:
    """Сбрасывает кешированные движок и фабрику сессий.

    Нужна тестам, меняющим BG_DATABASE_URL: без сброса приложение продолжит
    ходить в базу, выбранную при первом обращении, и смена настроек
    не даст никакого эффекта.
    """
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI. Фиксирует транзакцию при успехе, откатывает при исключении."""
    async with _get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

- [ ] **Шаг 5: Создать конфигурацию Alembic**

`server/alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(slug)s
version_path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`server/alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`server/alembic/env.py`:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from barysguard.core.config import get_settings
from barysguard.db.base import Base

# Импорт моделей обязателен: без него Base.metadata пуста и автогенерация
# миграций молча создаёт пустые ревизии.
import barysguard.db.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    from barysguard.db.session import create_engine_from_url

    engine = create_engine_from_url(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Шаг 6: Создать `server/barysguard/db/models/__init__.py`**

```python
"""Импорт всех моделей. Alembic полагается на этот модуль для автогенерации."""
```

- [ ] **Шаг 7: Создать `server/tests/conftest.py`**

```python
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.postgres import PostgresContainer

from barysguard.db.session import create_engine_from_url, session_factory


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    return (
        f"postgresql+asyncpg://{postgres_container.username}:"
        f"{postgres_container.password}@{host}:{port}/{postgres_container.dbname}"
    )


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    """Применяет все миграции один раз на сессию тестов."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def session(migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    """Сессия на один тест. По завершении всё откатывается — тесты не влияют друг на друга."""
    engine = create_engine_from_url(migrated_database_url)
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = session_factory(engine)

    async with maker(bind=connection) as db_session:
        yield db_session

    await transaction.rollback()
    await connection.close()
    await engine.dispose()
```

- [ ] **Шаг 8: Добавить эндпоинт `/ready` в `server/barysguard/main.py`**

Добавить в `create_app()` рядом с `/health`:

```python
    @app.get("/ready", tags=["ops"])
    async def ready(response: Response) -> dict[str, str]:
        """Готовность обслуживать запросы: база доступна.

        Отделено от /health намеренно. Балансировщик снимает трафик по
        /ready, но не перезапускает процесс — перезапуск при недоступной
        базе не помогает и только удлиняет простой.
        """
        from sqlalchemy import text

        from barysguard.db.session import _get_sessionmaker

        try:
            async with _get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable", "database": "unavailable"}

        return {"status": "ok", "database": "ok"}
```

Добавить в импорты `server/barysguard/main.py`:

```python
from fastapi import FastAPI, Response, status
```

- [ ] **Шаг 9: Создать начальную миграцию**

```bash
cd server
.venv/bin/alembic revision -m "initial empty baseline"
```

Открыть созданный файл в `alembic/versions/` и оставить `upgrade()` и `downgrade()` с `pass`. Это базовая ревизия, от которой пойдут остальные.

- [ ] **Шаг 10: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: 4 passed

Требуется работающий Docker — testcontainers поднимает настоящий PostgreSQL.

- [ ] **Шаг 11: Зафиксировать**

```bash
git add server/alembic.ini server/alembic server/barysguard server/tests
git commit -m "feat: database foundation with alembic migrations and readiness probe"
```

---

## Задача 3: Модели групп и агентов

**Files:**
- Create: `server/barysguard/db/models/agent.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Create: `server/alembic/versions/<timestamp>_agents_and_groups.py`
- Test: `server/tests/test_agent_models.py`

**Interfaces:**
- Consumes: `Base` из задачи 2
- Produces:
  - `AgentStatus` — строковое перечисление: `pending`, `active`, `offline`, `quarantined`, `revoked`
  - `AgentGroup(id: UUID, name: str, parent_id: UUID | None, created_at: datetime)`
  - `Agent(id: UUID, machine_id: str, hostname: str, os: str, os_version: str, arch: str, agent_version: str, group_id: UUID | None, status: AgentStatus, enrolled_at: datetime, last_heartbeat_at: datetime | None, clock_skew_ms: int, config_version: int, last_ip: str | None, tags: dict)`

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_agent_models.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from barysguard.db.models.agent import Agent, AgentGroup, AgentStatus


@pytest.mark.asyncio
async def test_agent_is_created_with_defaults(session):
    group = AgentGroup(name="Бухгалтерия")
    session.add(group)
    await session.flush()

    agent = Agent(
        machine_id="4C4C4544-0043-5A10-8046-B7C04F335931",
        hostname="ACC-PC-01",
        os="windows",
        os_version="10.0.26100",
        arch="amd64",
        agent_version="0.1.0",
        group_id=group.id,
    )
    session.add(agent)
    await session.flush()

    assert agent.id is not None
    assert agent.status == AgentStatus.PENDING
    assert agent.config_version == 0
    assert agent.clock_skew_ms == 0
    assert agent.last_heartbeat_at is None
    assert agent.tags == {}


@pytest.mark.asyncio
async def test_machine_id_is_unique(session):
    for _ in range(2):
        session.add(
            Agent(
                machine_id="DUPLICATE-MACHINE-ID",
                hostname="host",
                os="linux",
                os_version="6.8.0",
                arch="amd64",
                agent_version="0.1.0",
            )
        )

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_groups_form_a_tree(session):
    root = AgentGroup(name="Организация")
    session.add(root)
    await session.flush()

    child = AgentGroup(name="Филиал Астана", parent_id=root.id)
    session.add(child)
    await session.flush()

    found = await session.execute(select(AgentGroup).where(AgentGroup.parent_id == root.id))
    assert found.scalar_one().name == "Филиал Астана"
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_agent_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.db.models.agent'`

- [ ] **Шаг 3: Создать `server/barysguard/db/models/agent.py`**

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class AgentStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    OFFLINE = "offline"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"


class AgentGroup(Base):
    __tablename__ = "agent_groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Устойчивый идентификатор железа: на Windows — UUID из SMBIOS,
    # на Linux — /etc/machine-id. Позволяет узнать хост при повторной регистрации.
    machine_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    hostname: Mapped[str] = mapped_column(String(255))
    os: Mapped[str] = mapped_column(String(32))
    os_version: Mapped[str] = mapped_column(String(128))
    arch: Mapped[str] = mapped_column(String(32))
    agent_version: Mapped[str] = mapped_column(String(32))

    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", native_enum=False, length=32),
        default=AgentStatus.PENDING,
        index=True,
    )

    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # Дрейф часов агента относительно сервера. Отрицательное значение —
    # часы агента отстают. Существенное расхождение само по себе является
    # поводом для события безопасности.
    clock_skew_ms: Mapped[int] = mapped_column(BigInteger, default=0)

    config_version: Mapped[int] = mapped_column(Integer, default=0)
    last_ip: Mapped[str | None] = mapped_column(INET)
    tags: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
```

- [ ] **Шаг 4: Зарегистрировать модели для Alembic**

`server/barysguard/db/models/__init__.py`:

```python
"""Импорт всех моделей. Alembic полагается на этот модуль для автогенерации."""

from barysguard.db.models.agent import Agent, AgentGroup, AgentStatus

__all__ = ["Agent", "AgentGroup", "AgentStatus"]
```

- [ ] **Шаг 5: Сгенерировать миграцию**

```bash
cd server
BG_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard" \
  .venv/bin/alembic revision --autogenerate -m "agents and groups"
```

Открыть созданный файл и убедиться, что созданы таблицы `agent_groups` и `agents`, индексы на `machine_id`, `group_id`, `status`, `last_heartbeat_at`. Если автогенерация пуста — не импортированы модели в `models/__init__.py`.

- [ ] **Шаг 6: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_agent_models.py -v`
Expected: 3 passed

- [ ] **Шаг 7: Зафиксировать**

```bash
git add server/barysguard/db/models server/alembic/versions server/tests
git commit -m "feat: agent and agent group models"
```

---

## Задача 4: Токены регистрации

**Files:**
- Create: `server/barysguard/db/models/enrollment.py`
- Create: `server/barysguard/core/errors.py`
- Create: `server/barysguard/services/enrollment.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Create: `server/alembic/versions/<timestamp>_enrollment_tokens.py`
- Test: `server/tests/test_enrollment_service.py`

**Interfaces:**
- Consumes: `Base`, `AgentGroup`
- Produces:
  - `EnrollmentToken(id, token_sha256, created_by, group_id, expires_at, max_uses, used_count, revoked_at, created_at)`
  - `EnrollmentError` — базовое исключение; подклассы `TokenNotFound`, `TokenExpired`, `TokenExhausted`, `TokenRevoked`
  - `generate_token() -> str` — строка вида `BG-ENROLL-<32 символа base32>`
  - `hash_token(raw: str) -> str` — hex-строка SHA-256 длиной 64
  - `async create_enrollment_token(session, *, created_by: uuid.UUID | None, group_id: uuid.UUID | None, ttl_hours: int, max_uses: int) -> tuple[str, EnrollmentToken]` — возвращает **открытый** токен и запись; открытый токен нигде не сохраняется
  - `async consume_enrollment_token(session, raw: str) -> EnrollmentToken` — атомарно увеличивает `used_count`, при нарушении условий бросает подкласс `EnrollmentError`

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_enrollment_service.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from barysguard.core.errors import (
    TokenExhausted,
    TokenExpired,
    TokenNotFound,
    TokenRevoked,
)
from barysguard.services.enrollment import (
    consume_enrollment_token,
    create_enrollment_token,
    generate_token,
    hash_token,
)


def test_generated_token_has_expected_shape():
    token = generate_token()

    assert token.startswith("BG-ENROLL-")
    assert len(token) == len("BG-ENROLL-") + 32
    assert generate_token() != generate_token()


def test_hash_is_sha256_hex():
    digest = hash_token("BG-ENROLL-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    assert len(digest) == 64
    assert int(digest, 16) >= 0


@pytest.mark.asyncio
async def test_plaintext_token_is_never_stored(session):
    raw, record = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )

    assert record.token_sha256 == hash_token(raw)
    assert raw not in record.token_sha256


@pytest.mark.asyncio
async def test_valid_token_is_consumed(session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=2
    )

    consumed = await consume_enrollment_token(session, raw)

    assert consumed.used_count == 1


@pytest.mark.asyncio
async def test_unknown_token_is_rejected(session):
    with pytest.raises(TokenNotFound):
        await consume_enrollment_token(session, "BG-ENROLL-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ")


@pytest.mark.asyncio
async def test_expired_token_is_rejected(session):
    raw, record = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    with pytest.raises(TokenExpired):
        await consume_enrollment_token(session, raw)


@pytest.mark.asyncio
async def test_exhausted_token_is_rejected(session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await consume_enrollment_token(session, raw)

    with pytest.raises(TokenExhausted):
        await consume_enrollment_token(session, raw)


@pytest.mark.asyncio
async def test_revoked_token_is_rejected(session):
    raw, record = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=5
    )
    record.revoked_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(TokenRevoked):
        await consume_enrollment_token(session, raw)
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_enrollment_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.core.errors'`

- [ ] **Шаг 3: Создать `server/barysguard/core/errors.py`**

```python
class BarysGuardError(Exception):
    """Базовое исключение домена."""


class EnrollmentError(BarysGuardError):
    """Регистрация агента невозможна."""


class TokenNotFound(EnrollmentError):
    pass


class TokenExpired(EnrollmentError):
    pass


class TokenExhausted(EnrollmentError):
    pass


class TokenRevoked(EnrollmentError):
    pass


class PkiError(BarysGuardError):
    """Ошибка удостоверяющего центра."""


class InvalidCsr(PkiError):
    pass
```

- [ ] **Шаг 4: Создать `server/barysguard/db/models/enrollment.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Хранится ТОЛЬКО хеш, как пароль. Дамп базы не должен давать
    # возможности зарегистрировать агента.
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column()
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL")
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Шаг 5: Создать `server/barysguard/services/enrollment.py`**

```python
import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.errors import (
    TokenExhausted,
    TokenExpired,
    TokenNotFound,
    TokenRevoked,
)
from barysguard.db.models.enrollment import EnrollmentToken

TOKEN_PREFIX = "BG-ENROLL-"
TOKEN_BODY_LENGTH = 32


def generate_token() -> str:
    """Открытый токен регистрации. Base32 без набивки — безопасен для командной строки и MSI."""
    raw = secrets.token_bytes(20)
    body = base64.b32encode(raw).decode("ascii").rstrip("=")
    return TOKEN_PREFIX + body[:TOKEN_BODY_LENGTH]


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_enrollment_token(
    session: AsyncSession,
    *,
    created_by: uuid.UUID | None,
    group_id: uuid.UUID | None,
    ttl_hours: int,
    max_uses: int,
) -> tuple[str, EnrollmentToken]:
    """Создаёт токен. Открытая форма возвращается один раз и нигде не сохраняется."""
    raw = generate_token()
    record = EnrollmentToken(
        token_sha256=hash_token(raw),
        created_by=created_by,
        group_id=group_id,
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
        max_uses=max_uses,
        used_count=0,
    )
    session.add(record)
    await session.flush()
    return raw, record


async def consume_enrollment_token(session: AsyncSession, raw: str) -> EnrollmentToken:
    """Расходует одно использование токена.

    Строка блокируется через SELECT ... FOR UPDATE: без этого два агента,
    подключившиеся одновременно с одним токеном на одно использование,
    оба пройдут проверку и оба зарегистрируются.
    """
    statement = (
        select(EnrollmentToken)
        .where(EnrollmentToken.token_sha256 == hash_token(raw))
        .with_for_update()
    )
    record = (await session.execute(statement)).scalar_one_or_none()

    if record is None:
        raise TokenNotFound("enrollment token not found")
    if record.revoked_at is not None:
        raise TokenRevoked("enrollment token revoked")
    if record.expires_at <= datetime.now(UTC):
        raise TokenExpired("enrollment token expired")
    if record.used_count >= record.max_uses:
        raise TokenExhausted("enrollment token exhausted")

    record.used_count += 1
    await session.flush()
    return record
```

- [ ] **Шаг 6: Зарегистрировать модель и сгенерировать миграцию**

Добавить в `server/barysguard/db/models/__init__.py`:

```python
from barysguard.db.models.enrollment import EnrollmentToken

__all__ = ["Agent", "AgentGroup", "AgentStatus", "EnrollmentToken"]
```

```bash
cd server && .venv/bin/alembic revision --autogenerate -m "enrollment tokens"
```

- [ ] **Шаг 7: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_enrollment_service.py -v`
Expected: 8 passed

- [ ] **Шаг 8: Зафиксировать**

```bash
git add server/barysguard server/alembic/versions server/tests
git commit -m "feat: enrollment tokens with hashed storage and atomic consumption"
```

---

## Задача 5: Удостоверяющий центр

**Files:**
- Create: `server/barysguard/pki/ca.py`
- Test: `server/tests/test_ca.py`

**Interfaces:**
- Consumes: `InvalidCsr` из задачи 4
- Produces:
  - `CertificateAuthority` — объект с полями `certificate: x509.Certificate`, `private_key`
  - `CertificateAuthority.certificate_pem property -> bytes`
  - `CertificateAuthority.sign_csr(csr_pem: bytes, subject_cn: str, valid_days: int) -> tuple[bytes, int]` — возвращает PEM сертификата и его серийный номер
  - `ensure_ca(ca_dir: Path, passphrase: str, common_name: str, valid_days: int) -> CertificateAuthority` — создаёт при отсутствии, иначе загружает

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_ca.py`:

```python
import stat

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from barysguard.core.errors import InvalidCsr
from barysguard.pki.ca import ensure_ca

PASSPHRASE = "test-passphrase"


def _make_csr(common_name: str = "ignored-by-server") -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


def test_ca_is_created_on_first_call(tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    assert (tmp_path / "ca.crt").exists()
    assert (tmp_path / "ca.key").exists()
    assert ca.certificate.subject.rfc4514_string() == "CN=BarysGuard Test CA"


def test_ca_key_file_is_not_world_readable(tmp_path):
    ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    mode = (tmp_path / "ca.key").stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


def test_existing_ca_is_loaded_not_regenerated(tmp_path):
    first = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)
    second = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    assert first.certificate.serial_number == second.certificate.serial_number


def test_ca_key_is_encrypted_with_passphrase(tmp_path):
    ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)
    raw = (tmp_path / "ca.key").read_bytes()

    with pytest.raises(TypeError):
        serialization.load_pem_private_key(raw, password=None)


def test_signed_certificate_uses_server_supplied_subject(tmp_path):
    """Субъект CSR игнорируется: агент на момент запроса не знает своего agent_id."""
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)
    agent_id = "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f"

    pem, serial = ca.sign_csr(_make_csr("attacker-chosen-name"), agent_id, 90)
    certificate = x509.load_pem_x509_certificate(pem)

    assert certificate.subject.rfc4514_string() == f"CN={agent_id}"
    assert certificate.issuer == ca.certificate.subject
    assert certificate.serial_number == serial


def test_signed_certificate_is_client_auth_only(tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    pem, _ = ca.sign_csr(_make_csr(), "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f", 90)
    certificate = x509.load_pem_x509_certificate(pem)

    basic = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value

    assert basic.ca is False
    assert x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in eku
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH not in eku


def test_csr_with_broken_signature_is_rejected(tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)
    corrupted = _make_csr().replace(b"MII", b"MIA", 1)

    with pytest.raises(InvalidCsr):
        ca.sign_csr(corrupted, "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f", 90)
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_ca.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.pki.ca'`

- [ ] **Шаг 3: Создать `server/barysguard/pki/ca.py`**

```python
import datetime as dt
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from barysguard.core.errors import InvalidCsr

CA_CERT_FILENAME = "ca.crt"
CA_KEY_FILENAME = "ca.key"


@dataclass(frozen=True)
class CertificateAuthority:
    certificate: x509.Certificate
    private_key: ec.EllipticCurvePrivateKey

    @property
    def certificate_pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    def sign_csr(self, csr_pem: bytes, subject_cn: str, valid_days: int) -> tuple[bytes, int]:
        """Подписывает открытый ключ из CSR.

        Субъект самого CSR не используется: агент на момент формирования
        запроса ещё не знает своего agent_id, а доверять полю, которое
        заполняет клиент, нельзя в принципе. Субъект формирует сервер.
        """
        try:
            csr = x509.load_pem_x509_csr(csr_pem)
        except ValueError as exc:
            raise InvalidCsr(f"cannot parse CSR: {exc}") from exc

        if not csr.is_signature_valid:
            raise InvalidCsr("CSR signature is invalid")

        now = dt.datetime.now(dt.UTC)
        serial = x509.random_serial_number()

        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)]))
            .issuer_name(self.certificate.subject)
            .public_key(csr.public_key())
            .serial_number(serial)
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=valid_days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    self.certificate.public_key()
                ),
                critical=False,
            )
            .sign(self.private_key, hashes.SHA256())
        )

        return certificate.public_bytes(serialization.Encoding.PEM), serial


def _create_ca(
    ca_dir: Path, passphrase: str, common_name: str, valid_days: int
) -> CertificateAuthority:
    key = ec.generate_private_key(ec.SECP384R1())
    now = dt.datetime.now(dt.UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_dir.chmod(stat.S_IRWXU)  # 0700

    key_path = ca_dir / CA_KEY_FILENAME
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
        )
    )
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600

    (ca_dir / CA_CERT_FILENAME).write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )

    return CertificateAuthority(certificate=certificate, private_key=key)


def ensure_ca(
    ca_dir: Path, passphrase: str, common_name: str, valid_days: int
) -> CertificateAuthority:
    """Загружает удостоверяющий центр, создавая его при первом обращении."""
    if not passphrase:
        raise ValueError("BG_CA_PASSPHRASE must be set")

    cert_path = ca_dir / CA_CERT_FILENAME
    key_path = ca_dir / CA_KEY_FILENAME

    if not (cert_path.exists() and key_path.exists()):
        return _create_ca(ca_dir, passphrase, common_name, valid_days)

    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = serialization.load_pem_private_key(
        key_path.read_bytes(), password=passphrase.encode("utf-8")
    )
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return CertificateAuthority(certificate=certificate, private_key=key)
```

- [ ] **Шаг 4: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_ca.py -v`
Expected: 7 passed

Проверка прав файла (`test_ca_key_file_is_not_world_readable`) осмысленна только на POSIX. На Windows разработчику она пройдёт формально; окончательная проверка выполняется в CI на Linux.

- [ ] **Шаг 5: Зафиксировать**

```bash
git add server/barysguard/pki server/tests/test_ca.py
git commit -m "feat: internal certificate authority with encrypted key at rest"
```

---

## Задача 6: Модель и служба сертификатов агентов

**Files:**
- Create: `server/barysguard/db/models/certificate.py`
- Create: `server/barysguard/pki/service.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Create: `server/alembic/versions/<timestamp>_agent_certificates.py`
- Test: `server/tests/test_certificate_service.py`

**Interfaces:**
- Consumes: `CertificateAuthority`, `Agent`
- Produces:
  - `AgentCertificate(id, agent_id, serial, fingerprint_sha256, not_before, not_after, revoked_at, revocation_reason, superseded_by)` — `serial` хранится строкой шестнадцатеричных цифр в нижнем регистре без ведущих нулей
  - `async issue_certificate(session, ca, agent, csr_pem, valid_days) -> tuple[bytes, AgentCertificate]`
  - `async find_active_certificate(session, serial_hex: str) -> AgentCertificate | None` — возвращает только неотозванный и непросроченный
  - `async revoke_certificate(session, serial_hex: str, reason: str) -> None`
  - `serial_to_hex(serial: int) -> str`

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_certificate_service.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from barysguard.db.models.agent import Agent
from barysguard.pki.ca import ensure_ca
from barysguard.pki.service import (
    find_active_certificate,
    issue_certificate,
    revoke_certificate,
    serial_to_hex,
)

PASSPHRASE = "test-passphrase"


def _make_csr() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "x")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


async def _make_agent(session) -> Agent:
    agent = Agent(
        machine_id=f"machine-{datetime.now(UTC).timestamp()}",
        hostname="TEST-HOST",
        os="linux",
        os_version="6.8.0",
        arch="amd64",
        agent_version="0.1.0",
    )
    session.add(agent)
    await session.flush()
    return agent


def test_serial_is_lowercase_hex_without_leading_zeros():
    assert serial_to_hex(255) == "ff"
    assert serial_to_hex(1) == "1"


@pytest.mark.asyncio
async def test_issued_certificate_is_recorded_and_findable(session, tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "Test CA", 3650)
    agent = await _make_agent(session)

    pem, record = await issue_certificate(session, ca, agent, _make_csr(), 90)
    certificate = x509.load_pem_x509_certificate(pem)

    assert record.agent_id == agent.id
    assert record.serial == serial_to_hex(certificate.serial_number)
    assert certificate.subject.rfc4514_string() == f"CN={agent.id}"

    found = await find_active_certificate(session, record.serial)
    assert found is not None
    assert found.id == record.id


@pytest.mark.asyncio
async def test_revoked_certificate_is_not_active(session, tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "Test CA", 3650)
    agent = await _make_agent(session)
    _, record = await issue_certificate(session, ca, agent, _make_csr(), 90)

    await revoke_certificate(session, record.serial, reason="compromised")

    assert await find_active_certificate(session, record.serial) is None


@pytest.mark.asyncio
async def test_expired_certificate_is_not_active(session, tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "Test CA", 3650)
    agent = await _make_agent(session)
    _, record = await issue_certificate(session, ca, agent, _make_csr(), 90)

    record.not_after = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    assert await find_active_certificate(session, record.serial) is None


@pytest.mark.asyncio
async def test_unknown_serial_is_not_active(session):
    assert await find_active_certificate(session, "deadbeef") is None
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_certificate_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.pki.service'`

- [ ] **Шаг 3: Создать `server/barysguard/db/models/certificate.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class AgentCertificate(Base):
    """Сертификат агента.

    Вынесен в отдельную таблицу, а не в поле agents: за время жизни агента
    их накапливается десяток из-за продлений каждые 60 дней, и при
    расследовании требуется знать, каким именно сертификатом подписано
    событие полугодовой давности.
    """

    __tablename__ = "agent_certificates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )

    # Шестнадцатеричные цифры в нижнем регистре без ведущих нулей.
    # Именно в таком виде серийный номер приходит от nginx.
    serial: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), index=True)

    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(255))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_certificates.id", ondelete="SET NULL")
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Шаг 4: Создать `server/barysguard/pki/service.py`**

```python
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import Agent
from barysguard.db.models.certificate import AgentCertificate
from barysguard.pki.ca import CertificateAuthority


def serial_to_hex(serial: int) -> str:
    """Каноническая форма серийного номера: строчные шестнадцатеричные без ведущих нулей."""
    return format(serial, "x")


def normalize_serial(value: str) -> str:
    """Приводит серийный номер к канонической форме.

    nginx отдаёт значение в верхнем регистре и добавляет ведущий ноль,
    когда старший бит установлен. Без нормализации поиск не найдёт запись.
    Отдельная ветка для строки из одних нулей: lstrip вернул бы пустую строку.
    """
    stripped = value.strip().lower().lstrip("0")
    return stripped or "0"


async def issue_certificate(
    session: AsyncSession,
    ca: CertificateAuthority,
    agent: Agent,
    csr_pem: bytes,
    valid_days: int,
) -> tuple[bytes, AgentCertificate]:
    pem, serial = ca.sign_csr(csr_pem, str(agent.id), valid_days)
    certificate = x509.load_pem_x509_certificate(pem)

    record = AgentCertificate(
        agent_id=agent.id,
        serial=serial_to_hex(serial),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
    )
    session.add(record)
    await session.flush()
    return pem, record


async def find_active_certificate(
    session: AsyncSession, serial_hex: str
) -> AgentCertificate | None:
    """Действующий сертификат: не отозван и не просрочен."""
    now = datetime.now(UTC)
    statement = select(AgentCertificate).where(
        AgentCertificate.serial == normalize_serial(serial_hex),
        AgentCertificate.revoked_at.is_(None),
        AgentCertificate.not_before <= now,
        AgentCertificate.not_after > now,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def revoke_certificate(session: AsyncSession, serial_hex: str, reason: str) -> None:
    statement = select(AgentCertificate).where(
        AgentCertificate.serial == normalize_serial(serial_hex)
    )
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None or record.revoked_at is not None:
        return
    record.revoked_at = datetime.now(UTC)
    record.revocation_reason = reason
    await session.flush()
```

`normalize_serial` живёт именно здесь, а не в слое HTTP: она относится к формату серийного номера, а не к транспорту. Задача 8 импортирует её отсюда — обратный порядок дал бы циклический импорт.

- [ ] **Шаг 5: Зарегистрировать модель и сгенерировать миграцию**

Добавить в `server/barysguard/db/models/__init__.py`:

```python
from barysguard.db.models.certificate import AgentCertificate

__all__ = ["Agent", "AgentGroup", "AgentStatus", "AgentCertificate", "EnrollmentToken"]
```

```bash
cd server && .venv/bin/alembic revision --autogenerate -m "agent certificates"
```

- [ ] **Шаг 6: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_certificate_service.py -v`
Expected: 5 passed

- [ ] **Шаг 7: Зафиксировать**

```bash
git add server/barysguard server/alembic/versions server/tests
git commit -m "feat: agent certificate issuance and revocation service"
```

---

## Задача 7: Эндпоинты регистрации

**Files:**
- Create: `server/barysguard/gateway/schemas.py`
- Create: `server/barysguard/gateway/router.py`
- Create: `server/barysguard/pki/provider.py`
- Modify: `server/barysguard/main.py`
- Create: `api/gateway-v1.yaml`
- Test: `server/tests/test_enroll_endpoint.py`

**Interfaces:**
- Consumes: всё из задач 3–6
- Produces:
  - `GET /gateway/v1/ca` → `text/plain` с PEM сертификата CA
  - `POST /gateway/v1/enroll` → `EnrollResponse(agent_id, certificate_pem, ca_pem, config_version, heartbeat_interval_seconds)`
  - `get_ca() -> CertificateAuthority` — зависимость FastAPI, кеширующая CA

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_enroll_endpoint.py`:

```python
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy import select

from barysguard.db.models.agent import Agent
from barysguard.services.enrollment import create_enrollment_token


def make_csr() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


HOST_FACTS = {
    "machine_id": "4C4C4544-0043-5A10-8046-B7C04F335931",
    "hostname": "ACC-PC-01",
    "os": "windows",
    "os_version": "10.0.26100",
    "arch": "amd64",
    "agent_version": "0.1.0",
}


@pytest.mark.asyncio
async def test_ca_endpoint_returns_pem(app_client: AsyncClient):
    response = await app_client.get("/gateway/v1/ca")

    assert response.status_code == 200
    assert response.text.startswith("-----BEGIN CERTIFICATE-----")


@pytest.mark.asyncio
async def test_enroll_issues_certificate_and_creates_agent(app_client, session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={"token": raw, "csr_pem": make_csr(), "host": HOST_FACTS},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["heartbeat_interval_seconds"] == 30

    certificate = x509.load_pem_x509_certificate(body["certificate_pem"].encode())
    assert certificate.subject.rfc4514_string() == f"CN={body['agent_id']}"

    found = await session.execute(
        select(Agent).where(Agent.machine_id == HOST_FACTS["machine_id"])
    )
    assert found.scalar_one().hostname == "ACC-PC-01"


@pytest.mark.asyncio
async def test_enroll_with_unknown_token_is_forbidden(app_client):
    response = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": "BG-ENROLL-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
            "csr_pem": make_csr(),
            "host": HOST_FACTS,
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_enroll_with_exhausted_token_is_forbidden(app_client, session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()
    payload = {"token": raw, "csr_pem": make_csr(), "host": HOST_FACTS}

    assert (await app_client.post("/gateway/v1/enroll", json=payload)).status_code == 201
    second = await app_client.post("/gateway/v1/enroll", json=payload)

    assert second.status_code == 403


@pytest.mark.asyncio
async def test_reenrollment_reuses_existing_agent(app_client, session):
    for _ in range(2):
        raw, _ = await create_enrollment_token(
            session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
        )
        await session.commit()
        response = await app_client.post(
            "/gateway/v1/enroll",
            json={"token": raw, "csr_pem": make_csr(), "host": HOST_FACTS},
        )
        assert response.status_code == 201
        agent_id = response.json()["agent_id"]

    found = await session.execute(
        select(Agent).where(Agent.machine_id == HOST_FACTS["machine_id"])
    )
    agents = found.scalars().all()
    assert len(agents) == 1
    assert str(agents[0].id) == agent_id


@pytest.mark.asyncio
async def test_malformed_csr_is_rejected(app_client, session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={"token": raw, "csr_pem": "not a csr", "host": HOST_FACTS},
    )

    assert response.status_code == 400
```

- [ ] **Шаг 2: Добавить фикстуру `app_client` в `server/tests/conftest.py`**

```python
@pytest_asyncio.fixture
async def app_client(migrated_database_url, tmp_path, monkeypatch):
    """Приложение, подключённое к тестовой базе, со своим CA во временном каталоге."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from barysguard.core.config import get_settings
    from barysguard.db.session import get_session, reset_session_state
    from barysguard.main import create_app
    from barysguard.pki.provider import get_ca

    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("BG_CA_DIR", str(tmp_path / "pki"))
    monkeypatch.setenv("BG_CA_PASSPHRASE", "test-passphrase")

    # Обе функции кешируются через lru_cache. Без сброса тест получит
    # настройки и удостоверяющий центр от предыдущего теста, а CA из
    # удалённого tmp_path перестанет соответствовать записям в базе.
    get_settings.cache_clear()
    get_ca.cache_clear()
    reset_session_state()

    engine = create_engine_from_url(migrated_database_url)
    maker = session_factory(engine)

    # База общая на всю сессию тестов, поэтому состояние сбрасывается явно.
    # Полагаться на уникальность machine_id в каждом тесте — хрупко.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE agent_certificates, enrollment_tokens, agents, "
                "agent_groups, audit_log, users RESTART IDENTITY CASCADE"
            )
        )

    async def override_get_session():
        async with maker() as db_session:
            try:
                yield db_session
                await db_session.commit()
            except Exception:
                await db_session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await engine.dispose()
    get_settings.cache_clear()
    get_ca.cache_clear()
    reset_session_state()
```

Фикстура `session` откатывает свою транзакцию, а `app_client` работает через собственные сессии с фиксацией. Поэтому в тестах, где данные готовятся через `session`, а читаются через HTTP, подготовку нужно фиксировать явно вызовом `await session.commit()`.

Фикстура `app_client` создаётся в задаче 7, но в её `TRUNCATE` перечислены таблицы `audit_log` и `users`, которые появятся только в задачах 10 и 11. До этого момента их имена в списке вызовут ошибку. При выполнении задачи 7 перечислить только существующие таблицы (`agent_certificates`, `enrollment_tokens`, `agents`, `agent_groups`) и дополнить список в задачах 10 и 11.

- [ ] **Шаг 3: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_enroll_endpoint.py -v`
Expected: FAIL — 404, маршрут `/gateway/v1/ca` не зарегистрирован

- [ ] **Шаг 4: Создать `server/barysguard/pki/provider.py`**

```python
from functools import lru_cache

from barysguard.core.config import get_settings
from barysguard.pki.ca import CertificateAuthority, ensure_ca


@lru_cache
def get_ca() -> CertificateAuthority:
    """Удостоверяющий центр создаётся при первом обращении и далее кешируется.

    Кеш сбрасывается вызовом get_ca.cache_clear() — используется в тестах.
    """
    settings = get_settings()
    return ensure_ca(
        settings.ca_dir,
        settings.ca_passphrase,
        settings.ca_common_name,
        settings.ca_valid_days,
    )
```

- [ ] **Шаг 5: Создать `server/barysguard/gateway/schemas.py`**

```python
import uuid

from pydantic import BaseModel, Field


class HostFacts(BaseModel):
    machine_id: str = Field(max_length=255)
    hostname: str = Field(max_length=255)
    os: str = Field(max_length=32)
    os_version: str = Field(max_length=128)
    arch: str = Field(max_length=32)
    agent_version: str = Field(max_length=32)


class EnrollRequest(BaseModel):
    token: str = Field(max_length=64)
    csr_pem: str = Field(max_length=8192)
    host: HostFacts


class EnrollResponse(BaseModel):
    agent_id: uuid.UUID
    certificate_pem: str
    ca_pem: str
    config_version: int
    heartbeat_interval_seconds: int
```

- [ ] **Шаг 6: Создать `server/barysguard/gateway/router.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings, get_settings
from barysguard.core.errors import EnrollmentError, InvalidCsr
from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.session import get_session
from barysguard.gateway.schemas import EnrollRequest, EnrollResponse
from barysguard.pki.ca import CertificateAuthority
from barysguard.pki.provider import get_ca
from barysguard.pki.service import issue_certificate
from barysguard.services.enrollment import consume_enrollment_token

router = APIRouter(prefix="/gateway/v1", tags=["gateway"])


@router.get("/ca", response_class=Response)
async def get_ca_certificate(ca: CertificateAuthority = Depends(get_ca)) -> Response:
    """Сертификат удостоверяющего центра. Без аутентификации — он публичен по определению."""
    return Response(content=ca.certificate_pem, media_type="application/x-pem-file")


@router.post("/enroll", response_model=EnrollResponse, status_code=status.HTTP_201_CREATED)
async def enroll(
    payload: EnrollRequest,
    session: AsyncSession = Depends(get_session),
    ca: CertificateAuthority = Depends(get_ca),
    settings: Settings = Depends(get_settings),
) -> EnrollResponse:
    try:
        token = await consume_enrollment_token(session, payload.token)
    except EnrollmentError as exc:
        # Все причины отказа отдаются одинаково: различие в ответах
        # позволило бы перебором отличать существующий токен от несуществующего.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "enrollment refused") from exc

    existing = await session.execute(
        select(Agent).where(Agent.machine_id == payload.host.machine_id).with_for_update()
    )
    agent = existing.scalar_one_or_none()

    if agent is None:
        agent = Agent(
            machine_id=payload.host.machine_id,
            hostname=payload.host.hostname,
            os=payload.host.os,
            os_version=payload.host.os_version,
            arch=payload.host.arch,
            agent_version=payload.host.agent_version,
            group_id=token.group_id,
            status=AgentStatus.ACTIVE,
        )
        session.add(agent)
    else:
        # Повторная регистрация того же железа обновляет факты, но не плодит агентов.
        agent.hostname = payload.host.hostname
        agent.os_version = payload.host.os_version
        agent.agent_version = payload.host.agent_version
        agent.status = AgentStatus.ACTIVE

    await session.flush()

    try:
        certificate_pem, _ = await issue_certificate(
            session, ca, agent, payload.csr_pem.encode("utf-8"), settings.agent_cert_days
        )
    except InvalidCsr as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid CSR") from exc

    return EnrollResponse(
        agent_id=agent.id,
        certificate_pem=certificate_pem.decode("ascii"),
        ca_pem=ca.certificate_pem.decode("ascii"),
        config_version=agent.config_version,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
    )
```

- [ ] **Шаг 7: Подключить маршрутизатор в `server/barysguard/main.py`**

Добавить в `create_app()` перед `return app`:

```python
    from barysguard.gateway.router import router as gateway_router

    app.include_router(gateway_router)
```

- [ ] **Шаг 8: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_enroll_endpoint.py -v`
Expected: 6 passed

- [ ] **Шаг 9: Выгрузить контракт OpenAPI в `api/gateway-v1.yaml`**

```bash
cd server
BG_CA_DIR=/tmp/bg-pki BG_CA_PASSPHRASE=dev .venv/bin/python - <<'PY'
import yaml
from barysguard.main import create_app
spec = create_app().openapi()
with open("../api/gateway-v1.yaml", "w", encoding="utf-8") as fh:
    yaml.safe_dump(spec, fh, allow_unicode=True, sort_keys=False)
PY
```

Этот файл — общий контракт с агентом из плана 1B: из него генерируется клиент на Go. Он перегенерируется при каждом изменении схем.

- [ ] **Шаг 10: Зафиксировать**

```bash
git add server/barysguard server/tests api/gateway-v1.yaml
git commit -m "feat: agent enrollment endpoints with CSR signing"
```

---

## Задача 8: Аутентификация агента по mTLS

**Files:**
- Create: `server/barysguard/gateway/deps.py`
- Create: `deploy/nginx/barysguard.conf`
- Modify: `server/barysguard/gateway/router.py`
- Test: `server/tests/test_mtls_identity.py`

**Interfaces:**
- Consumes: `find_active_certificate`, `Agent`
- Produces:
  - `async current_agent(request, session) -> Agent` — зависимость FastAPI, возвращает агента по клиентскому сертификату либо бросает `HTTPException(403)`
  - Заголовки контракта с nginx: `X-Client-Verify` (`SUCCESS` или иное), `X-Client-Serial` (шестнадцатеричный серийный номер)
  - `GET /gateway/v1/whoami` → `{"agent_id": ..., "hostname": ...}` — эндпоинт для проверки аутентификации

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_mtls_identity.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _csr() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


async def _enroll(app_client, session, machine_id: str) -> tuple[str, str]:
    """Регистрирует агента и возвращает (agent_id, serial сертификата в hex)."""
    from barysguard.services.enrollment import create_enrollment_token

    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": raw,
            "csr_pem": _csr(),
            "host": {
                "machine_id": machine_id,
                "hostname": "H",
                "os": "linux",
                "os_version": "6.8.0",
                "arch": "amd64",
                "agent_version": "0.1.0",
            },
        },
    )
    body = response.json()
    certificate = x509.load_pem_x509_certificate(body["certificate_pem"].encode())
    return body["agent_id"], format(certificate.serial_number, "x")


@pytest.mark.asyncio
async def test_valid_client_certificate_identifies_agent(app_client, session):
    agent_id, serial = await _enroll(app_client, session, "mtls-happy-path")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 200
    assert response.json()["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_missing_verify_header_is_forbidden(app_client, session):
    _, serial = await _enroll(app_client, session, "mtls-no-verify")

    response = await app_client.get(
        "/gateway/v1/whoami", headers={"X-Client-Serial": serial}
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_failed_verify_is_forbidden(app_client, session):
    """Ключевой тест: подделка заголовков в обход nginx не должна работать."""
    _, serial = await _enroll(app_client, session, "mtls-failed-verify")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "FAILED:certificate has expired", "X-Client-Serial": serial},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unknown_serial_is_forbidden(app_client):
    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": "deadbeefcafe"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_revoked_certificate_is_forbidden_immediately(app_client, session):
    from barysguard.pki.service import revoke_certificate

    _, serial = await _enroll(app_client, session, "mtls-revoked")
    await revoke_certificate(session, serial, reason="test")
    await session.commit()

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_expired_certificate_is_forbidden(app_client, session):
    from sqlalchemy import select

    from barysguard.db.models.certificate import AgentCertificate

    _, serial = await _enroll(app_client, session, "mtls-expired")
    found = await session.execute(
        select(AgentCertificate).where(AgentCertificate.serial == serial)
    )
    found.scalar_one().not_after = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_serial_is_normalised_from_nginx_format(app_client, session):
    """nginx отдаёт серийный номер в верхнем регистре, возможно с ведущими нулями."""
    _, serial = await _enroll(app_client, session, "mtls-serial-case")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial.upper()},
    )

    assert response.status_code == 200
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_mtls_identity.py -v`
Expected: FAIL — 404, маршрут `/gateway/v1/whoami` отсутствует

- [ ] **Шаг 3: Создать `server/barysguard/gateway/deps.py`**

```python
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.session import get_session
from barysguard.pki.service import find_active_certificate, normalize_serial

VERIFY_HEADER = "X-Client-Verify"
SERIAL_HEADER = "X-Client-Serial"
VERIFY_SUCCESS = "SUCCESS"


async def current_agent(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Agent:
    """Личность агента из клиентского сертификата.

    Заголовки проставляет nginx после успешной проверки сертификата.
    Конфигурация nginx ОБЯЗАНА вырезать эти заголовки, приходящие снаружи,
    иначе любой клиент объявит себя любым агентом (см. deploy/nginx/barysguard.conf).
    """
    if request.headers.get(VERIFY_HEADER) != VERIFY_SUCCESS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    raw_serial = request.headers.get(SERIAL_HEADER)
    if not raw_serial:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    certificate = await find_active_certificate(session, normalize_serial(raw_serial))
    if certificate is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    agent = (
        await session.execute(select(Agent).where(Agent.id == certificate.agent_id))
    ).scalar_one_or_none()

    if agent is None or agent.status == AgentStatus.REVOKED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    return agent
```

- [ ] **Шаг 4: Добавить эндпоинт `whoami` в `server/barysguard/gateway/router.py`**

```python
from barysguard.gateway.deps import current_agent


@router.get("/whoami")
async def whoami(agent: Agent = Depends(current_agent)) -> dict[str, str]:
    """Проверка аутентификации по клиентскому сертификату."""
    return {"agent_id": str(agent.id), "hostname": agent.hostname}
```

- [ ] **Шаг 5: Создать `deploy/nginx/barysguard.conf`**

```nginx
# Конфигурация nginx для BarysGuard DLP.
#
# КРИТИЧНО ДЛЯ БЕЗОПАСНОСТИ: директивы proxy_set_header ниже перезаписывают
# заголовки X-Client-* значениями, вычисленными самим nginx. Это единственное,
# что мешает клиенту прислать свой X-Client-Verify: SUCCESS и объявить себя
# любым агентом. Не удалять и не переносить в location, где они не заданы.

upstream barysguard_server {
    server 127.0.0.1:8000;
    keepalive 64;
}

# Ограничение частоты: агент шлёт heartbeat раз в 30 секунд,
# события — пакетами. 20 запросов в секунду на агента с запасом достаточно.
limit_req_zone $ssl_client_serial zone=agents:10m rate=20r/s;

server {
    listen 8443 ssl;
    http2 on;
    server_name _;

    ssl_certificate     /etc/barysguard/tls/server.crt;
    ssl_certificate_key /etc/barysguard/tls/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    # Клиентские сертификаты проверяются нашим внутренним CA.
    ssl_client_certificate /var/lib/barysguard/pki/ca.crt;

    # optional, а не on: эндпоинты /enroll и /ca обязаны быть доступны
    # агенту, у которого сертификата ещё нет. Проверку того, что сертификат
    # предъявлен и валиден, выполняет приложение по заголовку X-Client-Verify.
    ssl_verify_client optional;
    ssl_verify_depth  2;

    client_max_body_size 64m;

    location / {
        limit_req zone=agents burst=40 nodelay;

        proxy_pass http://barysguard_server;
        proxy_http_version 1.1;

        # Значения вычисляет nginx. Всё, что клиент прислал под этими
        # именами, здесь безусловно затирается.
        proxy_set_header X-Client-Verify  $ssl_client_verify;
        proxy_set_header X-Client-Serial  $ssl_client_serial;
        proxy_set_header X-Client-DN      $ssl_client_s_dn;

        proxy_set_header Host             $host;
        proxy_set_header X-Real-IP        $remote_addr;
        proxy_set_header X-Forwarded-For  $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

- [ ] **Шаг 6: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_mtls_identity.py -v`
Expected: 7 passed

- [ ] **Шаг 7: Зафиксировать**

```bash
git add server/barysguard deploy/nginx server/tests
git commit -m "feat: mTLS agent identity with nginx header contract

Личность агента определяется серийным номером клиентского сертификата.
Конфигурация nginx безусловно перезаписывает заголовки X-Client-*,
приложение отклоняет запрос при X-Client-Verify != SUCCESS."
```

---

## Задача 9: Продление сертификата

**Files:**
- Modify: `server/barysguard/gateway/router.py`
- Modify: `server/barysguard/gateway/schemas.py`
- Modify: `server/barysguard/pki/service.py`
- Test: `server/tests/test_renew_endpoint.py`

**Interfaces:**
- Consumes: `current_agent`, `issue_certificate`
- Produces:
  - `RenewRequest(csr_pem: str)`, `RenewResponse(certificate_pem: str, ca_pem: str, not_after: datetime)`
  - `POST /gateway/v1/renew`
  - `async supersede_certificate(session, old_serial: str, new_id: uuid.UUID) -> None`

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_renew_endpoint.py`:

```python
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from barysguard.db.models.certificate import AgentCertificate


def _csr() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


async def _enroll(app_client, session, machine_id: str) -> tuple[str, str]:
    from barysguard.services.enrollment import create_enrollment_token

    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()
    response = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": raw,
            "csr_pem": _csr(),
            "host": {
                "machine_id": machine_id,
                "hostname": "H",
                "os": "linux",
                "os_version": "6.8.0",
                "arch": "amd64",
                "agent_version": "0.1.0",
            },
        },
    )
    body = response.json()
    certificate = x509.load_pem_x509_certificate(body["certificate_pem"].encode())
    return body["agent_id"], format(certificate.serial_number, "x")


@pytest.mark.asyncio
async def test_renew_issues_new_certificate_for_same_agent(app_client, session):
    agent_id, serial = await _enroll(app_client, session, "renew-happy")

    response = await app_client.post(
        "/gateway/v1/renew",
        json={"csr_pem": _csr()},
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 200
    new_certificate = x509.load_pem_x509_certificate(
        response.json()["certificate_pem"].encode()
    )
    assert new_certificate.subject.rfc4514_string() == f"CN={agent_id}"
    assert format(new_certificate.serial_number, "x") != serial


@pytest.mark.asyncio
async def test_old_certificate_is_marked_superseded_but_still_valid(app_client, session):
    """Старый сертификат не отзывается сразу: агент должен успеть сохранить новый."""
    _, serial = await _enroll(app_client, session, "renew-supersede")

    await app_client.post(
        "/gateway/v1/renew",
        json={"csr_pem": _csr()},
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    found = await session.execute(
        select(AgentCertificate).where(AgentCertificate.serial == serial)
    )
    old = found.scalar_one()
    assert old.superseded_by is not None
    assert old.revoked_at is None


@pytest.mark.asyncio
async def test_renew_without_certificate_is_forbidden(app_client):
    response = await app_client.post("/gateway/v1/renew", json={"csr_pem": _csr()})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_renew_with_revoked_certificate_is_forbidden(app_client, session):
    from barysguard.pki.service import revoke_certificate

    _, serial = await _enroll(app_client, session, "renew-revoked")
    await revoke_certificate(session, serial, reason="test")
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/renew",
        json={"csr_pem": _csr()},
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 403
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_renew_endpoint.py -v`
Expected: FAIL — 404, маршрут `/gateway/v1/renew` отсутствует

- [ ] **Шаг 3: Добавить `supersede_certificate` в `server/barysguard/pki/service.py`**

```python
import uuid


async def supersede_certificate(
    session: AsyncSession, old_serial: str, new_id: uuid.UUID
) -> None:
    """Помечает старый сертификат заменённым, НЕ отзывая его.

    Отзыв в момент продления сломал бы агента, у которого запрос прошёл,
    а ответ не дошёл: он остался бы со старым сертификатом, уже недействительным.
    Старый сертификат доживает свой срок сам.
    """
    statement = select(AgentCertificate).where(
        AgentCertificate.serial == normalize_serial(old_serial)
    )
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None:
        return
    record.superseded_by = new_id
    await session.flush()
```

- [ ] **Шаг 4: Добавить схемы в `server/barysguard/gateway/schemas.py`**

```python
from datetime import datetime


class RenewRequest(BaseModel):
    csr_pem: str = Field(max_length=8192)


class RenewResponse(BaseModel):
    certificate_pem: str
    ca_pem: str
    not_after: datetime
```

- [ ] **Шаг 5: Добавить эндпоинт в `server/barysguard/gateway/router.py`**

```python
from barysguard.gateway.deps import SERIAL_HEADER, current_agent
from barysguard.gateway.schemas import RenewRequest, RenewResponse
from barysguard.pki.service import supersede_certificate


@router.post("/renew", response_model=RenewResponse)
async def renew(
    payload: RenewRequest,
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
    ca: CertificateAuthority = Depends(get_ca),
    settings: Settings = Depends(get_settings),
) -> RenewResponse:
    try:
        certificate_pem, record = await issue_certificate(
            session, ca, agent, payload.csr_pem.encode("utf-8"), settings.agent_cert_days
        )
    except InvalidCsr as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid CSR") from exc

    old_serial = request.headers.get(SERIAL_HEADER, "")
    if old_serial:
        await supersede_certificate(session, old_serial, record.id)

    return RenewResponse(
        certificate_pem=certificate_pem.decode("ascii"),
        ca_pem=ca.certificate_pem.decode("ascii"),
        not_after=record.not_after,
    )
```

Добавить `Request` в импорты из `fastapi`.

- [ ] **Шаг 6: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_renew_endpoint.py -v`
Expected: 4 passed

- [ ] **Шаг 7: Зафиксировать**

```bash
git add server/barysguard server/tests
git commit -m "feat: certificate renewal endpoint"
```

---

## Задача 10: Журнал аудита с цепочкой хешей

**Files:**
- Create: `server/barysguard/db/models/audit.py`
- Create: `server/barysguard/services/audit.py`
- Modify: `server/barysguard/db/models/__init__.py`
- Create: `server/alembic/versions/<timestamp>_audit_log.py`
- Test: `server/tests/test_audit.py`

**Interfaces:**
- Consumes: `Base`
- Produces:
  - `AuditLog(id, seq, at, user_id, action, target_type, target_id, payload, prev_hash, hash)` — `seq` строго возрастающий `BigInteger` из последовательности
  - `async record_audit(session, *, user_id, action, target_type, target_id, payload) -> AuditLog`
  - `compute_entry_hash(seq, at, user_id, action, target_type, target_id, payload, prev_hash) -> str`
  - `async verify_audit_chain(session) -> int | None` — возвращает `seq` первой повреждённой записи либо `None`

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_audit.py`:

```python
import uuid

import pytest

from barysguard.services.audit import record_audit, verify_audit_chain


@pytest.mark.asyncio
async def test_first_entry_has_genesis_previous_hash(session):
    entry = await record_audit(
        session,
        user_id=None,
        action="enrollment_token.create",
        target_type="enrollment_token",
        target_id=None,
        payload={"max_uses": 1},
    )

    assert entry.prev_hash == "0" * 64
    assert len(entry.hash) == 64


@pytest.mark.asyncio
async def test_entries_are_chained(session):
    first = await record_audit(
        session, user_id=None, action="a", target_type="t", target_id=None, payload={}
    )
    second = await record_audit(
        session, user_id=None, action="b", target_type="t", target_id=None, payload={}
    )

    assert second.prev_hash == first.hash
    assert second.seq > first.seq


@pytest.mark.asyncio
async def test_intact_chain_verifies(session):
    for index in range(5):
        await record_audit(
            session,
            user_id=uuid.uuid4(),
            action=f"action.{index}",
            target_type="agent",
            target_id=uuid.uuid4(),
            payload={"index": index},
        )

    assert await verify_audit_chain(session) is None


@pytest.mark.asyncio
async def test_tampering_is_detected(session):
    await record_audit(
        session, user_id=None, action="a", target_type="t", target_id=None, payload={}
    )
    tampered = await record_audit(
        session, user_id=None, action="b", target_type="t", target_id=None, payload={}
    )
    await record_audit(
        session, user_id=None, action="c", target_type="t", target_id=None, payload={}
    )

    # Кто-то отредактировал запись напрямую в базе, минуя приложение.
    tampered.action = "harmless"
    await session.flush()

    assert await verify_audit_chain(session) == tampered.seq
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.services.audit'`

- [ ] **Шаг 3: Создать `server/barysguard/db/models/audit.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class AuditLog(Base):
    """Журнал действий операторов. Только добавление.

    Приложению не выдаются права UPDATE и DELETE на эту таблицу (см. миграцию).
    Каждая запись содержит хеш предыдущей, поэтому незаметная правка истории
    невозможна: она разрывает цепочку. DLP-система наблюдает за сотрудниками,
    и первый вопрос любой проверки — кто контролирует тех, кто читает перехват.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True, index=True)

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
```

- [ ] **Шаг 4: Создать `server/barysguard/services/audit.py`**

```python
import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.audit import AuditLog

GENESIS_HASH = "0" * 64


def compute_entry_hash(
    seq: int,
    at: datetime,
    user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict,
    prev_hash: str,
) -> str:
    """Хеш записи. Порядок и форма полей фиксированы — иначе проверка развалится."""
    material = json.dumps(
        {
            "seq": seq,
            "at": at.isoformat(),
            "user_id": str(user_id) if user_id else None,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id) if target_id else None,
            "payload": payload,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def record_audit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict,
) -> AuditLog:
    previous = (
        await session.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1))
    ).scalar_one_or_none()
    prev_hash = previous.hash if previous else GENESIS_HASH

    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        prev_hash=prev_hash,
        hash="",
    )
    session.add(entry)
    await session.flush()

    # seq выдаётся последовательностью, at — server_default. После flush
    # оба значения существуют в базе, но НЕ в объекте: без refresh
    # entry.seq и entry.at будут None и хеш посчитается по пустышкам.
    await session.refresh(entry)

    entry.hash = compute_entry_hash(
        entry.seq,
        entry.at,
        entry.user_id,
        entry.action,
        entry.target_type,
        entry.target_id,
        entry.payload,
        entry.prev_hash,
    )
    await session.flush()
    return entry


async def verify_audit_chain(session: AsyncSession) -> int | None:
    """Возвращает seq первой повреждённой записи либо None, если цепочка цела."""
    entries = (
        (await session.execute(select(AuditLog).order_by(AuditLog.seq.asc()))).scalars().all()
    )

    expected_prev = GENESIS_HASH
    for entry in entries:
        if entry.prev_hash != expected_prev:
            return entry.seq
        recomputed = compute_entry_hash(
            entry.seq,
            entry.at,
            entry.user_id,
            entry.action,
            entry.target_type,
            entry.target_id,
            entry.payload,
            entry.prev_hash,
        )
        if recomputed != entry.hash:
            return entry.seq
        expected_prev = entry.hash

    return None
```

- [ ] **Шаг 5: Зарегистрировать модель и сгенерировать миграцию**

Добавить в `server/barysguard/db/models/__init__.py`:

```python
from barysguard.db.models.audit import AuditLog
```

и в `__all__`.

```bash
cd server && .venv/bin/alembic revision --autogenerate -m "audit log"
```

- [ ] **Шаг 6: Добавить в миграцию отзыв прав на изменение**

В сгенерированный файл миграции, в конец `upgrade()`:

```python
    # Журнал аудита доступен приложению только на добавление.
    # Роль barysguard_app создаётся при развёртывании; при её отсутствии
    # (например, в тестах под суперпользователем) команда пропускается.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'barysguard_app') THEN
                REVOKE UPDATE, DELETE ON audit_log FROM barysguard_app;
            END IF;
        END $$;
        """
    )
```

В `downgrade()` перед удалением таблицы:

```python
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'barysguard_app') THEN
                GRANT UPDATE, DELETE ON audit_log TO barysguard_app;
            END IF;
        END $$;
        """
    )
```

- [ ] **Шаг 7: Запустить тесты**

Run: `cd server && .venv/bin/python -m pytest tests/test_audit.py -v`
Expected: 4 passed

- [ ] **Шаг 8: Зафиксировать**

```bash
git add server/barysguard server/alembic/versions server/tests
git commit -m "feat: append-only audit log with hash chain"
```

---

## Задача 11: Операторы и API выпуска токенов регистрации

**Files:**
- Create: `server/barysguard/db/models/user.py`
- Create: `server/barysguard/services/users.py`
- Create: `server/barysguard/api/deps.py`
- Create: `server/barysguard/api/schemas.py`
- Create: `server/barysguard/api/router.py`
- Create: `server/barysguard/cli.py`
- Modify: `server/barysguard/db/models/__init__.py`, `server/barysguard/main.py`, `server/pyproject.toml`
- Create: `server/alembic/versions/<timestamp>_users.py`
- Test: `server/tests/test_operator_api.py`

**Interfaces:**
- Consumes: `create_enrollment_token`, `record_audit`, `Base`
- Produces:
  - `UserRole` — перечисление: `admin`, `operator`
  - `User(id, username, api_key_sha256, role, scope_group_id, is_active, created_at)`
  - `async current_user(request, session) -> User` — по заголовку `X-Api-Key`
  - `require_role(*roles)` — фабрика зависимостей
  - `POST /api/v1/enrollment-tokens` → `EnrollmentTokenResponse(token, expires_at, max_uses)`
  - `GET /api/v1/agents` → список агентов, ограниченный `scope_group_id`
  - Консольная команда `barysguard-admin create-user --username U --role admin`

- [ ] **Шаг 1: Написать падающий тест**

`server/tests/test_operator_api.py`:

```python
import pytest
from sqlalchemy import select

from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.users import create_user


@pytest.mark.asyncio
async def test_create_user_returns_plaintext_key_once(session):
    raw_key, user = await create_user(session, username="admin", role=UserRole.ADMIN)

    assert len(raw_key) >= 32
    assert user.api_key_sha256 != raw_key
    assert user.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_creating_token_requires_api_key(app_client):
    response = await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 1})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_is_rejected(app_client):
    response = await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 1},
        headers={"X-Api-Key": "wrong-key-value-that-does-not-exist"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_operator_creates_enrollment_token(app_client, session):
    raw_key, _ = await create_user(session, username="officer", role=UserRole.OPERATOR)
    await session.commit()

    response = await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 5, "ttl_hours": 12},
        headers={"X-Api-Key": raw_key},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("BG-ENROLL-")
    assert body["max_uses"] == 5


@pytest.mark.asyncio
async def test_token_creation_is_audited(app_client, session):
    raw_key, user = await create_user(session, username="audited", role=UserRole.OPERATOR)
    await session.commit()

    await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 1},
        headers={"X-Api-Key": raw_key},
    )

    found = await session.execute(
        select(AuditLog).where(AuditLog.action == "enrollment_token.create")
    )
    entries = found.scalars().all()
    assert any(entry.user_id == user.id for entry in entries)


@pytest.mark.asyncio
async def test_deactivated_user_is_rejected(app_client, session):
    raw_key, user = await create_user(session, username="fired", role=UserRole.OPERATOR)
    user.is_active = False
    await session.commit()

    response = await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 1},
        headers={"X-Api-Key": raw_key},
    )

    assert response.status_code == 401
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Run: `cd server && .venv/bin/python -m pytest tests/test_operator_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barysguard.db.models.user'`

- [ ] **Шаг 3: Создать `server/barysguard/db/models/user.py`**

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class User(Base):
    """Оператор системы.

    scope_group_id ограничивает видимость поддеревом групп агентов. В плане 1A
    поле заполняется, но применяется только в списке агентов; полноценное
    разграничение доступа появится в подпроекте 4. Заложено сразу потому,
    что офицер ИБ филиала не имеет права читать перехват другого филиала,
    и это требование чаще юридическое, чем техническое.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    api_key_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False, length=32)
    )
    scope_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Шаг 4: Создать `server/barysguard/services/users.py`**

```python
import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.user import User, UserRole


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    role: UserRole,
    scope_group_id: uuid.UUID | None = None,
) -> tuple[str, User]:
    """Создаёт оператора. Открытый ключ возвращается один раз и не сохраняется."""
    raw_key = secrets.token_urlsafe(32)
    user = User(
        username=username,
        api_key_sha256=hash_api_key(raw_key),
        role=role,
        scope_group_id=scope_group_id,
    )
    session.add(user)
    await session.flush()
    return raw_key, user


async def find_active_user_by_key(session: AsyncSession, raw_key: str) -> User | None:
    statement = select(User).where(
        User.api_key_sha256 == hash_api_key(raw_key),
        User.is_active.is_(True),
    )
    return (await session.execute(statement)).scalar_one_or_none()
```

- [ ] **Шаг 5: Создать `server/barysguard/api/deps.py`**

```python
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.user import User, UserRole
from barysguard.db.session import get_session
from barysguard.services.users import find_active_user_by_key

API_KEY_HEADER = "X-Api-Key"


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    raw_key = request.headers.get(API_KEY_HEADER)
    if not raw_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "api key required")

    user = await find_active_user_by_key(session, raw_key)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")

    return user


def require_role(*roles: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    async def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user

    return dependency
```

- [ ] **Шаг 6: Создать `server/barysguard/api/schemas.py`**

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateEnrollmentTokenRequest(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=10_000)
    ttl_hours: int | None = Field(default=None, ge=1, le=8760)
    group_id: uuid.UUID | None = None


class EnrollmentTokenResponse(BaseModel):
    token: str
    expires_at: datetime
    max_uses: int


class AgentSummary(BaseModel):
    id: uuid.UUID
    hostname: str
    os: str
    agent_version: str
    status: str
    last_heartbeat_at: datetime | None
```

- [ ] **Шаг 7: Создать `server/barysguard/api/router.py`**

```python
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import current_user
from barysguard.api.schemas import (
    AgentSummary,
    CreateEnrollmentTokenRequest,
    EnrollmentTokenResponse,
)
from barysguard.core.config import Settings, get_settings
from barysguard.db.models.agent import Agent
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.enrollment import create_enrollment_token

router = APIRouter(prefix="/api/v1", tags=["api"])


@router.post(
    "/enrollment-tokens",
    response_model=EnrollmentTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    payload: CreateEnrollmentTokenRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> EnrollmentTokenResponse:
    ttl_hours = payload.ttl_hours or settings.enrollment_token_ttl_hours
    raw, record = await create_enrollment_token(
        session,
        created_by=user.id,
        group_id=payload.group_id,
        ttl_hours=ttl_hours,
        max_uses=payload.max_uses,
    )

    await record_audit(
        session,
        user_id=user.id,
        action="enrollment_token.create",
        target_type="enrollment_token",
        target_id=record.id,
        payload={"max_uses": payload.max_uses, "ttl_hours": ttl_hours},
    )

    # Открытая форма токена возвращается единственный раз.
    return EnrollmentTokenResponse(
        token=raw, expires_at=record.expires_at, max_uses=record.max_uses
    )


@router.get("/agents", response_model=list[AgentSummary])
async def list_agents(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentSummary]:
    statement = select(Agent).order_by(Agent.hostname)
    if user.scope_group_id is not None:
        statement = statement.where(Agent.group_id == user.scope_group_id)

    agents = (await session.execute(statement)).scalars().all()
    return [
        AgentSummary(
            id=agent.id,
            hostname=agent.hostname,
            os=agent.os,
            agent_version=agent.agent_version,
            status=agent.status.value,
            last_heartbeat_at=agent.last_heartbeat_at,
        )
        for agent in agents
    ]
```

- [ ] **Шаг 8: Создать `server/barysguard/cli.py`**

```python
import argparse
import asyncio

from barysguard.core.config import get_settings
from barysguard.db.models.user import UserRole
from barysguard.db.session import create_engine_from_url, session_factory
from barysguard.services.users import create_user


async def _create_user(username: str, role: str) -> None:
    engine = create_engine_from_url(get_settings().database_url)
    async with session_factory(engine)() as session:
        raw_key, user = await create_user(
            session, username=username, role=UserRole(role)
        )
        await session.commit()

    print(f"user:    {user.username}")
    print(f"role:    {user.role.value}")
    print(f"api key: {raw_key}")
    print("Ключ показывается один раз. Сохраните его сейчас.")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="barysguard-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="создать оператора")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=[r.value for r in UserRole], default="operator")

    args = parser.parse_args()
    if args.command == "create-user":
        asyncio.run(_create_user(args.username, args.role))
```

Добавить в `server/pyproject.toml`:

```toml
[project.scripts]
barysguard-admin = "barysguard.cli:main"
```

- [ ] **Шаг 9: Подключить маршрутизатор, зарегистрировать модель, сгенерировать миграцию**

В `server/barysguard/main.py`, в `create_app()`:

```python
    from barysguard.api.router import router as api_router

    app.include_router(api_router)
```

В `server/barysguard/db/models/__init__.py` добавить `User`, `UserRole` в импорты и `__all__`.

```bash
cd server && .venv/bin/alembic revision --autogenerate -m "users"
```

- [ ] **Шаг 10: Запустить весь набор тестов**

Run: `cd server && .venv/bin/python -m pytest -v`
Expected: все тесты проходят (около 45)

- [ ] **Шаг 11: Проверить линт и типы**

```bash
cd server
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy barysguard
```

Expected: без ошибок

- [ ] **Шаг 12: Зафиксировать**

```bash
git add server/barysguard server/alembic/versions server/tests server/pyproject.toml
git commit -m "feat: operator authentication and enrollment token API

Роль и область видимости заложены в схему сразу: разграничение доступа
офицеров ИБ по подразделениям чаще является юридическим требованием,
чем техническим удобством."
```

---

## Задача 12: Сквозная проверка на чистой машине

**Files:**
- Create: `deploy/docker-compose.dev.yml`
- Create: `docs/QUICKSTART.md`
- Test: ручная проверка по инструкции

**Interfaces:**
- Consumes: всё предыдущее
- Produces: воспроизводимая процедура запуска, подтверждающая критерии готовности 1–4 и 8 из раздела 22 спецификации

- [ ] **Шаг 1: Создать `deploy/docker-compose.dev.yml`**

```yaml
# Разработческий стенд: только PostgreSQL. Сервер запускается локально,
# чтобы работал отладчик и перезагрузка по изменению файлов.
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: barysguard
      POSTGRES_PASSWORD: barysguard
      POSTGRES_DB: barysguard
    ports:
      - "5432:5432"
    volumes:
      - barysguard_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U barysguard"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  barysguard_pgdata:
```

- [ ] **Шаг 2: Создать `docs/QUICKSTART.md`**

````markdown
# BarysGuard DLP — запуск сервера

## Требования

- Linux (проверено на Ubuntu 24.04) либо Windows с WSL2
- Python 3.12+
- Docker и Docker Compose

## Запуск

```bash
docker compose -f deploy/docker-compose.dev.yml up -d

cd server
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

export BG_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard"
export BG_CA_DIR="$PWD/.local/pki"
export BG_CA_PASSPHRASE="смените-это-значение"

.venv/bin/alembic upgrade head
.venv/bin/barysguard-admin create-user --username admin --role admin
```

Команда напечатает ключ API. Он показывается один раз.

```bash
.venv/bin/uvicorn barysguard.main:app --host 0.0.0.0 --port 8000
```

## Проверка

```bash
export BG_ADMIN_KEY="<ключ из create-user>"

# 1. Сервер жив
curl -s localhost:8000/health

# 2. Удостоверяющий центр создан
curl -s localhost:8000/gateway/v1/ca | head -1

# 3. Оператор выпускает токен регистрации
curl -s -X POST localhost:8000/api/v1/enrollment-tokens \
     -H "X-Api-Key: $BG_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"max_uses": 1}'

# 4. Агент регистрируется (эмуляция; настоящий агент — план 1B)
openssl ecparam -genkey -name prime256v1 -out /tmp/agent.key
openssl req -new -key /tmp/agent.key -subj "/CN=ignored" -out /tmp/agent.csr

python - <<'PY'
import json, urllib.request
csr = open("/tmp/agent.csr").read()
body = json.dumps({
    "token": "<ТОКЕН ИЗ ШАГА 3>",
    "csr_pem": csr,
    "host": {"machine_id": "test-machine-01", "hostname": "TEST",
             "os": "linux", "os_version": "6.8.0", "arch": "amd64",
             "agent_version": "0.1.0"},
}).encode()
req = urllib.request.Request("http://localhost:8000/gateway/v1/enroll",
                             data=body, headers={"Content-Type": "application/json"})
print(json.loads(urllib.request.urlopen(req).read())["agent_id"])
PY

# 5. Агент виден в списке
curl -s localhost:8000/api/v1/agents -H "X-Api-Key: $BG_ADMIN_KEY"
```

## Переменные окружения

| Переменная | Обязательна | Назначение |
|---|---|---|
| `BG_DATABASE_URL` | да | Подключение к PostgreSQL |
| `BG_CA_PASSPHRASE` | да | Парольная фраза ключа удостоверяющего центра |
| `BG_CA_DIR` | нет | Каталог удостоверяющего центра, по умолчанию `/var/lib/barysguard/pki` |
| `BG_LOG_LEVEL` | нет | Уровень журналирования, по умолчанию `INFO` |

`BG_CA_PASSPHRASE` при утрате делает невозможным выпуск и продление сертификатов — весь флот придётся регистрировать заново. Хранить вне сервера.
````

- [ ] **Шаг 3: Выполнить проверку по инструкции целиком**

Пройти `docs/QUICKSTART.md` от начала до конца на чистом окружении. Все пять проверок должны отработать. Если хотя бы одна не проходит — исправить и повторить.

- [ ] **Шаг 4: Проверить немедленность отзыва**

```bash
# Отозвать сертификат зарегистрированного агента напрямую в базе
docker compose -f deploy/docker-compose.dev.yml exec postgres \
  psql -U barysguard -c "UPDATE agent_certificates SET revoked_at = now();"

# Обращение с этим сертификатом должно немедленно получить 403
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/gateway/v1/whoami \
     -H "X-Client-Verify: SUCCESS" -H "X-Client-Serial: <серийный номер>"
```

Expected: `403`

- [ ] **Шаг 5: Зафиксировать**

```bash
git add deploy/docker-compose.dev.yml docs/QUICKSTART.md
git commit -m "docs: quickstart and development compose stack"
```

---

## Что остаётся за пределами этого плана

| Что | Где |
|---|---|
| Go-агент: регистрация, heartbeat, offline-буфер | План 1B |
| `POST /heartbeat`, `GET /config`, очередь команд | План 1B (сервер) |
| `POST /events`, партиционирование, воркер, артефакты | План 1C |
| Метрики Prometheus (§17 спецификации) | План 1D — вместе с нагрузочным тестом, где они впервые нужны |
| Нагрузочный тест на 5000 агентов, CI, systemd, MSI, deb | План 1D |
| Каналы перехвата на агенте | Подпроект 2 |
| Движки DLP и IoC | Подпроект 3 |
