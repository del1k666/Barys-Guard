import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from barysguard.db.session import create_engine_from_url, session_factory

# Интеграционные тесты идут на настоящем PostgreSQL: SQLite ведёт себя иначе
# на партиционировании, SKIP LOCKED и ON CONFLICT и даёт ложную уверенность.
# Сервер берётся из BG_TEST_DATABASE_URL, а если переменная не задана —
# поднимается в контейнере через testcontainers.
TEST_DATABASE_URL_ENV = "BG_TEST_DATABASE_URL"


def _with_database_name(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _execute_outside_transaction(url: str, statement: str) -> None:
    """CREATE/DROP DATABASE нельзя выполнить внутри транзакции, отсюда AUTOCOMMIT."""
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Чистая база на время прогона тестов.

    На сервере из BG_TEST_DATABASE_URL создаётся временная база со случайным
    именем и удаляется по завершении: прогоны не наследуют состояние друг друга.
    Без переменной окружения PostgreSQL поднимается в контейнере — нужен Docker.
    """
    admin_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if admin_url:
        name = f"barysguard_test_{uuid.uuid4().hex[:12]}"
        asyncio.run(_execute_outside_transaction(admin_url, f'CREATE DATABASE "{name}"'))
        try:
            yield _with_database_name(admin_url, name)
        finally:
            asyncio.run(
                _execute_outside_transaction(admin_url, f'DROP DATABASE "{name}" WITH (FORCE)')
            )
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        yield (
            f"postgresql+asyncpg://{container.username}:"
            f"{container.password}@{host}:{port}/{container.dbname}"
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
    """Сессия на один тест.

    Работает прямо на движке, а не внутри заранее открытой внешней транзакции.
    Иначе commit в тесте, который готовит данные для последующего HTTP-запроса,
    оказался бы всего лишь release savepoint: приложение ходит в базу по своему
    соединению и подготовленного не увидело бы. Незафиксированное откатывается
    здесь, а осознанно зафиксированное убирает TRUNCATE в фикстуре app_client.
    """
    engine = create_engine_from_url(migrated_database_url)
    maker = session_factory(engine)

    async with maker() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(migrated_database_url, tmp_path, monkeypatch):
    """Приложение, подключённое к тестовой базе, со своим CA во временном каталоге."""
    from httpx import ASGITransport, AsyncClient

    from barysguard.core.config import get_settings
    from barysguard.db.session import get_session, reset_session_state
    from barysguard.main import create_app
    from barysguard.pki.provider import get_ca

    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("BG_CA_DIR", str(tmp_path / "pki"))
    monkeypatch.setenv("BG_CA_PASSPHRASE", "test-passphrase")

    # Клиент тестов ходит по http, а cookie с флагом Secure браузерный клиент
    # по http не вернёт. В бою флаг остаётся включённым.
    monkeypatch.setenv("BG_COOKIE_SECURE", "false")

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
                "TRUNCATE events, artifacts, upload_sessions, commands, agent_configs, "
                "agent_certificates, enrollment_tokens, "
                "console_sessions, agents, agent_groups, audit_log, users "
                "RESTART IDENTITY CASCADE"
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
