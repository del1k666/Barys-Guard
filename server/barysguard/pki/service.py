import uuid
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import Agent
from barysguard.db.models.certificate import AgentCertificate
from barysguard.pki.ca import CertificateAuthority


def serial_to_hex(serial: int) -> str:
    """Каноническая форма серийного номера: строчные шестнадцатеричные без ведущих нулей."""
    return format(serial, "x")


def normalize_serial(value: str) -> str:
    """Приводит серийный номер к канонической форме.

    nginx отдаёт значение в верхнем регистре и добавляет ведущий ноль,
    когда старший бит установлен. Без нормализации поиск не найдёт запись.
    Отдельная ветка для строки из одних нулей: lstrip вернул бы пустую строку.
    """
    stripped = value.strip().lower().lstrip("0")
    return stripped or "0"


async def issue_certificate(
    session: AsyncSession,
    ca: CertificateAuthority,
    agent: Agent,
    csr_pem: bytes,
    valid_days: int,
) -> tuple[bytes, AgentCertificate]:
    pem, serial = ca.sign_csr(csr_pem, str(agent.id), valid_days)
    certificate = x509.load_pem_x509_certificate(pem)

    record = AgentCertificate(
        agent_id=agent.id,
        serial=serial_to_hex(serial),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
    )
    session.add(record)
    await session.flush()
    return pem, record


async def find_active_certificate(
    session: AsyncSession, serial_hex: str
) -> AgentCertificate | None:
    """Действующий сертификат: не отозван и не просрочен."""
    now = datetime.now(UTC)
    statement = select(AgentCertificate).where(
        AgentCertificate.serial == normalize_serial(serial_hex),
        AgentCertificate.revoked_at.is_(None),
        AgentCertificate.not_before <= now,
        AgentCertificate.not_after > now,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def revoke_certificate(session: AsyncSession, serial_hex: str, reason: str) -> None:
    statement = select(AgentCertificate).where(
        AgentCertificate.serial == normalize_serial(serial_hex)
    )
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None or record.revoked_at is not None:
        return
    record.revoked_at = datetime.now(UTC)
    record.revocation_reason = reason
    await session.flush()


async def supersede_certificate(session: AsyncSession, old_serial: str, new_id: uuid.UUID) -> None:
    """Помечает старый сертификат заменённым, НЕ отзывая его.

    Отзыв в момент продления сломал бы агента, у которого запрос прошёл,
    а ответ не дошёл: он остался бы со старым сертификатом, уже недействительным.
    Старый сертификат доживает свой срок сам.
    """
    statement = select(AgentCertificate).where(
        AgentCertificate.serial == normalize_serial(old_serial)
    )
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None:
        return
    record.superseded_by = new_id
    await session.flush()
