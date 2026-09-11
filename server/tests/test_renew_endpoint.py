import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from barysguard.db.models.certificate import AgentCertificate


def _csr() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


async def _enroll(app_client, session, machine_id: str) -> tuple[str, str]:
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
async def test_renew_issues_new_certificate_for_same_agent(app_client, session):
    agent_id, serial = await _enroll(app_client, session, "renew-happy")

    response = await app_client.post(
        "/gateway/v1/renew",
        json={"csr_pem": _csr()},
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 200
    new_certificate = x509.load_pem_x509_certificate(
        response.json()["certificate_pem"].encode()
    )
    assert new_certificate.subject.rfc4514_string() == f"CN={agent_id}"
    assert format(new_certificate.serial_number, "x") != serial


@pytest.mark.asyncio
async def test_old_certificate_is_marked_superseded_but_still_valid(app_client, session):
    """Старый сертификат не отзывается сразу: агент должен успеть сохранить новый."""
    _, serial = await _enroll(app_client, session, "renew-supersede")

    await app_client.post(
        "/gateway/v1/renew",
        json={"csr_pem": _csr()},
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    found = await session.execute(
        select(AgentCertificate).where(AgentCertificate.serial == serial)
    )
    old = found.scalar_one()
    assert old.superseded_by is not None
    assert old.revoked_at is None


@pytest.mark.asyncio
async def test_renew_without_certificate_is_forbidden(app_client):
    response = await app_client.post("/gateway/v1/renew", json={"csr_pem": _csr()})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_renew_with_revoked_certificate_is_forbidden(app_client, session):
    from barysguard.pki.service import revoke_certificate

    _, serial = await _enroll(app_client, session, "renew-revoked")
    await revoke_certificate(session, serial, reason="test")
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/renew",
        json={"csr_pem": _csr()},
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
    )

    assert response.status_code == 403
