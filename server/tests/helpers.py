import uuid
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession


def build_csr() -> str:
    """Запрос на сертификат. Субъект сервером игнорируется, имя произвольно."""
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


@dataclass(frozen=True)
class EnrolledAgent:
    agent_id: uuid.UUID
    serial: str
    headers: dict[str, str]
    body: dict


async def enroll_agent(
    app_client,
    session: AsyncSession,
    machine_id: str,
    group_id: uuid.UUID | None = None,
) -> EnrolledAgent:
    """Регистрирует агента через настоящий эндпоинт и отдаёт заголовки mTLS.

    Заголовки формируются так же, как их проставляет nginx после проверки
    клиентского сертификата.
    """
    from barysguard.services.enrollment import create_enrollment_token

    raw, _ = await create_enrollment_token(
        session, created_by=None, group_id=group_id, ttl_hours=24, max_uses=1
    )
    await session.commit()

    response = await app_client.post(
        "/gateway/v1/enroll",
        json={
            "token": raw,
            "csr_pem": build_csr(),
            "host": {
                "machine_id": machine_id,
                "hostname": "ws-1",
                "os": "linux",
                "os_version": "6.8.0",
                "arch": "amd64",
                "agent_version": "0.1.0",
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    certificate = x509.load_pem_x509_certificate(body["certificate_pem"].encode())
    serial = format(certificate.serial_number, "x")

    return EnrolledAgent(
        agent_id=uuid.UUID(body["agent_id"]),
        serial=serial,
        headers={"X-Client-Verify": "SUCCESS", "X-Client-Serial": serial},
        body=body,
    )
