import pytest

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.config import AgentConfig, ConfigScope
from tests.helpers import enroll_agent


@pytest.mark.asyncio
async def test_config_returns_document_and_version(app_client, session):
    agent = await enroll_agent(app_client, session, "config-basic")

    response = await app_client.get("/gateway/v1/config", headers=agent.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["transport"]["heartbeat_interval_seconds"] == 30
    assert body["version"] > 0
    assert response.headers["ETag"] == f'"{body["version"]}"'


@pytest.mark.asyncio
async def test_matching_etag_returns_304(app_client, session):
    agent = await enroll_agent(app_client, session, "config-etag")

    first = await app_client.get("/gateway/v1/config", headers=agent.headers)
    etag = first.headers["ETag"]

    second = await app_client.get(
        "/gateway/v1/config", headers={**agent.headers, "If-None-Match": etag}
    )

    assert second.status_code == 304
    assert second.content == b""


@pytest.mark.asyncio
async def test_group_override_reaches_its_agent_only(app_client, session):
    group = AgentGroup(name="sales")
    session.add(group)
    await session.flush()
    session.add(
        AgentConfig(
            scope=ConfigScope.GROUP,
            group_id=group.id,
            document={"logging": {"level": "debug"}},
        )
    )
    await session.commit()

    inside = await enroll_agent(app_client, session, "config-inside", group_id=group.id)
    outside = await enroll_agent(app_client, session, "config-outside")

    mine = await app_client.get("/gateway/v1/config", headers=inside.headers)
    theirs = await app_client.get("/gateway/v1/config", headers=outside.headers)

    assert mine.json()["document"]["logging"]["level"] == "debug"
    assert theirs.json()["document"]["logging"]["level"] == "info"


@pytest.mark.asyncio
async def test_config_requires_client_certificate(app_client):
    response = await app_client.get("/gateway/v1/config")

    assert response.status_code == 403
