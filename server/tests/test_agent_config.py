import uuid

import pytest

from barysguard.core.errors import ConfigTreeError
from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.config import AgentConfig, ConfigScope
from barysguard.services.config import (
    AgentConfigDocument,
    compute_config_version,
    effective_config_for_agent,
    effective_document,
    global_heartbeat_interval,
    merge_documents,
)


def test_defaults_match_specification():
    document = AgentConfigDocument().model_dump(mode="json")

    assert document["transport"]["heartbeat_interval_seconds"] == 30
    assert document["transport"]["event_batch_max"] == 500
    assert document["buffer"]["max_bytes"] == 500 * 1024 * 1024
    assert document["buffer"]["max_age_days"] == 7
    assert document["policies"] == {}


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError):
        AgentConfigDocument.model_validate({"transport": {"heartbeat_intervall": 30}})


def test_merge_is_recursive_for_dictionaries():
    base = {"transport": {"a": 1, "b": 2}, "logging": {"level": "info"}}
    overlay = {"transport": {"b": 3}}

    assert merge_documents(base, overlay) == {
        "transport": {"a": 1, "b": 3},
        "logging": {"level": "info"},
    }


def test_merge_replaces_lists_entirely():
    # Дополнение списков сделало бы невыразимым снятие унаследованного пути.
    base = {"policies": {"excluded": ["/srv/docs", "/srv/reports"]}}
    overlay = {"policies": {"excluded": ["/srv/docs"]}}

    assert merge_documents(base, overlay)["policies"]["excluded"] == ["/srv/docs"]


def test_version_ignores_key_order():
    first = compute_config_version({"a": 1, "b": {"c": 2, "d": 3}})
    second = compute_config_version({"b": {"d": 3, "c": 2}, "a": 1})

    assert first == second


def test_version_is_positive_int32():
    version = compute_config_version(AgentConfigDocument().model_dump(mode="json"))

    assert 0 <= version <= 0x7FFFFFFF


def test_version_changes_with_document():
    base = AgentConfigDocument().model_dump(mode="json")
    changed = merge_documents(base, {"transport": {"heartbeat_interval_seconds": 15}})

    assert compute_config_version(base) != compute_config_version(changed)


@pytest.mark.asyncio
async def test_effective_document_without_rows_returns_defaults(session):
    document = await effective_document(session, None)

    assert document == AgentConfigDocument().model_dump(mode="json")


@pytest.mark.asyncio
async def test_group_chain_merges_from_root_to_leaf(session):
    root = AgentGroup(name="root")
    session.add(root)
    await session.flush()

    middle = AgentGroup(name="middle", parent_id=root.id)
    session.add(middle)
    await session.flush()

    leaf = AgentGroup(name="leaf", parent_id=middle.id)
    session.add(leaf)
    await session.flush()

    session.add_all(
        [
            AgentConfig(scope=ConfigScope.GLOBAL, document={"logging": {"level": "info"}}),
            AgentConfig(
                scope=ConfigScope.GROUP,
                group_id=root.id,
                document={"transport": {"heartbeat_interval_seconds": 60}},
            ),
            AgentConfig(
                scope=ConfigScope.GROUP,
                group_id=leaf.id,
                document={"logging": {"level": "debug"}},
            ),
        ]
    )
    await session.flush()

    document = await effective_document(session, leaf.id)

    # Значение корня наследуется, значение листа перекрывает глобальное.
    assert document["transport"]["heartbeat_interval_seconds"] == 60
    assert document["logging"]["level"] == "debug"


@pytest.mark.asyncio
async def test_cycle_in_group_tree_is_rejected(session):
    first = AgentGroup(name="first")
    second = AgentGroup(name="second")
    session.add_all([first, second])
    await session.flush()

    first.parent_id = second.id
    second.parent_id = first.id
    await session.flush()

    with pytest.raises(ConfigTreeError):
        await effective_document(session, first.id)


@pytest.mark.asyncio
async def test_effective_config_for_agent_uses_its_group(session):
    group = AgentGroup(name="sales")
    session.add(group)
    await session.flush()

    session.add(
        AgentConfig(
            scope=ConfigScope.GROUP,
            group_id=group.id,
            document={"transport": {"heartbeat_interval_seconds": 5}},
        )
    )
    agent = Agent(
        machine_id=f"machine-{uuid.uuid4().hex}",
        hostname="ws-1",
        os="windows",
        os_version="11",
        arch="amd64",
        agent_version="0.1.0",
        group_id=group.id,
    )
    session.add(agent)
    await session.flush()

    document, version = await effective_config_for_agent(session, agent)

    assert document["transport"]["heartbeat_interval_seconds"] == 5
    assert version == compute_config_version(document)


@pytest.mark.asyncio
async def test_global_heartbeat_interval_reads_global_row(session):
    session.add(
        AgentConfig(
            scope=ConfigScope.GLOBAL,
            document={"transport": {"heartbeat_interval_seconds": 45}},
        )
    )
    await session.flush()

    assert await global_heartbeat_interval(session) == 45
