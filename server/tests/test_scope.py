"""Область видимости оператора: поддерево групп, а не одна группа."""

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.user import UserRole
from barysguard.services.scope import scope_group_ids
from tests.helpers import create_operator, enroll_agent

PASSWORD = "correct horse battery staple"


async def _group(session, name: str, parent: AgentGroup | None = None) -> AgentGroup:
    group = AgentGroup(name=name, parent_id=parent.id if parent else None)
    session.add(group)
    await session.flush()
    return group


async def test_operator_without_scope_sees_the_whole_fleet(session) -> None:
    user = await create_operator(session, username="admin-wide", role=UserRole.ADMIN)

    assert await scope_group_ids(session, user) is None


async def test_scope_covers_the_whole_subtree(session) -> None:
    head = await _group(session, "Головной офис")
    branch = await _group(session, "Филиал Астана", head)
    department = await _group(session, "Бухгалтерия", branch)
    other = await _group(session, "Филиал Алматы", head)

    user = await create_operator(session, username="astana", scope_group_id=branch.id)

    visible = await scope_group_ids(session, user)

    assert visible == {branch.id, department.id}
    assert head.id not in visible
    assert other.id not in visible


async def test_agent_list_is_limited_to_the_subtree(app_client, session) -> None:
    head = await _group(session, "Головной офис")
    branch = await _group(session, "Филиал Астана", head)
    department = await _group(session, "Бухгалтерия", branch)
    other = await _group(session, "Филиал Алматы", head)

    await create_operator(session, username="astana", password=PASSWORD, scope_group_id=branch.id)
    await session.commit()

    await enroll_agent(app_client, session, "machine-branch", group_id=branch.id)
    await enroll_agent(app_client, session, "machine-department", group_id=department.id)
    await enroll_agent(app_client, session, "machine-other", group_id=other.id)
    await enroll_agent(app_client, session, "machine-head", group_id=head.id)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "astana", "password": PASSWORD})
    response = await app_client.get("/api/v1/agents")

    assert response.status_code == 200, response.text
    # Дочерняя группа входит в область видимости, родительская и соседняя — нет.
    assert len(response.json()["items"]) == 2


async def test_agent_outside_the_subtree_is_not_found(app_client, session) -> None:
    head = await _group(session, "Головной офис")
    branch = await _group(session, "Филиал Астана", head)
    other = await _group(session, "Филиал Алматы", head)

    await create_operator(session, username="astana", password=PASSWORD, scope_group_id=branch.id)
    await session.commit()

    foreign = await enroll_agent(app_client, session, "machine-other", group_id=other.id)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "astana", "password": PASSWORD})

    # Не 403: различая «нет такого агента» и «есть, но не ваш», мы позволили бы
    # перебором идентификаторов пересчитать чужой филиал.
    assert (await app_client.get(f"/api/v1/agents/{foreign.agent_id}/config")).status_code == 404
    assert (
        await app_client.post(f"/api/v1/agents/{foreign.agent_id}/commands", json={"type": "ping"})
    ).status_code == 404


async def test_agent_in_a_child_group_is_reachable(app_client, session) -> None:
    branch = await _group(session, "Филиал Астана")
    department = await _group(session, "Бухгалтерия", branch)

    await create_operator(session, username="astana", password=PASSWORD, scope_group_id=branch.id)
    await session.commit()

    agent = await enroll_agent(app_client, session, "machine-department", group_id=department.id)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "astana", "password": PASSWORD})

    assert (await app_client.get(f"/api/v1/agents/{agent.agent_id}/config")).status_code == 200


async def test_ungrouped_agent_is_invisible_to_a_scoped_operator(app_client, session) -> None:
    branch = await _group(session, "Филиал Астана")
    await create_operator(session, username="astana", password=PASSWORD, scope_group_id=branch.id)
    await session.commit()

    orphan = await enroll_agent(app_client, session, "machine-orphan", group_id=None)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "astana", "password": PASSWORD})

    assert (await app_client.get("/api/v1/agents")).json()["items"] == []
    assert (await app_client.get(f"/api/v1/agents/{orphan.agent_id}/config")).status_code == 404


async def test_group_with_a_scoped_operator_cannot_be_deleted(session) -> None:
    branch = await _group(session, "Филиал Астана")
    # Имя уникально в пределах прогона: база общая, а этот тест идёт без
    # фикстуры app_client, которая очищает таблицы.
    await create_operator(session, username="astana-restrict", scope_group_id=branch.id)
    await session.flush()

    # Раньше связь была объявлена ON DELETE SET NULL, и удаление группы
    # превращало офицера филиала в оператора без ограничения области —
    # то есть открывало ему весь флот. База обязана это запретить.
    with pytest.raises(IntegrityError):
        await session.execute(delete(AgentGroup).where(AgentGroup.id == branch.id))
        await session.flush()
