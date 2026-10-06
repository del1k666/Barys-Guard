"""Команда ensure-partitions."""

import asyncio

import pytest

from barysguard.cli import main


async def test_cli_ensure_partitions_reports_created_partitions(
    migrated_database_url, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("BG_DATABASE_URL", migrated_database_url)
    monkeypatch.setattr(
        "sys.argv", ["barysguard-admin", "ensure-partitions", "--months-ahead", "4"]
    )

    from barysguard.core.config import get_settings

    get_settings.cache_clear()

    # CLI поднимает собственный цикл событий через asyncio.run, поэтому
    # вызывается в отдельном потоке: внутри работающего цикла это запрещено.
    with pytest.raises(SystemExit) as exited:
        await asyncio.to_thread(main)

    assert exited.value.code == 0
    assert "создано разделов" in capsys.readouterr().out
