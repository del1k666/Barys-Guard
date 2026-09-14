import uuid
from datetime import UTC, datetime, timedelta

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.services.presence import OFFLINE_MISSED_INTERVALS, derive_status


def _agent(**overrides) -> Agent:
    agent = Agent(
        machine_id=f"machine-{uuid.uuid4().hex}",
        hostname="ws-1",
        os="linux",
        os_version="24.04",
        arch="amd64",
        agent_version="0.1.0",
        status=AgentStatus.ACTIVE,
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


def test_agent_without_heartbeat_is_pending():
    # Зарегистрировался, но ещё не выходил на связь — это не то же самое,
    # что пропавший.
    now = datetime.now(UTC)

    assert derive_status(_agent(last_heartbeat_at=None), 30, now) == "pending"


def test_recent_heartbeat_keeps_active():
    now = datetime.now(UTC)
    agent = _agent(last_heartbeat_at=now - timedelta(seconds=10))

    assert derive_status(agent, 30, now) == "active"


def test_missed_intervals_make_agent_offline():
    now = datetime.now(UTC)
    gap = timedelta(seconds=30 * OFFLINE_MISSED_INTERVALS + 1)
    agent = _agent(last_heartbeat_at=now - gap)

    assert derive_status(agent, 30, now) == "offline"


def test_quarantine_survives_silence():
    # Карантин снимает только оператор: молчание его не отменяет.
    now = datetime.now(UTC)
    agent = _agent(status=AgentStatus.QUARANTINED, last_heartbeat_at=now - timedelta(days=1))

    assert derive_status(agent, 30, now) == "quarantined"


def test_revoked_survives_silence():
    now = datetime.now(UTC)
    agent = _agent(status=AgentStatus.REVOKED, last_heartbeat_at=now - timedelta(days=1))

    assert derive_status(agent, 30, now) == "revoked"
