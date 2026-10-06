"""Начальная загрузка: администратор консоли заводится из командной строки.

Иначе первого входа в систему не существует: пароль некому выдать,
потому что выдающих ещё нет.
"""

import asyncio

import pytest
from sqlalchemy import select

from barysguard.cli import main
from barysguard.db.models.user import User, UserRole
from barysguard.services.auth import verify_password


async def test_cli_creates_an_operator_with_a_password(
    migrated_database_url, monkeypatch, capsys, session
) -> None:
    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    monkeypatch.setattr(
        "sys.argv",
        ["barysguard-admin", "create-user", "--username", "cli-admin", "--role", "admin"],
    )

    from barysguard.core.config import get_settings

    get_settings.cache_clear()

    # CLI поднимает собственный цикл событий через asyncio.run, поэтому
    # вызывается в отдельном потоке: внутри работающего цикла это запрещено.
    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)
    assert exited.value.code == 0

    printed = capsys.readouterr().out
    password = next(
        line.split(":", 1)[1].strip() for line in printed.splitlines() if line.startswith("пароль")
    )

    stored = (await session.execute(select(User).where(User.username == "cli-admin"))).scalar_one()

    assert stored.role is UserRole.ADMIN
    assert stored.must_change_password is True
    assert verify_password(password, stored.password_hash) is True
    # Машинный ключ человеку не нужен: он входит паролем.
    assert stored.api_key_sha256 is None


async def test_cli_issues_an_api_key_for_automation(
    migrated_database_url, monkeypatch, capsys, session
) -> None:
    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    monkeypatch.setattr(
        "sys.argv",
        ["barysguard-admin", "create-user", "--username", "cli-robot", "--api-key"],
    )

    from barysguard.core.config import get_settings

    get_settings.cache_clear()

    # CLI поднимает собственный цикл событий через asyncio.run, поэтому
    # вызывается в отдельном потоке: внутри работающего цикла это запрещено.
    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)
    assert exited.value.code == 0

    printed = capsys.readouterr().out
    assert "ключ" in printed

    stored = (await session.execute(select(User).where(User.username == "cli-robot"))).scalar_one()

    assert stored.api_key_sha256 is not None
    assert stored.password_hash is None
