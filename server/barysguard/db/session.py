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
