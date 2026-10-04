"""Управление операторами консоли."""

from sqlalchemy import select

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.user import User, UserRole
from barysguard.services.auth import create_session, find_valid_session
from tests.helpers import CONSOLE_PASSWORD, create_operator, login_as

ADMIN = "console-admin"


async def test_admin_lists_operators(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)
    await create_operator(session, username="ivanov", password=CONSOLE_PASSWORD)
    await session.commit()

    response = await app_client.get("/api/v1/users")

    assert response.status_code == 200, response.text
    rows = response.json()
    names = {row["username"] for row in rows}
    assert {"ivanov", ADMIN} <= names

    # Наружу отдаётся только факт наличия секрета, но не он сам.
    assert all({"password_hash", "api_key_sha256"}.isdisjoint(row) for row in rows)
    assert all({"has_password", "has_api_key"} <= set(row) for row in rows)


async def test_operator_cannot_list_operators(app_client, session) -> None:
    await login_as(app_client, session, username="plain")

    assert (await app_client.get("/api/v1/users")).status_code == 403


async def test_created_operator_gets_a_one_time_password(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)

    response = await app_client.post(
        "/api/v1/users", json={"username": "petrov", "role": "operator"}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["password"]) >= 12
    assert body["must_change_password"] is True

    # Выданный пароль обязан работать ровно один раз — до смены.
    login = await app_client.post(
        "/api/v1/auth/login", json={"username": "petrov", "password": body["password"]}
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


async def test_created_operator_scope_must_exist(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)

    response = await app_client.post(
        "/api/v1/users",
        json={
            "username": "ghost",
            "role": "operator",
            "scope_group_id": "11111111-1111-1111-1111-111111111111",
        },
    )

    assert response.status_code == 404


async def test_duplicate_username_is_refused(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)
    await create_operator(session, username="ivanov")
    await session.commit()

    response = await app_client.post(
        "/api/v1/users", json={"username": "ivanov", "role": "operator"}
    )

    assert response.status_code == 409


async def test_role_and_scope_are_changed(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)
    branch = AgentGroup(name="Филиал Астана")
    session.add(branch)
    target = await create_operator(session, username="ivanov")
    await session.commit()

    response = await app_client.patch(
        f"/api/v1/users/{target.id}",
        json={"role": "admin", "scope_group_id": str(branch.id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == "admin"
    assert body["scope_group_id"] == str(branch.id)


async def test_deactivation_ends_active_sessions(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)
    target = await create_operator(session, username="fired", password=CONSOLE_PASSWORD)
    raw, _ = await create_session(
        session, user_id=target.id, ip=None, user_agent=None, ttl_minutes=720
    )
    await session.commit()

    response = await app_client.patch(f"/api/v1/users/{target.id}", json={"is_active": False})

    assert response.status_code == 200, response.text
    # Увольнение, оставляющее открытую вкладку рабочей, не является увольнением.
    session.expire_all()
    assert await find_valid_session(session, raw, idle_minutes=30) is None


async def test_password_reset_issues_a_new_one_and_ends_sessions(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)
    target = await create_operator(session, username="forgot", password=CONSOLE_PASSWORD)
    raw, _ = await create_session(
        session, user_id=target.id, ip=None, user_agent=None, ttl_minutes=720
    )
    await session.commit()

    response = await app_client.post(f"/api/v1/users/{target.id}/reset-password")

    assert response.status_code == 200, response.text
    new_password = response.json()["password"]
    assert len(new_password) >= 12

    session.expire_all()
    assert await find_valid_session(session, raw, idle_minutes=30) is None

    fresh = await app_client.post(
        "/api/v1/auth/login", json={"username": "forgot", "password": new_password}
    )
    assert fresh.status_code == 200
    assert fresh.json()["must_change_password"] is True


async def test_api_key_is_issued_for_a_machine_account(app_client, session) -> None:
    await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)
    robot = await create_operator(session, username="robot")
    await session.commit()

    response = await app_client.post(f"/api/v1/users/{robot.id}/api-key")

    assert response.status_code == 200, response.text
    key = response.json()["api_key"]

    # Ключ работает сразу и не требует пароля: машине неоткуда его вводить.
    tokens = await app_client.get("/api/v1/enrollment-tokens", headers={"X-Api-Key": key})
    assert tokens.status_code == 200


async def test_admin_cannot_deactivate_itself(app_client, session) -> None:
    admin = await login_as(app_client, session, username=ADMIN, role=UserRole.ADMIN)
    admin_id = admin.id

    response = await app_client.patch(f"/api/v1/users/{admin_id}", json={"is_active": False})

    # Иначе последний администратор запирает систему одним неверным кликом.
    assert response.status_code == 409

    # Идентификатор снят заранее: после сброса кеша обращение к атрибуту
    # объекта потребовало бы похода в базу вне ожидания.
    session.expire_all()
    stored = (await session.execute(select(User).where(User.id == admin_id))).scalar_one()
    assert stored.is_active is True
