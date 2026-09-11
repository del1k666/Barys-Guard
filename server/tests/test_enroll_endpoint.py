import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy import select

from barysguard.db.models.agent import Agent
from barysguard.services.enrollment import create_enrollment_token


def make_csr() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


HOST_FACTS = {
    "machine_id": "4C4C4544-0043-5A10-8046-B7C04F335931",
    "hostname": "ACC-PC-01",
    "os": "windows",
    "os_version": "10.0.26100",
    "arch": "amd64",
    "agent_version": "0.1.0",
}


@pytest.mark.asyncio
async def test_ca_endpoint_returns_pem(app_client: AsyncClient):
    response = await app_client.get("/gateway/v1/ca")

    assert response.status_code == 200
    assert response.text.startswith("-----BEGIN CERTIFICATE-----")


@pytest.mark.asyncio
async def test_enroll_issues_certificate_and_creates_agent(app_client, session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={"token": raw, "csr_pem": make_csr(), "host": HOST_FACTS},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["heartbeat_interval_seconds"] == 30

    certificate = x509.load_pem_x509_certificate(body["certificate_pem"].encode())
    assert certificate.subject.rfc4514_string() == f"CN={body['agent_id']}"

    found = await session.execute(
        select(Agent).where(Agent.machine_id == HOST_FACTS["machine_id"])
    )
    assert found.scalar_one().hostname == "ACC-PC-01"


@pytest.mark.asyncio
async def test_enroll_with_unknown_token_is_forbidden(app_client):
    response = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": "BG-ENROLL-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
            "csr_pem": make_csr(),
            "host": HOST_FACTS,
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_enroll_with_exhausted_token_is_forbidden(app_client, session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()
    payload = {"token": raw, "csr_pem": make_csr(), "host": HOST_FACTS}

    assert (await app_client.post("/gateway/v1/enroll", json=payload)).status_code == 201
    second = await app_client.post("/gateway/v1/enroll", json=payload)

    assert second.status_code == 403


@pytest.mark.asyncio
async def test_reenrollment_reuses_existing_agent(app_client, session):
    for _ in range(2):
        raw, _ = await create_enrollment_token(
            session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
        )
        await session.commit()
        response = await app_client.post(
            "/gateway/v1/enroll",
            json={"token": raw, "csr_pem": make_csr(), "host": HOST_FACTS},
        )
        assert response.status_code == 201
        agent_id = response.json()["agent_id"]

    found = await session.execute(
        select(Agent).where(Agent.machine_id == HOST_FACTS["machine_id"])
    )
    agents = found.scalars().all()
    assert len(agents) == 1
    assert str(agents[0].id) == agent_id


@pytest.mark.asyncio
async def test_malformed_csr_is_rejected(app_client, session):
    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=None, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={"token": raw, "csr_pem": "not a csr", "host": HOST_FACTS},
    )

    assert response.status_code == 400
