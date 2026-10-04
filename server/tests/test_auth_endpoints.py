from barysguard.db.models.user import UserRole
from tests.helpers import create_operator

PASSWORD = "correct horse battery staple"


async def test_login_returns_the_operator_and_sets_a_session_cookie(app_client, session) -> None:
    user = await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    response = await app_client.post(
        "/api/v1/auth/login", json={"username": "ivanov", "password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "ivanov"
    assert body["role"] == UserRole.OPERATOR.value
    assert body["id"] == str(user.id)

    cookie = response.cookies.get("bg_session")
    assert cookie is not None and len(cookie) > 20

    # Cookie обязана быть недоступна скриптам: украденная через XSS сессия
    # в системе, читающей перехват, означает доступ ко всему перехвату.
    # Регистр атрибутов cookie по RFC 6265 не значим, поэтому сравнение
    # приведённое: Starlette пишет samesite=strict строчными.
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=strict" in header


async def test_me_returns_the_logged_in_operator(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "ivanov", "password": PASSWORD})
    response = await app_client.get("/api/v1/auth/me")

    assert response.status_code == 200, response.text
    assert response.json()["username"] == "ivanov"


async def test_me_without_a_session_is_unauthorized(app_client) -> None:
    assert (await app_client.get("/api/v1/auth/me")).status_code == 401


async def test_wrong_password_is_rejected_without_a_cookie(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    response = await app_client.post(
        "/api/v1/auth/login", json={"username": "ivanov", "password": "мимо"}
    )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def test_logout_ends_the_session(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "ivanov", "password": PASSWORD})
    assert (await app_client.post("/api/v1/auth/logout")).status_code == 204

    assert (await app_client.get("/api/v1/auth/me")).status_code == 401


async def test_session_cookie_grants_access_to_the_operator_api(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "ivanov", "password": PASSWORD})

    assert (await app_client.get("/api/v1/agents")).status_code == 200


async def test_state_changing_request_from_a_foreign_origin_is_refused(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "ivanov", "password": PASSWORD})

    response = await app_client.post(
        "/api/v1/enrollment-tokens",
        json={"max_uses": 1},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403


async def test_reading_from_a_foreign_origin_is_allowed(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "ivanov", "password": PASSWORD})

    response = await app_client.get("/api/v1/agents", headers={"Origin": "https://evil.example"})

    # Чтение чужой вкладке всё равно не достанется: ответ ей не покажут
    # без CORS-заголовков, а мы их не выдаём.
    assert response.status_code == 200


async def test_own_origin_is_accepted(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "ivanov", "password": PASSWORD})

    response = await app_client.post(
        "/api/v1/enrollment-tokens", json={"max_uses": 1}, headers={"Origin": "http://test"}
    )

    assert response.status_code == 201, response.text
