"""Таблицы artifacts и upload_sessions."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from barysguard.db.models.artifact import Artifact, UploadSession
from tests.helpers import enroll_agent

SHA = "ab" * 32


def _artifact(**extra) -> Artifact:
    return Artifact(sha256=SHA, size=5, storage_path="ab/ab/x.enc", key_wrapped=b"k" * 40, **extra)


async def _only_id(session) -> uuid.UUID:
    return (await session.scalars(select(Artifact.id))).one()


async def test_artifact_defaults(app_client, session) -> None:
    session.add(_artifact())
    await session.commit()

    stored = await session.get(Artifact, (await _only_id(session)))
    assert stored is not None
    assert stored.ref_count == 1
    assert stored.scan_status == "pending"
    assert stored.wrap_version == 1
    assert stored.first_seen_at is not None


async def test_artifact_sha256_is_unique(app_client, session) -> None:
    session.add(_artifact())
    await session.commit()

    session.add(_artifact())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_one_open_session_per_agent_and_hash(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "models-1")
    now = datetime.now(UTC)

    def make() -> UploadSession:
        return UploadSession(
            agent_id=agent.agent_id,
            artifact_sha256=SHA,
            expected_size=10,
            temp_path="tmp/x.part",
            expires_at=now + timedelta(hours=1),
        )

    session.add(make())
    await session.commit()

    session.add(make())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
