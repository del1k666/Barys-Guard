from datetime import UTC, datetime, timedelta

import pytest

from tests.helpers import enroll_agent


@pytest.mark.asyncio
async def test_valid_client_certificate_identifies_agent(app_client, session):
    enrolled = await enroll_agent(app_client, session, "mtls-happy-path")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": enrolled.serial},
    )

    assert response.status_code == 200
    assert response.json()["agent_id"] == str(enrolled.agent_id)


@pytest.mark.asyncio
async def test_missing_verify_header_is_forbidden(app_client, session):
    enrolled = await enroll_agent(app_client, session, "mtls-no-verify")

    response = await app_client.get(
        "/gateway/v1/whoami", headers={"X-Client-Serial": enrolled.serial}
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_failed_verify_is_forbidden(app_client, session):
    """Ключевой тест: подделка заголовков в обход nginx не должна работать."""
    enrolled = await enroll_agent(app_client, session, "mtls-failed-verify")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={
            "X-Client-Verify": "FAILED:certificate has expired",
            "X-Client-Serial": enrolled.serial,
        },
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

    enrolled = await enroll_agent(app_client, session, "mtls-revoked")
    await revoke_certificate(session, enrolled.serial, reason="test")
    await session.commit()

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": enrolled.serial},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_expired_certificate_is_forbidden(app_client, session):
    from sqlalchemy import select

    from barysguard.db.models.certificate import AgentCertificate

    enrolled = await enroll_agent(app_client, session, "mtls-expired")
    found = await session.execute(
        select(AgentCertificate).where(AgentCertificate.serial == enrolled.serial)
    )
    found.scalar_one().not_after = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": enrolled.serial},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_serial_is_normalised_from_nginx_format(app_client, session):
    """nginx отдаёт серийный номер в верхнем регистре, возможно с ведущими нулями."""
    enrolled = await enroll_agent(app_client, session, "mtls-serial-case")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": enrolled.serial.upper()},
    )

    assert response.status_code == 200
