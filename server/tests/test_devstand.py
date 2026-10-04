import asyncio
import dataclasses
import ipaddress
import uuid

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
from sqlalchemy import delete, func, select

from barysguard.cli import main
from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.enrollment import EnrollmentToken
from barysguard.db.models.user import User, UserRole
from barysguard.devstand import StandGroup, StandOptions, bootstrap_stand
from barysguard.pki.ca import ensure_ca
from barysguard.services.auth import verify_password
from barysguard.services.enrollment import hash_token


@pytest.fixture
def ca(tmp_path):
    return ensure_ca(tmp_path / "pki", "test-passphrase", "Test CA", 30)


@pytest.fixture
def options(tmp_path):
    suffix = uuid.uuid4().hex[:8]
    return StandOptions(
        admin_username=f"stand-{suffix}",
        admin_password="stand-test-password",
        tls_dir=tmp_path / "tls",
        enroll_dir=tmp_path / "enroll",
        groups=(
            StandGroup(f"Группа-A-{suffix}", "a.token", 3),
            StandGroup(f"Группа-B-{suffix}", "b.token", 2),
        ),
        tls_names=("localhost", "nginx", "127.0.0.1"),
    )


def _certificate(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


async def _group(session, name):
    return (await session.execute(select(AgentGroup).where(AgentGroup.name == name))).scalar_one()


async def test_creates_an_admin_with_the_given_password(session, ca, options):
    await bootstrap_stand(session, ca, options)

    admin = (
        await session.execute(select(User).where(User.username == options.admin_username))
    ).scalar_one()

    assert admin.role is UserRole.ADMIN
    # На стенде вход не должен требовать лишнего шага.
    assert admin.must_change_password is False
    assert verify_password(options.admin_password, admin.password_hash) is True
    assert admin.api_key_sha256 is None


async def test_creates_groups_and_group_bound_tokens(session, ca, options):
    report = await bootstrap_stand(session, ca, options)

    assert sorted(report.groups_created) == sorted(spec.name for spec in options.groups)
    assert sorted(report.tokens_created) == sorted(spec.name for spec in options.groups)

    for spec in options.groups:
        group = await _group(session, spec.name)
        raw = (options.enroll_dir / spec.token_file).read_text(encoding="ascii")
        token = (
            await session.execute(
                select(EnrollmentToken).where(EnrollmentToken.token_sha256 == hash_token(raw))
            )
        ).scalar_one()

        assert raw.startswith("BG-ENROLL-")
        assert token.group_id == group.id
        assert token.max_uses == spec.max_uses
        assert token.used_count == 0


async def test_second_run_changes_nothing(session, ca, options):
    await bootstrap_stand(session, ca, options)
    tokens = {
        spec.token_file: (options.enroll_dir / spec.token_file).read_text()
        for spec in options.groups
    }
    certificate = (options.tls_dir / "server.crt").read_bytes()

    report = await bootstrap_stand(session, ca, options)

    assert report.admin_created is False
    assert report.groups_created == []
    assert report.tokens_created == []
    assert report.certificate_issued is False

    admins = (
        await session.execute(
            select(func.count()).select_from(User).where(User.username == options.admin_username)
        )
    ).scalar_one()
    names = [spec.name for spec in options.groups]
    groups = (
        await session.execute(
            select(func.count()).select_from(AgentGroup).where(AgentGroup.name.in_(names))
        )
    ).scalar_one()
    group_ids = [(await _group(session, name)).id for name in names]
    stored_tokens = (
        await session.execute(
            select(func.count())
            .select_from(EnrollmentToken)
            .where(EnrollmentToken.group_id.in_(group_ids))
        )
    ).scalar_one()

    assert (admins, groups, stored_tokens) == (1, 2, 2)
    assert {
        spec.token_file: (options.enroll_dir / spec.token_file).read_text()
        for spec in options.groups
    } == tokens
    assert (options.tls_dir / "server.crt").read_bytes() == certificate


async def test_server_certificate_is_signed_by_the_ca_and_carries_every_name(session, ca, options):
    await bootstrap_stand(session, ca, options)

    certificate = _certificate(options.tls_dir / "server.crt")
    certificate.verify_directly_issued_by(ca.certificate)

    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert set(names.get_values_for_type(x509.DNSName)) == {"localhost", "nginx"}
    assert set(names.get_values_for_type(x509.IPAddress)) == {ipaddress.ip_address("127.0.0.1")}

    usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in usage

    # nginx проверяет клиентские сертификаты тем же CA: файл лежит рядом.
    assert (options.tls_dir / "ca.crt").read_bytes() == ca.certificate_pem


async def test_a_certificate_close_to_expiry_is_reissued(session, ca, options):
    await bootstrap_stand(session, ca, dataclasses.replace(options, server_cert_days=10))
    old = (options.tls_dir / "server.crt").read_bytes()

    report = await bootstrap_stand(session, ca, options)

    assert report.certificate_issued is True
    assert (options.tls_dir / "server.crt").read_bytes() != old


async def test_a_certificate_missing_a_name_is_reissued(session, ca, options):
    await bootstrap_stand(session, ca, options)
    wider = dataclasses.replace(options, tls_names=(*options.tls_names, "host.docker.internal"))

    report = await bootstrap_stand(session, ca, wider)

    assert report.certificate_issued is True
    names = (
        _certificate(options.tls_dir / "server.crt")
        .extensions.get_extension_for_class(x509.SubjectAlternativeName)
        .value
    )
    assert "host.docker.internal" in names.get_values_for_type(x509.DNSName)


@pytest.fixture
def stand_env(monkeypatch, tmp_path, migrated_database_url):
    from barysguard.core.config import get_settings
    from barysguard.pki.provider import get_ca

    suffix = uuid.uuid4().hex[:8]
    values = {
        "BG_DATABASE_URL": migrated_database_url,
        "BG_CA_DIR": str(tmp_path / "pki"),
        "BG_CA_PASSPHRASE": "test-passphrase",
        "BG_STAND": "1",
        "BG_BOOTSTRAP_ADMIN_USERNAME": f"cli-stand-{suffix}",
        "BG_BOOTSTRAP_ADMIN_PASSWORD": "stand-cli-password",
        "BG_STAND_TLS_DIR": str(tmp_path / "tls"),
        "BG_STAND_ENROLL_DIR": str(tmp_path / "enroll"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["barysguard-admin", "bootstrap-dev"])
    monkeypatch.setattr(
        "barysguard.cli.DEFAULT_GROUPS", (StandGroup(f"CLI-{suffix}", "cli.token", 5),)
    )
    get_settings.cache_clear()
    get_ca.cache_clear()
    yield {**values, "group": f"CLI-{suffix}"}
    get_settings.cache_clear()
    get_ca.cache_clear()


async def test_bootstrap_dev_creates_the_stand_state(stand_env, session, capsys, tmp_path):
    # CLI поднимает собственный цикл событий через asyncio.run, поэтому
    # вызывается в отдельном потоке.
    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)
    assert exited.value.code == 0

    admin = (
        await session.execute(
            select(User).where(User.username == stand_env["BG_BOOTSTRAP_ADMIN_USERNAME"])
        )
    ).scalar_one()
    assert verify_password("stand-cli-password", admin.password_hash) is True
    assert (tmp_path / "enroll" / "cli.token").read_text().startswith("BG-ENROLL-")
    assert (tmp_path / "tls" / "server.crt").exists()
    assert stand_env["BG_BOOTSTRAP_ADMIN_USERNAME"] in capsys.readouterr().out

    # CLI фиксирует изменения, поэтому за собой убираем сами.
    group = await _group(session, stand_env["group"])
    await session.execute(delete(EnrollmentToken).where(EnrollmentToken.group_id == group.id))
    await session.execute(delete(AgentGroup).where(AgentGroup.id == group.id))
    await session.execute(delete(User).where(User.id == admin.id))
    await session.commit()


async def test_bootstrap_dev_refuses_outside_a_stand(stand_env, monkeypatch, capsys, tmp_path):
    from barysguard.core.config import get_settings

    monkeypatch.delenv("BG_STAND")
    get_settings.cache_clear()

    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)

    assert exited.value.code == 1
    assert "BG_STAND=1" in capsys.readouterr().out
    assert not (tmp_path / "tls").exists()


async def test_bootstrap_dev_refuses_without_an_admin_password(
    stand_env, monkeypatch, capsys, tmp_path
):
    from barysguard.core.config import get_settings

    monkeypatch.setenv("BG_BOOTSTRAP_ADMIN_PASSWORD", "")
    get_settings.cache_clear()

    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)

    assert exited.value.code == 1
    assert "BG_BOOTSTRAP_ADMIN_PASSWORD" in capsys.readouterr().out
    assert not (tmp_path / "tls").exists()
