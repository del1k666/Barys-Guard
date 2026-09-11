import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.audit import AuditLog

GENESIS_HASH = "0" * 64


def compute_entry_hash(
    seq: int,
    at: datetime,
    user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict[str, Any],
    prev_hash: str,
) -> str:
    """Хеш записи. Порядок и форма полей фиксированы — иначе проверка развалится."""
    material = json.dumps(
        {
            "seq": seq,
            "at": at.isoformat(),
            "user_id": str(user_id) if user_id else None,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id) if target_id else None,
            "payload": payload,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def record_audit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> AuditLog:
    previous = (
        await session.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1))
    ).scalar_one_or_none()
    prev_hash = previous.hash if previous else GENESIS_HASH

    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        prev_hash=prev_hash,
        hash="",
    )
    session.add(entry)
    await session.flush()

    # seq выдаётся последовательностью, at — server_default. После flush
    # оба значения существуют в базе, но НЕ в объекте: без refresh
    # entry.seq и entry.at будут None и хеш посчитается по пустышкам.
    await session.refresh(entry)

    entry.hash = compute_entry_hash(
        entry.seq,
        entry.at,
        entry.user_id,
        entry.action,
        entry.target_type,
        entry.target_id,
        entry.payload,
        entry.prev_hash,
    )
    await session.flush()
    return entry


async def verify_audit_chain(session: AsyncSession) -> int | None:
    """Возвращает seq первой повреждённой записи либо None, если цепочка цела."""
    entries = (
        (await session.execute(select(AuditLog).order_by(AuditLog.seq.asc()))).scalars().all()
    )

    expected_prev = GENESIS_HASH
    for entry in entries:
        if entry.prev_hash != expected_prev:
            return entry.seq
        recomputed = compute_entry_hash(
            entry.seq,
            entry.at,
            entry.user_id,
            entry.action,
            entry.target_type,
            entry.target_id,
            entry.payload,
            entry.prev_hash,
        )
        if recomputed != entry.hash:
            return entry.seq
        expected_prev = entry.hash

    return None
