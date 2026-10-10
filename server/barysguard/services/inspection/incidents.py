"""Создание и дополнение инцидентов по вердиктам."""

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.event import Event
from barysguard.db.models.inspection import Incident, IncidentEvent, Verdict

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]

_RULE_LABELS = {"iin_bin": "ИИН/БИН", "card": "карта", "markings": "гриф"}
_ACTION_TITLES = {
    "copy": "Копирование на USB",
    "create": "Запись на USB",
    "modify": "Изменение на USB",
    "upload": "Отправка файла в сеть",
}


def build_title(action: str, matches: list[dict[str, Any]]) -> str:
    head = _ACTION_TITLES.get(action, "Файловое событие")
    # Порядок в заголовке постоянный (как в _RULE_LABELS), а не по ключам правил в БД.
    order = list(_RULE_LABELS)
    ranked = sorted(
        matches, key=lambda m: order.index(m["rule_key"]) if m["rule_key"] in order else len(order)
    )
    parts = ", ".join(
        f"{_RULE_LABELS.get(m['rule_key']) or m.get('rule_title') or m['rule_key']} ×{m['count']}"
        for m in ranked
    )
    return f"{head}: {parts}"


def _group_key(event: Event) -> str:
    actor = (event.actor or {}).get("user_name") or "unknown"
    return f"{event.agent_id}|{actor}|{event.artifact_sha256}"


async def _open_incident(session: AsyncSession, key: str) -> Incident | None:
    return (
        await session.scalars(
            select(Incident)
            .where(Incident.group_key == key, Incident.status != "closed")
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def apply_verdict(session: AsyncSession, event: Event, verdict: Verdict) -> Incident:
    """Дополняет открытый инцидент группы или заводит новый; возвращает инцидент."""
    key = _group_key(event)
    title = build_title(event.action, verdict.matches)

    incident = await _open_incident(session, key)
    if incident is None:
        await session.execute(
            pg_insert(Incident)
            .values(
                id=uuid.uuid4(),
                group_key=key,
                agent_id=event.agent_id,
                artifact_sha256=event.artifact_sha256,
                title=title,
                severity=verdict.severity,
                score=verdict.score,
                status="open",
                first_event_at=event.occurred_at,
                last_event_at=event.occurred_at,
                events_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=["group_key"], index_where=text("status <> 'closed'")
            )
        )
        # Если вставку опередил другой воркер, блокировка возьмёт его строку.
        incident = await _open_incident(session, key)
        assert incident is not None  # noqa: S101 - строка только что вставлена или чужая

    linked = (
        await session.execute(
            pg_insert(IncidentEvent)
            .values(
                incident_id=incident.id,
                event_occurred_at=event.occurred_at,
                event_id=event.event_id,
            )
            .on_conflict_do_nothing()
            .returning(IncidentEvent.event_id)
        )
    ).scalar_one_or_none()

    if linked is not None:
        incident.events_count += 1
        incident.first_event_at = min(incident.first_event_at, event.occurred_at)
        incident.last_event_at = max(incident.last_event_at, event.occurred_at)
        if verdict.score > incident.score:
            incident.score = verdict.score
            incident.title = title
        if SEVERITY_ORDER.index(verdict.severity) > SEVERITY_ORDER.index(incident.severity):
            incident.severity = verdict.severity
        await session.flush()
    return incident
