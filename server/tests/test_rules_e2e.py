"""Правки правил из API действуют на следующий проверяемый файл."""

from sqlalchemy import select

from barysguard.core.config import get_settings
from barysguard.db.models.inspection import ArtifactScan
from barysguard.db.models.user import UserRole
from barysguard.db.session import create_engine_from_url, session_factory
from barysguard.services.inspection.rules import seed_rules
from barysguard.services.inspection.worker import run_once
from barysguard.storage.artifact_store import build_store
from tests.helpers import enroll_agent, login_as
from tests.test_inspection_e2e import _event, _upload, artifact_env  # noqa: F401

CONTRACT = "Договор №1234-567 подписан директором. Строго конфиденциально.".encode()
CARD_ONLY = "Оплата картой 4111 1111 1111 1111 и больше ничего".encode()


async def _run_worker(database_url: str) -> int:
    settings = get_settings()
    engine = create_engine_from_url(database_url)
    try:
        return await run_once(session_factory(engine), build_store(settings), settings)
    finally:
        await engine.dispose()


async def test_custom_regex_rule_flags_a_file_and_a_disabled_builtin_does_not(
    app_client,
    session,
    migrated_database_url,
    artifact_env,  # noqa: F811
) -> None:
    await login_as(app_client, session, username="rules-e2e", role=UserRole.ADMIN)
    await seed_rules(session)
    await session.commit()
    agent = await enroll_agent(app_client, session, "rules-e2e-agent")

    created = await app_client.post(
        "/api/v1/rules",
        json={
            "kind": "regex",
            "title": "Номер договора",
            "weight": 30,
            "cap": 2,
            "pattern": r"№\s?\d{4}-\d{3}",
            "test_text": "Договор №1234-567",
        },
    )
    assert created.status_code == 201, created.text

    await _event(app_client, agent, CONTRACT, "E:\\contract.txt")
    await _upload(app_client, agent, CONTRACT)
    assert await _run_worker(migrated_database_url) == 1

    incidents = (await app_client.get("/api/v1/incidents")).json()["items"]
    assert len(incidents) == 1
    assert "Номер договора ×1" in incidents[0]["title"]
    detail = (await app_client.get(f"/api/v1/incidents/{incidents[0]['id']}")).json()
    assert any(m["rule_title"] == "Номер договора" for m in detail["matches"])
    assert "1234-567" not in str(detail)  # образец замаскирован

    rules = (await app_client.get("/api/v1/rules")).json()
    cards = next(r for r in rules if r["key"] == "card")
    off = await app_client.patch(f"/api/v1/rules/{cards['id']}", json={"enabled": False})
    assert off.status_code == 200, off.text

    await _event(app_client, agent, CARD_ONLY, "E:\\card.txt")
    await _upload(app_client, agent, CARD_ONLY)
    assert await _run_worker(migrated_database_url) == 1

    events = (await app_client.get("/api/v1/events", params={"channel": "file"})).json()["items"]
    verdicts = {e["subject"]["dst_path"]: e["verdict"] for e in events}
    assert verdicts["E:\\card.txt"]["status"] == "clean"  # встроенное правило отключено

    # Набор правил изменился — тот же файл сканируется заново по новому хешу набора.
    await _event(app_client, agent, CONTRACT, "E:\\contract-again.txt")
    assert await _run_worker(migrated_database_url) == 1
    scans = (await session.scalars(select(ArtifactScan))).all()
    assert len(scans) == 3  # contract (набор 1), card (набор 2), contract (набор 2)
    assert len({s.ruleset_hash for s in scans}) == 2
