from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, not_

from barysguard.db.models.agent import Agent, AgentStatus

# Один пропущенный heartbeat — сетевая икота, три подряд — уже молчание.
OFFLINE_MISSED_INTERVALS = 3

# Статусы, которые снимает только оператор. Молчание агента их не меняет.
OPERATOR_CONTROLLED = (AgentStatus.QUARANTINED, AgentStatus.REVOKED)


def offline_deadline(interval_seconds: int, now: datetime) -> datetime:
    """Момент, раньше которого последний heartbeat означает молчание."""
    return now - timedelta(seconds=interval_seconds * OFFLINE_MISSED_INTERVALS)


def status_condition(wanted: str, interval_seconds: int, now: datetime) -> ColumnElement[bool]:
    """Тот же признак, что и в derive_status, но выражением SQL.

    Дублирование правила нежелательно, но выбор простой: либо оно живёт
    здесь, рядом со своим близнецом и меняется вместе с ним, либо фильтр
    по статусу вытягивает весь флот в приложение на каждый запрос списка.
    """
    deadline = offline_deadline(interval_seconds, now)
    operator_controlled = Agent.status.in_(OPERATOR_CONTROLLED)

    if wanted in {status.value for status in OPERATOR_CONTROLLED}:
        return Agent.status == wanted

    if wanted == AgentStatus.PENDING.value:
        return and_(not_(operator_controlled), Agent.last_heartbeat_at.is_(None))

    if wanted == AgentStatus.OFFLINE.value:
        return and_(
            not_(operator_controlled),
            Agent.last_heartbeat_at.is_not(None),
            Agent.last_heartbeat_at < deadline,
        )

    return and_(
        not_(operator_controlled),
        Agent.status == wanted,
        Agent.last_heartbeat_at.is_not(None),
        Agent.last_heartbeat_at >= deadline,
    )


def derive_status(agent: Agent, interval_seconds: int, now: datetime) -> str:
    """Статус агента с поправкой на давность последнего heartbeat.

    Вычисляется при чтении, а не хранится: единственным источником истины
    остаётся last_heartbeat_at, и порог живёт в одном месте. Фоновый сторож,
    порождающий событие безопасности о пропаже, появится в плане 1C.
    """
    if agent.status in OPERATOR_CONTROLLED:
        return str(agent.status.value)

    if agent.last_heartbeat_at is None:
        return str(AgentStatus.PENDING.value)

    deadline = timedelta(seconds=interval_seconds * OFFLINE_MISSED_INTERVALS)
    if now - agent.last_heartbeat_at > deadline:
        return str(AgentStatus.OFFLINE.value)

    return str(agent.status.value)
