from barysguard.services.auth import create_session, find_valid_session
from tests.helpers import create_operator

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "tamyz qorghan 2026 qala"


async def _login(app_client, username: str = "ivanov", password: str = PASSWORD):
    return await app_client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


async def test_password_changes_and_the_old_one_stops_working(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()
    await _login(app_client)

    response = await app_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204, response.text

    await app_client.post("/api/v1/auth/logout")

    assert (await _login(app_client, password=PASSWORD)).status_code == 401
    assert (await _login(app_client, password=NEW_PASSWORD)).status_code == 200


async def test_wrong_current_password_is_refused(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()
    await _login(app_client)

    response = await app_client.post(
        "/api/v1/auth/password",
        json={"current_password": "мимо", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400


async def test_short_password_is_rejected(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    await session.commit()
    await _login(app_client)

    response = await app_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "korotko"},
    )

    assert response.status_code == 422


async def test_changing_password_ends_sessions_on_other_devices(app_client, session) -> None:
    user = await create_operator(session, username="ivanov", password=PASSWORD)
    elsewhere, _ = await create_session(
        session, user_id=user.id, ip=None, user_agent="другое устройство", ttl_minutes=720
    )
    await session.commit()

    await _login(app_client)
    response = await app_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204, response.text

    # Смена пароля, оставляющая чужой вход живым, не защищает от того,
    # кто уже внутри, — а ради этого её чаще всего и делают.
    assert await find_valid_session(session, elsewhere, idle_minutes=30) is None

    # Текущая сессия переживает смену: иначе оператора выбрасывает
    # из консоли ровно в тот момент, когда он выполнил требование системы.
    assert (await app_client.get("/api/v1/auth/me")).status_code == 200


async def test_forced_password_change_clears_the_flag(app_client, session) -> None:
    user = await create_operator(session, username="ivanov", password=PASSWORD)
    user.must_change_password = True
    await session.commit()

    assert (await _login(app_client)).json()["must_change_password"] is True

    await app_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert (await app_client.get("/api/v1/auth/me")).json()["must_change_password"] is False


async def test_own_sessions_are_listed_with_the_current_one_marked(app_client, session) -> None:
    user = await create_operator(session, username="ivanov", password=PASSWORD)
    await create_session(
        session, user_id=user.id, ip=None, user_agent="другое устройство", ttl_minutes=720
    )
    await session.commit()

    await _login(app_client)
    response = await app_client.get("/api/v1/auth/sessions")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 2
    assert sum(1 for row in rows if row["current"]) == 1


async def test_revoking_another_session_ends_it(app_client, session) -> None:
    user = await create_operator(session, username="ivanov", password=PASSWORD)
    elsewhere_raw, elsewhere = await create_session(
        session, user_id=user.id, ip=None, user_agent="другое устройство", ttl_minutes=720
    )
    await session.commit()

    await _login(app_client)
    response = await app_client.delete(f"/api/v1/auth/sessions/{elsewhere.id}")

    assert response.status_code == 204, response.text
    assert await find_valid_session(session, elsewhere_raw, idle_minutes=30) is None


async def test_foreign_session_cannot_be_revoked(app_client, session) -> None:
    await create_operator(session, username="ivanov", password=PASSWORD)
    other = await create_operator(session, username="petrov", password=PASSWORD)
    other_raw, other_session = await create_session(
        session, user_id=other.id, ip=None, user_agent=None, ttl_minutes=720
    )
    await session.commit()

    await _login(app_client)
    response = await app_client.delete(f"/api/v1/auth/sessions/{other_session.id}")

    assert response.status_code == 404
    assert await find_valid_session(session, other_raw, idle_minutes=30) is not None
