"""Раздел collectors документа конфигурации агента."""

import pytest
from pydantic import ValidationError

from barysguard.services.config import AgentConfigDocument
from tests.helpers import enroll_agent


def test_defaults_match_the_spec() -> None:
    document = AgentConfigDocument().model_dump(mode="json")["collectors"]

    assert document["usb"] == {"enabled": True, "poll_seconds": 2}
    watch = document["file_watch"]
    assert watch["enabled"] is True
    assert watch["paths"] == [
        "%USERS%\\Documents",
        "%USERS%\\Desktop",
        "%USERS%\\Downloads",
    ]
    assert watch["exclude"] == ["*\\~$*", "*.tmp", "*.crdownload", "*\\AppData\\*"]
    assert (watch["stable_ms"], watch["max_wait_ms"]) == (1500, 30000)
    assert watch["max_hash_bytes"] == 256 * 1024 * 1024
    assert watch["max_events_per_second"] == 200


@pytest.mark.parametrize(
    "patch",
    [
        {"usb": {"poll_seconds": 0}},
        {"usb": {"poll_seconds": 61}},
        {"file_watch": {"stable_ms": 100}},
        {"file_watch": {"max_wait_ms": 500}},
        {"file_watch": {"max_hash_bytes": 1024}},
        {"file_watch": {"max_events_per_second": 0}},
        {"file_watch": {"paths": [""]}},
        {"file_watch": {"typo_field": 1}},
        {"unknown_collector": {}},
    ],
)
def test_out_of_range_or_unknown_values_are_rejected(patch: dict) -> None:
    with pytest.raises(ValidationError):
        AgentConfigDocument.model_validate({"collectors": patch})


async def test_agent_receives_collectors_defaults(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "collectors-defaults")

    response = await app_client.get("/gateway/v1/config", headers=agent.headers)

    assert response.status_code == 200
    document = response.json()["document"]
    assert document["collectors"]["file_watch"]["stable_ms"] == 1500
    assert document["collectors"]["usb"]["enabled"] is True


def test_artifact_defaults_match_the_spec() -> None:
    artifact = AgentConfigDocument().collectors.artifact

    assert artifact.enabled is True
    assert artifact.max_bytes == 52_428_800
    assert artifact.staging_max_bytes == 524_288_000
    assert artifact.upload_bytes_per_second == 2_097_152
    assert artifact.stage_bytes_per_minute == 209_715_200


def test_artifact_rejects_unknown_keys_and_absurd_values() -> None:
    with pytest.raises(ValidationError):
        AgentConfigDocument.model_validate({"collectors": {"artifact": {"max_byte": 1}}})
    with pytest.raises(ValidationError):
        AgentConfigDocument.model_validate({"collectors": {"artifact": {"max_bytes": 0}}})
