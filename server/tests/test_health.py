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
