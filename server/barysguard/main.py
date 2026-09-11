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

    return app


app = create_app()
