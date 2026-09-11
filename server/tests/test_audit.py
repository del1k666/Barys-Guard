import uuid

import pytest

from barysguard.services.audit import record_audit, verify_audit_chain


@pytest.mark.asyncio
async def test_first_entry_has_genesis_previous_hash(session):
    entry = await record_audit(
        session,
        user_id=None,
        action="enrollment_token.create",
        target_type="enrollment_token",
        target_id=None,
        payload={"max_uses": 1},
    )

    assert entry.prev_hash == "0" * 64
    assert len(entry.hash) == 64


@pytest.mark.asyncio
async def test_entries_are_chained(session):
    first = await record_audit(
        session, user_id=None, action="a", target_type="t", target_id=None, payload={}
    )
    second = await record_audit(
        session, user_id=None, action="b", target_type="t", target_id=None, payload={}
    )

    assert second.prev_hash == first.hash
    assert second.seq > first.seq


@pytest.mark.asyncio
async def test_intact_chain_verifies(session):
    for index in range(5):
        await record_audit(
            session,
            user_id=uuid.uuid4(),
            action=f"action.{index}",
            target_type="agent",
            target_id=uuid.uuid4(),
            payload={"index": index},
        )

    assert await verify_audit_chain(session) is None


@pytest.mark.asyncio
async def test_tampering_is_detected(session):
    await record_audit(
        session, user_id=None, action="a", target_type="t", target_id=None, payload={}
    )
    tampered = await record_audit(
        session, user_id=None, action="b", target_type="t", target_id=None, payload={}
    )
    await record_audit(
        session, user_id=None, action="c", target_type="t", target_id=None, payload={}
    )

    # Кто-то отредактировал запись напрямую в базе, минуя приложение.
    tampered.action = "harmless"
    await session.flush()

    assert await verify_audit_chain(session) == tampered.seq
