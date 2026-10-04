from fastapi import FastAPI, Response, status

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

    return app


app = create_app()
