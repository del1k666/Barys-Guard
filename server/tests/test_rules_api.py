"""/api/v1/rules: права, правила, термины, проверка шаблона, аудит."""

import uuid

import pytest
from sqlalchemy import select

from barysguard.db.models.audit import AuditLog
from barysguard.db.models.user import UserRole
from barysguard.services.inspection.rules import seed_rules
from tests.helpers import login_as

SAMPLE = "Договор №1234-567 подписан"


async def _admin(app_client, session, name="rules-admin"):
    await login_as(app_client, session, username=name, role=UserRole.ADMIN)
    await seed_rules(session)
    await session.commit()


async def _by_key(app_client, key: str) -> dict:
    items = (await app_client.get("/api/v1/rules")).json()
    return next(item for item in items if item["key"] == key)


async def test_every_route_is_closed_to_operators_and_anonymous(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()
    anonymous = await app_client.get("/api/v1/rules")
    assert anonymous.status_code == 401

    await login_as(app_client, session, username="rules-op")  # оператор
    rule_id = str(uuid.uuid4())
    calls = [
        ("get", "/api/v1/rules", None),
        ("post", "/api/v1/rules", {"kind": "dictionary", "title": "x", "weight": 1, "cap": 1}),
        ("get", f"/api/v1/rules/{rule_id}", None),
        ("patch", f"/api/v1/rules/{rule_id}", {"enabled": False}),
        ("get", f"/api/v1/rules/{rule_id}/versions", None),
        ("get", f"/api/v1/rules/{rule_id}/terms", None),
        ("post", f"/api/v1/rules/{rule_id}/terms", {"terms": ["a"]}),
        ("delete", f"/api/v1/rules/{rule_id}/terms/{uuid.uuid4()}", None),
        ("post", "/api/v1/rules/test", {"kind": "regex", "pattern": "a", "text": "a"}),
    ]
    for method, url, body in calls:
        response = await getattr(app_client, method)(url, **({"json": body} if body else {}))
        assert response.status_code == 403, (method, url, response.status_code)


async def test_list_has_builtin_rules_first(app_client, session) -> None:
    await _admin(app_client, session)

    items = (await app_client.get("/api/v1/rules")).json()

    assert {i["key"] for i in items} == {"iin_bin", "card", "markings"}
    assert all(i["builtin"] and i["enabled"] and i["version"] == 1 for i in items)
    markings = next(i for i in items if i["key"] == "markings")
    assert markings["kind"] == "dictionary" and markings["terms_count"] == 7


async def test_create_regex_rule_with_test_text(app_client, session) -> None:
    await _admin(app_client, session, "rules-create")

    created = await app_client.post(
        "/api/v1/rules",
        json={
            "kind": "regex",
            "title": "Номер договора",
            "weight": 30,
            "cap": 2,
            "pattern": r"№\s?\d{4}-\d{3}",
            "ignore_case": False,
            "test_text": SAMPLE,
        },
    )
    without_text = await app_client.post(
        "/api/v1/rules",
        json={"kind": "regex", "title": "x", "weight": 30, "cap": 2, "pattern": "a"},
    )
    lookahead = await app_client.post(
        "/api/v1/rules",
        json={
            "kind": "regex",
            "title": "x",
            "weight": 30,
            "cap": 2,
            "pattern": "(?=a)b",
            "test_text": "ab",
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key"].startswith("custom_") and body["builtin"] is False and body["version"] == 1
    assert without_text.status_code == 422
    assert lookahead.status_code == 422 and "Шаблон не принят" in lookahead.text


async def test_patch_weight_enabled_and_errors(app_client, session) -> None:
    await _admin(app_client, session, "rules-patch")
    card = await _by_key(app_client, "card")

    heavier = await app_client.patch(f"/api/v1/rules/{card['id']}", json={"weight": 40})
    off = await app_client.patch(f"/api/v1/rules/{card['id']}", json={"enabled": False})
    bad = await app_client.patch(f"/api/v1/rules/{card['id']}", json={"weight": 0})
    unknown = await app_client.patch(f"/api/v1/rules/{uuid.uuid4()}", json={"enabled": False})

    assert heavier.json()["version"] == 2 and heavier.json()["weight"] == 40
    assert off.json()["enabled"] is False and off.json()["version"] == 2
    assert bad.status_code == 422 and unknown.status_code == 404


async def test_last_enabled_rule_cannot_be_disabled(app_client, session) -> None:
    await _admin(app_client, session, "rules-last")
    items = (await app_client.get("/api/v1/rules")).json()
    for item in items[:-1]:
        assert (
            await app_client.patch(f"/api/v1/rules/{item['id']}", json={"enabled": False})
        ).status_code == 200

    last = await app_client.patch(f"/api/v1/rules/{items[-1]['id']}", json={"enabled": False})

    assert last.status_code == 409


async def test_terms_lifecycle(app_client, session) -> None:
    await _admin(app_client, session, "rules-terms")
    markings = await _by_key(app_client, "markings")
    base = f"/api/v1/rules/{markings['id']}"

    added = await app_client.post(f"{base}/terms", json={"terms": ["Только для своих", "секретно"]})
    page = await app_client.get(f"{base}/terms", params={"q": "своих"})
    term_id = page.json()["items"][0]["id"]
    deleted = await app_client.delete(f"{base}/terms/{term_id}")
    again = await app_client.delete(f"{base}/terms/{term_id}")
    versions = await app_client.get(f"{base}/versions")

    assert added.status_code == 200 and added.json() == {"added": 1}
    assert page.json()["total"] == 1 and page.json()["items"][0]["term"] == "Только для своих"
    assert deleted.status_code == 204 and again.status_code == 404
    assert [v["version"] for v in versions.json()] == [3, 2, 1]


async def test_terms_of_a_non_dictionary_rule_are_422(app_client, session) -> None:
    await _admin(app_client, session, "rules-terms-bad")
    card = await _by_key(app_client, "card")

    response = await app_client.get(f"/api/v1/rules/{card['id']}/terms")

    assert response.status_code == 422


async def test_test_endpoint_reports_positions_and_pattern_errors(app_client, session) -> None:
    await _admin(app_client, session, "rules-test")

    good = await app_client.post(
        "/api/v1/rules/test", json={"kind": "regex", "pattern": r"\d{3}", "text": "a 123 b 456"}
    )
    bad = await app_client.post(
        "/api/v1/rules/test", json={"kind": "regex", "pattern": "(?=a)b", "text": "x"}
    )
    words = await app_client.post(
        "/api/v1/rules/test",
        json={"kind": "dictionary", "terms": ["секретно"], "text": "Это СЕКРЕТНО"},
    )
    too_long = await app_client.post(
        "/api/v1/rules/test", json={"kind": "regex", "pattern": "a", "text": "a" * 20001}
    )

    assert good.json() == {
        "ok": True,
        "error": None,
        "count": 2,
        "matches": [{"start": 2, "end": 5}, {"start": 8, "end": 11}],
    }
    assert bad.status_code == 200 and bad.json()["ok"] is False and bad.json()["error"]
    assert words.json()["count"] == 1
    assert too_long.status_code == 422


async def test_writes_are_audited_without_test_text(app_client, session) -> None:
    await _admin(app_client, session, "rules-audit")
    created = await app_client.post(
        "/api/v1/rules",
        json={
            "kind": "regex",
            "title": "Договор",
            "weight": 30,
            "cap": 2,
            "pattern": r"№\s?\d{4}-\d{3}",
            "test_text": SAMPLE,
        },
    )
    rule_id = created.json()["id"]
    await app_client.patch(f"/api/v1/rules/{rule_id}", json={"weight": 35})

    rows = (await session.scalars(select(AuditLog).where(AuditLog.target_type == "rule"))).all()

    assert [r.action for r in rows] == ["rule.create", "rule.update"]
    assert rows[1].payload["changes"] == {"weight": [30, 35]}
    assert "Договор №1234" not in str([r.payload for r in rows])


async def test_rejected_edits_leave_the_rule_unchanged(app_client, session) -> None:
    await _admin(app_client, session, "rules-rollback")
    card = await _by_key(app_client, "card")

    bad_weight = await app_client.patch(
        f"/api/v1/rules/{card['id']}", json={"title": "Другое имя", "weight": 0}
    )
    after_422 = await app_client.get(f"/api/v1/rules/{card['id']}")

    assert bad_weight.status_code == 422
    assert after_422.json() == card

    items = (await app_client.get("/api/v1/rules")).json()
    for item in items[:-1]:
        assert (
            await app_client.patch(f"/api/v1/rules/{item['id']}", json={"enabled": False})
        ).status_code == 200
    last = items[-1]
    before = (await app_client.get(f"/api/v1/rules/{last['id']}")).json()

    conflict = await app_client.patch(
        f"/api/v1/rules/{last['id']}", json={"title": "Новое имя", "enabled": False}
    )
    after_409 = await app_client.get(f"/api/v1/rules/{last['id']}")

    assert conflict.status_code == 409
    assert after_409.json() == before
    session.expire_all()
    versions = await app_client.get(f"/api/v1/rules/{last['id']}/versions")
    assert [v["version"] for v in versions.json()] == [1]


async def test_test_text_never_reaches_error_bodies(app_client, session) -> None:
    await _admin(app_client, session, "rules-leak")

    bad = await app_client.post(
        "/api/v1/rules/test", json={"kind": "regex", "pattern": "(?=a)b", "text": SAMPLE}
    )
    rejected = await app_client.post(
        "/api/v1/rules",
        json={
            "kind": "regex",
            "title": "x",
            "weight": 1,
            "cap": 1,
            "pattern": "(?=a)b",
            "test_text": SAMPLE,
        },
    )

    assert "Договор" not in bad.text and "1234" not in bad.text
    assert "Договор" not in rejected.text and "1234" not in rejected.text


@pytest.mark.parametrize("name", ["title", "enabled", "weight", "cap", "pattern", "ignore_case"])
async def test_patch_with_explicit_null_is_422_and_changes_nothing(
    app_client, session, name
) -> None:
    await _admin(app_client, session, f"rules-null-{name}")
    card = await _by_key(app_client, "card")

    response = await app_client.patch(f"/api/v1/rules/{card['id']}", json={name: None})
    after = await app_client.get(f"/api/v1/rules/{card['id']}")

    assert response.status_code == 422
    assert after.json() == card
