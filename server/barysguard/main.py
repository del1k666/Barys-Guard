import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Response, status

from barysguard.core.config import get_settings
from barysguard.core.logging import setup_logging

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


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="BarysGuard DLP Server",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Процесс жив. Не проверяет зависимости — для этого /ready."""
        return {"status": "ok"}

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

    from barysguard.api.auth import router as auth_router
    from barysguard.api.events import router as events_router
    from barysguard.api.groups import router as groups_router
    from barysguard.api.incidents import router as incidents_router
    from barysguard.api.overview import router as overview_router
    from barysguard.api.router import router as api_router
    from barysguard.api.rules import router as rules_router
    from barysguard.api.users import router as users_router
    from barysguard.gateway.router import router as gateway_router

    app.include_router(gateway_router)
    app.include_router(auth_router)

    # Основной роутер идёт раньше: в нём живёт /api/v1/groups/{id}/config,
    # и он должен разбираться прежде, чем более общий /api/v1/groups/{id}.
    app.include_router(api_router)
    app.include_router(groups_router)
    app.include_router(users_router)
    app.include_router(overview_router)
    app.include_router(events_router)
    app.include_router(incidents_router)
    app.include_router(rules_router)

    from barysguard.gateway.event_schemas import EventEnvelope

    original_openapi = app.openapi

    def openapi_with_envelope() -> dict[str, Any]:
        # Тело /gateway/v1/events — NDJSON, FastAPI его схему не знает:
        # описание конверта добавляется в components вручную.
        schema = original_openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        if "EventEnvelope" not in components:
            envelope = EventEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")
            components.update(envelope.pop("$defs", {}))
            components["EventEnvelope"] = envelope
        return schema

    app.openapi = openapi_with_envelope  # type: ignore[method-assign]

    return app


app = create_app()
