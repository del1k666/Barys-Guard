from datetime import datetime, timedelta

from barysguard.db.models.agent import Agent, AgentStatus

# Один пропущенный heartbeat — сетевая икота, три подряд — уже молчание.
OFFLINE_MISSED_INTERVALS = 3

# Статусы, которые снимает только оператор. Молчание агента их не меняет.
OPERATOR_CONTROLLED = (AgentStatus.QUARANTINED, AgentStatus.REVOKED)


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
