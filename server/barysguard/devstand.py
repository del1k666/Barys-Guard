"""Начальное состояние dev-стенда.

Всё здесь идемпотентно: стенд перезапускают постоянно, и повторный запуск не
должен ни дублировать записи, ни пересоздавать то, что уже выдано (открытый
токен регистрации после создания нигде не хранится — перезаписать его значит
осиротить агентов, которые его ещё не использовали).
"""

import datetime as dt
import ipaddress
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.enrollment import EnrollmentToken
from barysguard.db.models.user import User, UserRole
from barysguard.pki.ca import CertificateAuthority
from barysguard.services.enrollment import create_enrollment_token, hash_token
from barysguard.services.users import create_account

# Сертификат, до конца которого осталось меньше, выпускается заново.
RENEW_BEFORE = dt.timedelta(days=30)

# Токены на стенде живут год и с запасом по числу использований: агентов
# добавляют командой add-agent, и каждому нужен свой проход по токену группы.
TOKEN_TTL_HOURS = 24 * 365


@dataclass(frozen=True)
class StandGroup:
    name: str
    token_file: str
    max_uses: int


DEFAULT_GROUPS = (
    StandGroup("Бухгалтерия", "accounting.token", 20),
    StandGroup("ИТ", "it.token", 20),
)
DEFAULT_TLS_NAMES = ("localhost", "nginx", "host.docker.internal", "127.0.0.1")


@dataclass(frozen=True)
class StandOptions:
    admin_username: str
    admin_password: str
    tls_dir: Path
    enroll_dir: Path
    groups: tuple[StandGroup, ...] = DEFAULT_GROUPS
    tls_names: tuple[str, ...] = DEFAULT_TLS_NAMES
    server_cert_days: int = 365


@dataclass
class BootstrapReport:
    admin_created: bool = False
    groups_created: list[str] = field(default_factory=list)
    tokens_created: list[str] = field(default_factory=list)
    certificate_issued: bool = False


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def _certificate_is_current(path: Path, ca: CertificateAuthority, names: tuple[str, ...]) -> bool:
    try:
        certificate = x509.load_pem_x509_certificate(path.read_bytes())
        certificate.verify_directly_issued_by(ca.certificate)
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except Exception:  # noqa: BLE001
        # Повреждённый файл, чужой CA, нет расширения — любая причина, по
        # которой сертификат нельзя использовать, означает «выпустить заново».
        return False

    if certificate.not_valid_after_utc - dt.datetime.now(dt.UTC) <= RENEW_BEFORE:
        return False

    present = {str(value) for value in san.get_values_for_type(x509.DNSName)} | {
        str(value) for value in san.get_values_for_type(x509.IPAddress)
    }
    return present == set(names)


def issue_server_certificate(
    ca: CertificateAuthority, tls_dir: Path, names: tuple[str, ...], valid_days: int
) -> bool:
    """Выпускает серверный сертификат для nginx. True — если выпущен новый."""
    tls_dir.mkdir(parents=True, exist_ok=True)
    certificate_path = tls_dir / "server.crt"
    key_path = tls_dir / "server.key"

    # Публичный сертификат CA нужен и nginx (проверка клиентов), и агентам
    # (проверка сервера). Копия обновляется всегда: она дешёвая.
    (tls_dir / "ca.crt").write_bytes(ca.certificate_pem)
    (tls_dir / "ca.crt").chmod(0o644)

    if (
        certificate_path.exists()
        and key_path.exists()
        and _certificate_is_current(certificate_path, ca, names)
    ):
        return False

    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    alternative_names = [
        x509.IPAddress(ipaddress.ip_address(name)) if _is_ip(name) else x509.DNSName(name)
        for name in names
    ]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(alternative_names), critical=False)
        .sign(ca.private_key, hashes.SHA256())
    )

    # Ключ пишется первым и сразу с правами 0600: сбой между записями не должен
    # оставить новый сертификат при старом ключе, а сам ключ — стать читаемым.
    # Права 0600 оставлены намеренно: ключ читает главный процесс nginx от root.
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as key_file:
        key_file.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    key_path.chmod(0o600)
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    certificate_path.chmod(0o644)
    return True


def _read_token_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="ascii", errors="replace")


async def _token_file_is_issued(
    session: AsyncSession, token_path: Path, group_id: uuid.UUID
) -> bool:
    """Файл токена считается выданным, только если в базе есть его строка для этой группы."""
    raw = _read_token_file(token_path)
    if raw is None:
        return False
    found = (
        await session.execute(
            select(EnrollmentToken.id).where(
                EnrollmentToken.token_sha256 == hash_token(raw),
                EnrollmentToken.group_id == group_id,
            )
        )
    ).first()
    return found is not None


async def bootstrap_stand(
    session: AsyncSession, ca: CertificateAuthority, options: StandOptions
) -> BootstrapReport:
    """Создаёт всё, чего ещё нет. Транзакцию фиксирует вызывающий."""
    if not options.admin_password:
        raise ValueError("admin_password must not be empty")

    report = BootstrapReport()

    report.certificate_issued = issue_server_certificate(
        ca, options.tls_dir, options.tls_names, options.server_cert_days
    )

    admin = (
        await session.execute(select(User).where(User.username == options.admin_username))
    ).scalar_one_or_none()
    if admin is None:
        admin, _, _ = await create_account(
            session,
            username=options.admin_username,
            role=UserRole.ADMIN,
            with_password=True,
            fixed_password=options.admin_password,
            must_change_password=False,
        )
        report.admin_created = True

    options.enroll_dir.mkdir(parents=True, exist_ok=True)
    for spec in options.groups:
        group = (
            (await session.execute(select(AgentGroup).where(AgentGroup.name == spec.name)))
            .scalars()
            .first()
        )
        if group is None:
            group = AgentGroup(name=spec.name)
            session.add(group)
            await session.flush()
            report.groups_created.append(spec.name)

        token_path = options.enroll_dir / spec.token_file
        if await _token_file_is_issued(session, token_path, group.id):
            continue

        raw, _ = await create_enrollment_token(
            session,
            created_by=admin.id,
            group_id=group.id,
            ttl_hours=TOKEN_TTL_HOURS,
            max_uses=spec.max_uses,
        )
        # Файл подменяется только после того, как строка попала в сессию.
        # Если вызывающий потом откатит транзакцию, файл останется «чужим» —
        # следующий запуск это заметит по хешу и выпустит токен заново.
        temporary = token_path.with_name(token_path.name + ".tmp")
        temporary.write_text(raw, encoding="ascii")
        # Токен читают агенты, работающие не от того пользователя, что bootstrap.
        temporary.chmod(0o644)
        os.replace(temporary, token_path)
        report.tokens_created.append(spec.name)

    return report
