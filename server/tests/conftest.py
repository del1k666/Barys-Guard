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
