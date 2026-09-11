from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _csr() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


async def _enroll(app_client, session, machine_id: str) -> tuple[str, str]:
    """Регистрирует агента и возвращает (agent_id, serial сертификата в hex)."""
    from barysguard.services.enrollment import create_enrollment_token

    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": raw,
            "csr_pem": _csr(),
            "host": {
                "machine_id": machine_id,
                "hostname": "H",
                "os": "linux",
                "os_version": "6.8.0",
                "arch": "amd64",
                "agent_version": "0.1.0",
            },
        },
    )
    body = response.json()
    certificate = x509.load_pem_x509_certificate(body["certificate_pem"].encode())
    return body["agent_id"], format(certificate.serial_number, "x")


@pytest.mark.asyncio
async def test_valid_client_certificate_identifies_agent(app_client, session):
    agent_id, serial = await _enroll(app_client, session, "mtls-happy-path")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 200
    assert response.json()["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_missing_verify_header_is_forbidden(app_client, session):
    _, serial = await _enroll(app_client, session, "mtls-no-verify")

    response = await app_client.get(
        "/gateway/v1/whoami", headers={"X-Client-Serial": serial}
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_failed_verify_is_forbidden(app_client, session):
    """Ключевой тест: подделка заголовков в обход nginx не должна работать."""
    _, serial = await _enroll(app_client, session, "mtls-failed-verify")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "FAILED:certificate has expired", "X-Client-Serial": serial},
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

    _, serial = await _enroll(app_client, session, "mtls-revoked")
    await revoke_certificate(session, serial, reason="test")
    await session.commit()

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_expired_certificate_is_forbidden(app_client, session):
    from sqlalchemy import select

    from barysguard.db.models.certificate import AgentCertificate

    _, serial = await _enroll(app_client, session, "mtls-expired")
    found = await session.execute(
        select(AgentCertificate).where(AgentCertificate.serial == serial)
    )
    found.scalar_one().not_after = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_serial_is_normalised_from_nginx_format(app_client, session):
    """nginx отдаёт серийный номер в верхнем регистре, возможно с ведущими нулями."""
    _, serial = await _enroll(app_client, session, "mtls-serial-case")

    response = await app_client.get(
        "/gateway/v1/whoami",
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial.upper()},
    )

    assert response.status_code == 200
