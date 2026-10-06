"""Демонстрационные события для dev-стенда.

Агенты стенда работают на Linux и событий файлов и USB не порождают, поэтому
консоль на стенде почти пуста. Набор ниже повторяет то, что присылает
Windows-агент, и проходит тот же разбор, что и настоящий пакет: форма subject
проверяется `subject_is_valid`, а не принимается на веру.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.models.event import Event
from barysguard.services.events import _parse_line, store_events

DEMO_LABEL = {"demo": "true"}

_USER = {"user_name": "PC\\ivanov", "user_sid": "S-1-5-21-1-2-3-1001"}
_FIXED = {"type": "fixed", "label": "Windows"}
_FLASH = {"type": "removable", "label": "KINGSTON", "serial": "0781-5583"}


def _artifact(name: str, size: int) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(name.encode()).hexdigest(), "size": size, "uploaded": False}


def _file(
    minutes_ago: int, action: str, severity: str, subject: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    return {
        "minutes_ago": minutes_ago,
        "channel": "file",
        "action": action,
        "severity_hint": severity,
        "actor": _USER,
        "subject": subject,
        **extra,
    }


def _templates() -> list[dict[str, Any]]:
    report = "C:\\Users\\ivanov\\Documents\\Отчёт_Q3.xlsx"
    payroll = "C:\\Users\\ivanov\\Documents\\Зарплаты_2026.xlsx"
    clients = "C:\\Finance\\clients_database.csv"
    return [
        {
            "minutes_ago": 50,
            "channel": "usb",
            "action": "mount",
            "severity_hint": "info",
            "actor": {"user_name": "PC\\console", "user_sid": "S-1-5-21-1-2-3-1002"},
            "subject": {
                "drive_letter": "E:",
                "volume": _FLASH,
                "device": {"vendor": "Kingston", "product": "DataTraveler 3.0"},
            },
        },
        _file(
            45,
            "modify",
            "info",
            {"dst_path": report, "size_bytes": 48213, "volume": _FIXED},
            artifact=_artifact(report, 48213),
        ),
        _file(
            30,
            "copy",
            "high",
            {
                "src_path": report,
                "dst_path": "E:\\Отчёт_Q3.xlsx",
                "size_bytes": 48213,
                "volume": _FLASH,
            },
            artifact=_artifact(report, 48213),
            labels={"process": "explorer.exe"},
        ),
        _file(
            22,
            "copy",
            "critical",
            {
                "src_path": clients,
                "dst_path": "E:\\clients_database.csv",
                "size_bytes": 9_482_113,
                "volume": _FLASH,
            },
            artifact=_artifact(clients, 9_482_113),
            labels={"process": "explorer.exe"},
        ),
        _file(
            18,
            "create",
            "medium",
            {"dst_path": "E:\\notes.txt", "size_bytes": 1530, "volume": _FLASH},
            artifact=_artifact("E:\\notes.txt", 1530),
        ),
        _file(
            12,
            "rename",
            "low",
            {
                "old_path": payroll,
                "dst_path": "C:\\Users\\ivanov\\Documents\\Зарплаты_2026_final.xlsx",
                "size_bytes": 20480,
                "volume": _FIXED,
            },
        ),
        _file(
            8,
            "delete",
            "info",
            {"dst_path": "C:\\Users\\ivanov\\Desktop\\черновик.docx", "volume": _FIXED},
        ),
        {
            "minutes_ago": 3,
            "channel": "usb",
            "action": "unmount",
            "severity_hint": "info",
            "actor": {"user_name": "PC\\console", "user_sid": "S-1-5-21-1-2-3-1002"},
            "subject": {
                "drive_letter": "E:",
                "volume": _FLASH,
                "device": {"vendor": "Kingston", "product": "DataTraveler 3.0"},
            },
        },
    ]


def build_demo_events(now: datetime) -> list[dict[str, Any]]:
    """Конверты событий v1 за последний час. Идентификаторы случайные."""
    events: list[dict[str, Any]] = []
    for template in _templates():
        item = dict(template)
        minutes_ago = item.pop("minutes_ago")
        labels = {**item.pop("labels", {}), **DEMO_LABEL}
        events.append(
            {
                "event_id": str(uuid.uuid4()),
                "schema_version": 1,
                "occurred_at": (now - timedelta(minutes=minutes_ago)).isoformat(),
                "process": {},
                "labels": labels,
                **item,
            }
        )
    return events


@dataclass(frozen=True)
class SeedResult:
    agents: int
    inserted: int


async def seed_demo_events(session: AsyncSession) -> SeedResult:
    """Пишет демонстрационные события каждому не отозванному агенту.

    Повторный вызов ничего не добавляет: агент, у которого набор уже есть,
    пропускается. Отметка времени относительна «сейчас», поэтому защитой от
    дублей служит не ключ события, а проверка метки `labels.demo`.
    """
    agents = (await session.scalars(select(Agent).where(Agent.status != AgentStatus.REVOKED))).all()

    now = datetime.now(UTC)
    inserted = 0
    for agent in agents:
        already = await session.scalar(
            select(Event.event_id)
            .where(Event.agent_id == agent.id, Event.labels["demo"].astext == "true")
            .limit(1)
        )
        if already is not None:
            continue

        envelopes = []
        for index, event in enumerate(build_demo_events(now)):
            # Стабильный идентификатор: агент и номер в наборе.
            event["event_id"] = str(uuid.uuid5(agent.id, f"demo-{index}"))
            envelope, reason = _parse_line(json.dumps(event))
            if envelope is None:
                raise ValueError(f"демо-событие {index} не проходит разбор: {reason}")
            envelopes.append(envelope)

        inserted += await store_events(session, agent.id, envelopes, now)

    return SeedResult(agents=len(agents), inserted=inserted)
