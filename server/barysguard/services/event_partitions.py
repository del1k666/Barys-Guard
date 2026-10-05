"""Помесячные разделы таблицы events."""

from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Окно, в пределах которого события из events_default получают собственные разделы.
PAST_WINDOW_MONTHS = 36
FUTURE_WINDOW_MONTHS = 12


def _add_months(first_of_month: date, months: int) -> date:
    index = first_of_month.year * 12 + (first_of_month.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _bound(day: date) -> str:
    return f"{day.isoformat()} 00:00:00+00"


async def _create_partition(session: AsyncSession, name: str, start: date, end: date) -> None:
    # Имя и границы строятся из объектов date, а не из пользовательского ввода:
    # DDL не принимает параметры привязки.
    lower, upper = _bound(start), _bound(end)

    # Если в events_default уже лежат строки из этого диапазона (часы агента
    # убежали вперёд), база откажется создавать раздел. Строки временно
    # убираются и возвращаются уже в новый раздел.
    await session.execute(text("CREATE TEMP TABLE _events_move (LIKE events_default)"))
    await session.execute(
        text(
            "WITH moved AS (DELETE FROM events_default "  # noqa: S608
            f"WHERE occurred_at >= '{lower}' AND occurred_at < '{upper}' RETURNING *) "
            "INSERT INTO _events_move SELECT * FROM moved"
        )
    )
    await session.execute(
        text(
            f"CREATE TABLE {name} PARTITION OF events "  # noqa: S608
            f"FOR VALUES FROM ('{lower}') TO ('{upper}')"
        )
    )
    await session.execute(text("INSERT INTO events SELECT * FROM _events_move"))
    await session.execute(text("DROP TABLE _events_move"))


async def ensure_event_partitions(
    session: AsyncSession, months_ahead: int = 2, today: date | None = None
) -> list[str]:
    """Создаёт недостающие разделы от текущего месяца на months_ahead месяцев вперёд.

    Возвращает имена созданных разделов. Повторный вызов ничего не делает.
    """
    first = (today or datetime.now(UTC).date()).replace(day=1)
    created: list[str] = []

    months = {_add_months(first, offset) for offset in range(months_ahead + 1)}

    # Сервер, проработавший месяцы без перезапуска, оставил события прошлых
    # месяцев в events_default. Разделы под них создаются тоже, но только в
    # разумном окне: часы агента, ушедшие на десятки лет, не должны порождать
    # тысячи разделов.
    window_start = _add_months(first, -PAST_WINDOW_MONTHS)
    window_end = _add_months(first, FUTURE_WINDOW_MONTHS)
    stray = (
        await session.execute(
            text(
                "SELECT DISTINCT date_trunc('month', occurred_at AT TIME ZONE 'UTC')::date "
                "FROM events_default"
            )
        )
    ).scalars()
    months.update(month for month in stray if window_start <= month <= window_end)

    for start in sorted(months):
        end = _add_months(start, 1)
        name = f"events_{start:%Y_%m}"

        exists = (
            await session.execute(text("SELECT to_regclass(:name)"), {"name": name})
        ).scalar_one()
        if exists is not None:
            continue

        await _create_partition(session, name, start, end)
        created.append(name)

    return created
