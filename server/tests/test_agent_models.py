import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from barysguard.db.models.agent import Agent, AgentGroup, AgentStatus


@pytest.mark.asyncio
async def test_agent_is_created_with_defaults(session):
    group = AgentGroup(name="Бухгалтерия")
    session.add(group)
    await session.flush()

    agent = Agent(
        machine_id="4C4C4544-0043-5A10-8046-B7C04F335931",
        hostname="ACC-PC-01",
        os="windows",
        os_version="10.0.26100",
        arch="amd64",
        agent_version="0.1.0",
        group_id=group.id,
    )
    session.add(agent)
    await session.flush()

    assert agent.id is not None
    assert agent.status == AgentStatus.PENDING
    assert agent.config_version == 0
    assert agent.clock_skew_ms == 0
    assert agent.last_heartbeat_at is None
    assert agent.tags == {}


@pytest.mark.asyncio
async def test_machine_id_is_unique(session):
    for _ in range(2):
        session.add(
            Agent(
                machine_id="DUPLICATE-MACHINE-ID",
                hostname="host",
                os="linux",
                os_version="6.8.0",
                arch="amd64",
                agent_version="0.1.0",
            )
        )

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_groups_form_a_tree(session):
    root = AgentGroup(name="Организация")
    session.add(root)
    await session.flush()

    child = AgentGroup(name="Филиал Астана", parent_id=root.id)
    session.add(child)
    await session.flush()

    found = await session.execute(select(AgentGroup).where(AgentGroup.parent_id == root.id))
    assert found.scalar_one().name == "Филиал Астана"
