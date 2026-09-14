import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.errors import CommandNotDelivered
from barysguard.db.models.command import Command, CommandStatus, CommandType

# Больше полусотни указаний за один цикл опроса означает не работу оператора,
# а ошибку автоматики. Остаток уйдёт следующим heartbeat.
MAX_COMMANDS_PER_HEARTBEAT = 50

# Диагностика агента не является каналом передачи артефактов: для этого
# предусмотрен POST /artifacts.
MAX_RESULT_BYTES = 64 * 1024

TERMINAL_STATUSES = (CommandStatus.DONE, CommandStatus.FAILED, CommandStatus.EXPIRED)
DELIVERED_STATUSES = (CommandStatus.SENT, CommandStatus.RUNNING)


async def queue_command(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    command_type: CommandType,
    payload: dict[str, Any],
    created_by: uuid.UUID | None,
    ttl_seconds: int,
) -> Command:
    command = Command(
        agent_id=agent_id,
        type=command_type,
        payload=payload,
        created_by=created_by,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    session.add(command)
    await session.flush()
    return command


async def expire_stale_commands(session: AsyncSession, agent_id: uuid.UUID) -> int:
    """Просроченные команды агента переводятся в expired до выборки."""
    now = datetime.now(UTC)
    result = await session.execute(
        update(Command)
        .where(
            Command.agent_id == agent_id,
            Command.status.in_((CommandStatus.QUEUED, CommandStatus.SENT)),
            Command.expires_at <= now,
        )
        .values(status=CommandStatus.EXPIRED, completed_at=now)
    )
    # execute обещает Result, но DML всегда возвращает CursorResult,
    # и только у него есть rowcount.
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def dequeue_commands(session: AsyncSession, agent_id: uuid.UUID, limit: int) -> list[Command]:
    """Выдать команды агенту и пометить их отправленными.

    SKIP LOCKED нужен потому, что агент может прислать два heartbeat подряд
    в разные воркеры uvicorn: без него второй запрос ждал бы первый, а не
    прошёл мимо занятых строк.
    """
    now = datetime.now(UTC)
    commands = (
        (
            await session.execute(
                select(Command)
                .where(
                    Command.agent_id == agent_id,
                    Command.status == CommandStatus.QUEUED,
                    Command.expires_at > now,
                )
                .order_by(Command.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    for command in commands:
        command.status = CommandStatus.SENT
        command.sent_at = now

    await session.flush()
    return list(commands)


async def record_command_result(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    command_id: uuid.UUID,
    status: CommandStatus,
    result: dict[str, Any],
) -> tuple[Command, bool] | None:
    """Сохранить результат команды.

    None означает «нет такой команды у этого агента». Второй элемент пары —
    признак того, что состояние действительно изменилось: обработчику нужно
    отличить принятие от повтора, и выводить это из содержимого полей значило
    бы угадывать.
    """
    command = (
        await session.execute(
            select(Command).where(Command.id == command_id, Command.agent_id == agent_id)
        )
    ).scalar_one_or_none()

    if command is None:
        return None

    # Повтор после обрыва связи не должен затирать сохранённое.
    if command.status in TERMINAL_STATUSES:
        return command, False

    if command.status not in DELIVERED_STATUSES:
        raise CommandNotDelivered(str(command_id))

    command.status = status
    command.result = result
    command.completed_at = datetime.now(UTC)
    await session.flush()
    return command, True
