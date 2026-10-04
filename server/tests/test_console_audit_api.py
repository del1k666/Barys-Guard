"""Просмотр журнала аудита из консоли."""

from barysguard.db.models.user import UserRole
from barysguard.services.audit import record_audit
from tests.helpers import login_as


async def test_admin_reads_the_whole_journal(app_client, session) -> None:
    await login_as(app_client, session, username="audit-admin", role=UserRole.ADMIN)

    await record_audit(
        session,
        user_id=None,
        action="test.event",
        target_type="agent",
        target_id=None,
        payload={"note": "чужое действие"},
    )
    await session.commit()

    response = await app_client.get("/api/v1/audit")

    assert response.status_code == 200, response.text
    actions = [row["action"] for row in response.json()["items"]]
    assert "test.event" in actions
    # Вход администратора сам является событием журнала.
    assert "auth.login.success" in actions


async def test_operator_sees_only_its_own_actions(app_client, session) -> None:
    await login_as(app_client, session, username="audit-operator")

    await record_audit(
        session,
        user_id=None,
        action="somebody.else",
        target_type="agent",
        target_id=None,
        payload={},
    )
    await session.commit()

    rows = (await app_client.get("/api/v1/audit")).json()["items"]

    # Кто читает журнал целиком — тот контролирует контролирующих.
    # Это право администратора, а не любого оператора.
    assert {row["action"] for row in rows} == {"auth.login.success"}


async def test_journal_is_filtered_by_action(app_client, session) -> None:
    await login_as(app_client, session, username="audit-filter", role=UserRole.ADMIN)
    await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 1})

    rows = (await app_client.get("/api/v1/audit?action=enrollment_token.create")).json()["items"]

    assert len(rows) == 1
    assert rows[0]["action"] == "enrollment_token.create"


async def test_journal_pages_backwards_by_cursor(app_client, session) -> None:
    await login_as(app_client, session, username="audit-pager", role=UserRole.ADMIN)
    for _ in range(3):
        await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 1})

    first = (await app_client.get("/api/v1/audit?limit=2")).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None

    second = (await app_client.get(f"/api/v1/audit?limit=2&cursor={first['next_cursor']}")).json()

    # Страницы не пересекаются и идут от новых к старым.
    assert first["items"][0]["seq"] > second["items"][0]["seq"]
    assert {row["id"] for row in first["items"]}.isdisjoint({row["id"] for row in second["items"]})


async def test_chain_integrity_is_verified(app_client, session) -> None:
    await login_as(app_client, session, username="audit-verifier", role=UserRole.ADMIN)
    await app_client.post("/api/v1/enrollment-tokens", json={"max_uses": 1})

    response = await app_client.get("/api/v1/audit/verify")

    assert response.status_code == 200, response.text
    assert response.json() == {"intact": True, "broken_seq": None}


async def test_operator_cannot_verify_the_chain(app_client, session) -> None:
    await login_as(app_client, session, username="audit-nonverifier")

    assert (await app_client.get("/api/v1/audit/verify")).status_code == 403
