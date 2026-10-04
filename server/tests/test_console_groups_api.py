"""Дерево групп агентов."""

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.user import UserRole
from tests.helpers import enroll_agent, login_as


async def _group(session, name: str, parent: AgentGroup | None = None) -> AgentGroup:
    group = AgentGroup(name=name, parent_id=parent.id if parent else None)
    session.add(group)
    await session.flush()
    return group


async def test_groups_are_listed_with_agent_counts(app_client, session) -> None:
    await login_as(app_client, session, username="group-admin", role=UserRole.ADMIN)
    branch = await _group(session, "Филиал Астана")
    await _group(session, "Бухгалтерия", branch)
    await session.commit()

    await enroll_agent(app_client, session, "machine-in-branch", group_id=branch.id)
    await session.commit()

    response = await app_client.get("/api/v1/groups")

    assert response.status_code == 200, response.text
    rows = {row["name"]: row for row in response.json()}
    assert rows["Филиал Астана"]["agent_count"] == 1
    assert rows["Бухгалтерия"]["parent_id"] == str(branch.id)


async def test_admin_creates_a_group(app_client, session) -> None:
    await login_as(app_client, session, username="group-creator", role=UserRole.ADMIN)

    response = await app_client.post("/api/v1/groups", json={"name": "Филиал Алматы"})

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Филиал Алматы"


async def test_operator_cannot_create_a_group(app_client, session) -> None:
    await login_as(app_client, session, username="group-operator")

    response = await app_client.post("/api/v1/groups", json={"name": "Самовольный филиал"})

    assert response.status_code == 403


async def test_group_is_renamed_and_moved(app_client, session) -> None:
    await login_as(app_client, session, username="group-mover", role=UserRole.ADMIN)
    head = await _group(session, "Головной офис")
    branch = await _group(session, "Филиал")
    await session.commit()

    response = await app_client.patch(
        f"/api/v1/groups/{branch.id}",
        json={"name": "Филиал Астана", "parent_id": str(head.id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Филиал Астана"
    assert body["parent_id"] == str(head.id)


async def test_group_cannot_become_its_own_descendant(app_client, session) -> None:
    await login_as(app_client, session, username="cycle-maker", role=UserRole.ADMIN)
    head = await _group(session, "Головной офис")
    branch = await _group(session, "Филиал", head)
    await session.commit()

    # Цикл в дереве групп навсегда зациклил бы обход области видимости
    # и сбор эффективной конфигурации.
    response = await app_client.patch(
        f"/api/v1/groups/{head.id}", json={"parent_id": str(branch.id)}
    )

    assert response.status_code == 409


async def test_group_cannot_be_its_own_parent(app_client, session) -> None:
    await login_as(app_client, session, username="self-parent", role=UserRole.ADMIN)
    group = await _group(session, "Филиал")
    await session.commit()

    response = await app_client.patch(
        f"/api/v1/groups/{group.id}", json={"parent_id": str(group.id)}
    )

    assert response.status_code == 409


async def test_empty_group_is_deleted(app_client, session) -> None:
    await login_as(app_client, session, username="group-deleter", role=UserRole.ADMIN)
    group = await _group(session, "Пустая группа")
    group_id = group.id
    await session.commit()

    response = await app_client.delete(f"/api/v1/groups/{group_id}")

    assert response.status_code == 204, response.text

    # Удаление шло по соединению приложения, а эта сессия держит объект
    # в своей карте идентичности: без сброса get вернул бы его из памяти.
    # Идентификатор снят заранее — после сброса обращение к атрибуту
    # потребовало бы похода в базу там, где его никто не ждёт.
    session.expire_all()
    assert await session.get(AgentGroup, group_id) is None


async def test_group_with_agents_is_not_deleted(app_client, session) -> None:
    await login_as(app_client, session, username="group-keeper", role=UserRole.ADMIN)
    group = await _group(session, "Живая группа")
    await session.commit()

    await enroll_agent(app_client, session, "machine-attached", group_id=group.id)
    await session.commit()

    response = await app_client.delete(f"/api/v1/groups/{group.id}")

    # Удаление вместе с агентами оставило бы хосты без политик и без владельца.
    assert response.status_code == 409


async def test_group_with_children_is_not_deleted(app_client, session) -> None:
    await login_as(app_client, session, username="parent-keeper", role=UserRole.ADMIN)
    parent = await _group(session, "Родитель")
    await _group(session, "Потомок", parent)
    await session.commit()

    response = await app_client.delete(f"/api/v1/groups/{parent.id}")

    assert response.status_code == 409


async def test_group_of_a_scoped_operator_is_not_deleted(app_client, session) -> None:
    branch = await _group(session, "Филиал с офицером")
    await session.commit()
    await login_as(app_client, session, username="scope-holder-admin", role=UserRole.ADMIN)
    await login_as(
        app_client,
        session,
        username="branch-officer",
        scope_group_id=branch.id,
    )
    # Возвращаемся администратором: удаление доступно только ему.
    await app_client.post(
        "/api/v1/auth/login",
        json={"username": "scope-holder-admin", "password": "correct horse battery staple"},
    )

    response = await app_client.delete(f"/api/v1/groups/{branch.id}")

    # Иначе офицер филиала превратился бы в оператора без ограничения области.
    assert response.status_code == 409


async def test_scoped_operator_sees_only_its_subtree_of_groups(app_client, session) -> None:
    head = await _group(session, "Головной офис")
    branch = await _group(session, "Филиал Астана", head)
    await _group(session, "Бухгалтерия", branch)
    await _group(session, "Филиал Алматы", head)
    await session.commit()

    await login_as(app_client, session, username="astana-groups", scope_group_id=branch.id)

    rows = (await app_client.get("/api/v1/groups")).json()

    assert {row["name"] for row in rows} == {"Филиал Астана", "Бухгалтерия"}
