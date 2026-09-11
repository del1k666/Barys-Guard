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

    monkeypatch.setenv("BG_DATABASE_URL", "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nothing")
    get_settings.cache_clear()
    reset_session_state()

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    get_settings.cache_clear()
    reset_session_state()
    assert response.status_code == 503
