import datetime as dt
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from barysguard.core.errors import InvalidCsr

CA_CERT_FILENAME = "ca.crt"
CA_KEY_FILENAME = "ca.key"


@dataclass(frozen=True)
class CertificateAuthority:
    certificate: x509.Certificate
    private_key: ec.EllipticCurvePrivateKey

    @property
    def certificate_pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    def sign_csr(self, csr_pem: bytes, subject_cn: str, valid_days: int) -> tuple[bytes, int]:
        """Подписывает открытый ключ из CSR.

        Субъект самого CSR не используется: агент на момент формирования
        запроса ещё не знает своего agent_id, а доверять полю, которое
        заполняет клиент, нельзя в принципе. Субъект формирует сервер.
        """
        try:
            csr = x509.load_pem_x509_csr(csr_pem)
        except ValueError as exc:
            raise InvalidCsr(f"cannot parse CSR: {exc}") from exc

        if not csr.is_signature_valid:
            raise InvalidCsr("CSR signature is invalid")

        now = dt.datetime.now(dt.UTC)
        serial = x509.random_serial_number()

        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)]))
            .issuer_name(self.certificate.subject)
            .public_key(csr.public_key())
            .serial_number(serial)
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=valid_days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
            )
            .add_extension(
                # Ключ берётся у самого центра, а не из его сертификата: тип
                # открытого ключа сертификата шире и включает схемы, которых
                # from_issuer_public_key не принимает.
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    self.private_key.public_key()
                ),
                critical=False,
            )
            .sign(self.private_key, hashes.SHA256())
        )

        return certificate.public_bytes(serialization.Encoding.PEM), serial


def _create_ca(
    ca_dir: Path, passphrase: str, common_name: str, valid_days: int
) -> CertificateAuthority:
    key = ec.generate_private_key(ec.SECP384R1())
    now = dt.datetime.now(dt.UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_dir.chmod(stat.S_IRWXU)  # 0700

    key_path = ca_dir / CA_KEY_FILENAME
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
        )
    )
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600

    (ca_dir / CA_CERT_FILENAME).write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )

    return CertificateAuthority(certificate=certificate, private_key=key)


def ensure_ca(
    ca_dir: Path, passphrase: str, common_name: str, valid_days: int
) -> CertificateAuthority:
    """Загружает удостоверяющий центр, создавая его при первом обращении."""
    if not passphrase:
        raise ValueError("BG_CA_PASSPHRASE must be set")

    cert_path = ca_dir / CA_CERT_FILENAME
    key_path = ca_dir / CA_KEY_FILENAME

    if not (cert_path.exists() and key_path.exists()):
        return _create_ca(ca_dir, passphrase, common_name, valid_days)

    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = serialization.load_pem_private_key(
        key_path.read_bytes(), password=passphrase.encode("utf-8")
    )
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return CertificateAuthority(certificate=certificate, private_key=key)
