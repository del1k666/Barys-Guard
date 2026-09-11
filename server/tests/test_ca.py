import os
import stat

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from barysguard.core.errors import InvalidCsr
from barysguard.pki.ca import ensure_ca

PASSPHRASE = "test-passphrase"  # noqa: S105
AGENT_ID = "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f"


def _make_csr(common_name: str = "ignored-by-server") -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


def _make_csr_with_tampered_subject() -> bytes:
    """CSR, у которого имя субъекта подменено уже после подписи.

    Подставляется строка той же длины, поэтому ASN.1 остаётся разбираемым:
    запрос выглядит правильным ровно до проверки подписи.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "tampered-subject")])
        )
        .sign(key, hashes.SHA256())
    )
    der = csr.public_bytes(serialization.Encoding.DER)
    tampered = der.replace(b"tampered-subject", b"tampered-subjecX", 1)
    return x509.load_der_x509_csr(tampered).public_bytes(serialization.Encoding.PEM)


def test_ca_is_created_on_first_call(tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    assert (tmp_path / "ca.crt").exists()
    assert (tmp_path / "ca.key").exists()
    assert ca.certificate.subject.rfc4514_string() == "CN=BarysGuard Test CA"


@pytest.mark.skipif(
    os.name == "nt",
    reason="chmod на Windows переключает только атрибут «только чтение»; "
    "права проверяются на Linux, где сервер и работает",
)
def test_ca_key_file_is_not_world_readable(tmp_path):
    ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    mode = (tmp_path / "ca.key").stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


def test_existing_ca_is_loaded_not_regenerated(tmp_path):
    first = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)
    second = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    assert first.certificate.serial_number == second.certificate.serial_number


def test_ca_key_is_encrypted_with_passphrase(tmp_path):
    ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)
    raw = (tmp_path / "ca.key").read_bytes()

    with pytest.raises(TypeError):
        serialization.load_pem_private_key(raw, password=None)


def test_signed_certificate_uses_server_supplied_subject(tmp_path):
    """Субъект CSR игнорируется: агент на момент запроса не знает своего agent_id."""
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    pem, serial = ca.sign_csr(_make_csr("attacker-chosen-name"), AGENT_ID, 90)
    certificate = x509.load_pem_x509_certificate(pem)

    assert certificate.subject.rfc4514_string() == f"CN={AGENT_ID}"
    assert certificate.issuer == ca.certificate.subject
    assert certificate.serial_number == serial


def test_signed_certificate_is_client_auth_only(tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    pem, _ = ca.sign_csr(_make_csr(), AGENT_ID, 90)
    certificate = x509.load_pem_x509_certificate(pem)

    basic = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value

    assert basic.ca is False
    assert x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in eku
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH not in eku


def test_csr_with_broken_signature_is_rejected(tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    with pytest.raises(InvalidCsr):
        ca.sign_csr(_make_csr_with_tampered_subject(), AGENT_ID, 90)


def test_unparsable_csr_is_rejected(tmp_path):
    ca = ensure_ca(tmp_path, PASSPHRASE, "BarysGuard Test CA", 3650)

    with pytest.raises(InvalidCsr):
        ca.sign_csr(b"this is not a certificate signing request", AGENT_ID, 90)
