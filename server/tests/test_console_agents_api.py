"""Список и карточка агента для веб-консоли."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from barysguard.db.models.agent import Agent, AgentGroup, AgentStatus
from barysguard.db.models.certificate import AgentCertificate
from barysguard.db.models.user import UserRole
from tests.helpers import enroll_agent, login_as


async def _group(session, name: str, parent: AgentGroup | None = None) -> AgentGroup:
    group = AgentGroup(name=name, parent_id=parent.id if parent else None)
    session.add(group)
    await session.flush()
    return group


async def test_agent_list_comes_with_a_total_for_pagination(app_client, session) -> None:
    await login_as(app_client, session, username="lister", role=UserRole.ADMIN)
    for index in range(3):
        await enroll_agent(app_client, session, f"machine-{index}")
    await session.commit()

    response = await app_client.get("/api/v1/agents?limit=2")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 2
    # Без общего числа консоль не может нарисовать постраничную навигацию,
    # а длина страницы про это ничего не говорит.
    assert body["total"] == 3


async def test_agents_are_searched_by_hostname(app_client, session) -> None:
    await login_as(app_client, session, username="searcher", role=UserRole.ADMIN)
    first = await enroll_agent(app_client, session, "machine-buh")
    await enroll_agent(app_client, session, "machine-other")

    agent = await session.get(Agent, first.agent_id)
    agent.hostname = "BUH-PC-07"
    await session.commit()

    response = await app_client.get("/api/v1/agents?q=buh-pc")

    items = response.json()["items"]
    assert [item["hostname"] for item in items] == ["BUH-PC-07"]


async def test_agents_are_filtered_by_status(app_client, session) -> None:
    await login_as(app_client, session, username="filterer", role=UserRole.ADMIN)
    silent = await enroll_agent(app_client, session, "machine-silent")
    await enroll_agent(app_client, session, "machine-fresh")

    agent = await session.get(Agent, silent.agent_id)
    agent.status = AgentStatus.ACTIVE
    agent.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=200)
    await session.commit()

    response = await app_client.get("/api/v1/agents?status=offline")

    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(silent.agent_id)]


async def test_agents_are_filtered_by_group(app_client, session) -> None:
    await login_as(app_client, session, username="grouper", role=UserRole.ADMIN)
    branch = await _group(session, "Филиал")
    await session.commit()

    inside = await enroll_agent(app_client, session, "machine-inside", group_id=branch.id)
    await enroll_agent(app_client, session, "machine-outside")
    await session.commit()

    response = await app_client.get(f"/api/v1/agents?group_id={branch.id}")

    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(inside.agent_id)]


async def test_agent_card_shows_host_facts_and_active_certificate(app_client, session) -> None:
    await login_as(app_client, session, username="viewer", role=UserRole.ADMIN)
    enrolled = await enroll_agent(app_client, session, "machine-card")
    await session.commit()

    response = await app_client.get(f"/api/v1/agents/{enrolled.agent_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["machine_id"] == "machine-card"
    assert body["os_version"] == "6.8.0"
    assert body["arch"] == "amd64"
    assert body["certificate"]["serial"] == enrolled.serial
    assert body["certificate"]["not_after"] is not None


async def test_unknown_agent_card_is_not_found(app_client, session) -> None:
    await login_as(app_client, session, username="seeker", role=UserRole.ADMIN)

    response = await app_client.get("/api/v1/agents/11111111-1111-1111-1111-111111111111")

    assert response.status_code == 404


async def test_agent_moves_to_another_group(app_client, session) -> None:
    await login_as(app_client, session, username="mover", role=UserRole.ADMIN)
    branch = await _group(session, "Филиал Астана")
    await session.commit()

    enrolled = await enroll_agent(app_client, session, "machine-move")
    await session.commit()

    response = await app_client.patch(
        f"/api/v1/agents/{enrolled.agent_id}", json={"group_id": str(branch.id)}
    )

    assert response.status_code == 200, response.text
    assert response.json()["group_id"] == str(branch.id)

    agent = await session.get(Agent, enrolled.agent_id)
    await session.refresh(agent)
    assert agent.group_id == branch.id


async def test_agent_cannot_be_moved_outside_the_operator_scope(app_client, session) -> None:
    branch = await _group(session, "Филиал Астана")
    other = await _group(session, "Филиал Алматы")
    await session.commit()

    await login_as(app_client, session, username="astana-mover", scope_group_id=branch.id)
    enrolled = await enroll_agent(app_client, session, "machine-scoped", group_id=branch.id)
    await session.commit()

    response = await app_client.patch(
        f"/api/v1/agents/{enrolled.agent_id}", json={"group_id": str(other.id)}
    )

    # Иначе оператор филиала переносит хост в чужой филиал и вместе с ним
    # отдаёт туда весь дальнейший перехват.
    assert response.status_code == 404


async def test_certificate_history_is_listed(app_client, session) -> None:
    await login_as(app_client, session, username="pki-viewer", role=UserRole.ADMIN)
    enrolled = await enroll_agent(app_client, session, "machine-certs")
    await session.commit()

    response = await app_client.get(f"/api/v1/agents/{enrolled.agent_id}/certificates")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["serial"] == enrolled.serial
    assert rows[0]["revoked_at"] is None


async def test_revoking_a_certificate_stops_the_agent(app_client, session) -> None:
    await login_as(app_client, session, username="revoker", role=UserRole.ADMIN)
    enrolled = await enroll_agent(app_client, session, "machine-revoke")
    await session.commit()

    response = await app_client.post(
        f"/api/v1/agents/{enrolled.agent_id}/revoke", json={"reason": "увольнение сотрудника"}
    )

    assert response.status_code == 204, response.text

    certificate = (
        await session.execute(
            select(AgentCertificate).where(AgentCertificate.serial == enrolled.serial)
        )
    ).scalar_one()
    await session.refresh(certificate)
    assert certificate.revoked_at is not None

    # Отзыв обязан немедленно прекращать обслуживание, а не только
    # помечать строку в базе.
    heartbeat = await app_client.post(
        "/gateway/v1/heartbeat",
        json={"status": "active", "config_version": 0, "sent_at": datetime.now(UTC).isoformat()},
        headers=enrolled.headers,
    )
    assert heartbeat.status_code == 403


async def test_operator_cannot_revoke_a_certificate(app_client, session) -> None:
    await login_as(app_client, session, username="plain-operator")
    enrolled = await enroll_agent(app_client, session, "machine-keep")
    await session.commit()

    response = await app_client.post(
        f"/api/v1/agents/{enrolled.agent_id}/revoke", json={"reason": "просто так"}
    )

    assert response.status_code == 403
