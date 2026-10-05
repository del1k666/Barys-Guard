import logging
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _ensure_partitions_on_startup()
    yield


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
    from barysguard.api.overview import router as overview_router
    from barysguard.api.router import router as api_router
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

    return app


app = create_app()
