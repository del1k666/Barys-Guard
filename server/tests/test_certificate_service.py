from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from barysguard.db.models.agent import Agent
from barysguard.pki.ca import ensure_ca
from barysguard.pki.service import (
    find_active_certificate,
    issue_certificate,
    revoke_certificate,
    serial_to_hex,
)

PASSPHRASE = "test-passphrase"  # noqa: S105


def _make_csr() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "x")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


async def _make_agent(session) -> Agent:
    agent = Agent(
        machine_id=f"machine-{datetime.now(UTC).timestamp()}",
        hostname="TEST-HOST",
        os="linux",
        os_version="6.8.0",
        arch="amd64",
        agent_version="0.1.0",
    )
    session.add(agent)
    await session.flush()
    return agent


def test_serial_is_lowercase_hex_without_leading_zeros():
    assert serial_to_hex(255) == "ff"
    assert serial_to_hex(1) == "1"


@pytest.mark.asyncio
async def test_issued_certificate_is_recorded_and_findable(session, tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "Test CA", 3650)
    agent = await _make_agent(session)

    pem, record = await issue_certificate(session, ca, agent, _make_csr(), 90)
    certificate = x509.load_pem_x509_certificate(pem)

    assert record.agent_id == agent.id
    assert record.serial == serial_to_hex(certificate.serial_number)
    assert certificate.subject.rfc4514_string() == f"CN={agent.id}"

    found = await find_active_certificate(session, record.serial)
    assert found is not None
    assert found.id == record.id


@pytest.mark.asyncio
async def test_revoked_certificate_is_not_active(session, tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "Test CA", 3650)
    agent = await _make_agent(session)
    _, record = await issue_certificate(session, ca, agent, _make_csr(), 90)

    await revoke_certificate(session, record.serial, reason="compromised")

    assert await find_active_certificate(session, record.serial) is None


@pytest.mark.asyncio
async def test_expired_certificate_is_not_active(session, tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "Test CA", 3650)
    agent = await _make_agent(session)
    _, record = await issue_certificate(session, ca, agent, _make_csr(), 90)

    record.not_after = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    assert await find_active_certificate(session, record.serial) is None


@pytest.mark.asyncio
async def test_unknown_serial_is_not_active(session):
    assert await find_active_certificate(session, "deadbeef") is None
