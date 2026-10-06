"""Таблицы инспекции содержимого: очередь, правила, сканы, вердикты, инциденты."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from barysguard.db.base import Base


class EventQueue(Base):
    """Очередь инспекции. Ссылка на событие составная: ключ events партиционирован."""

    __tablename__ = "event_queue"
    __table_args__ = (
        UniqueConstraint("event_occurred_at", "event_id", name="uq_event_queue_event"),
        Index("ix_event_queue_claim", "state", "locked_until", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[uuid.UUID] = mapped_column()
    artifact_sha256: Mapped[str] = mapped_column(Text)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pending | failed
    state: Mapped[str] = mapped_column(Text, server_default="pending")


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(Text, unique=True)
    # detector | dictionary
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # Встроенные правила (ИИН/БИН, карты, грифы): их нельзя удалить и нельзя менять тип и ключ.
    builtin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RuleVersion(Base):
    """Неизменяемая версия правила: вердикт ссылается именно на неё."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_rule_versions_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dictionary(Base):
    __tablename__ = "dictionaries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)


class DictionaryTerm(Base):
    __tablename__ = "dictionary_terms"
    __table_args__ = (UniqueConstraint("dictionary_id", "term", name="uq_dictionary_terms_term"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dictionaries.id", ondelete="CASCADE")
    )
    term: Mapped[str] = mapped_column(Text)


class ArtifactScan(Base):
    """Результат скана содержимого: один на хеш и версию набора правил."""

    __tablename__ = "artifact_scans"
    __table_args__ = (
        UniqueConstraint("artifact_sha256", "ruleset_hash", name="uq_artifact_scans_ruleset"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artifact_sha256: Mapped[str] = mapped_column(Text, index=True)
    ruleset_hash: Mapped[str] = mapped_column(Text)
    # ok | unsupported | no_text | encrypted | too_large | error
    status: Mapped[str] = mapped_column(Text)
    truncated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # {rule_key: {rule_version_id, count, samples}} — только счётчики и маски.
    findings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Verdict(Base):
    __tablename__ = "verdicts"
    __table_args__ = (UniqueConstraint("event_occurred_at", "event_id", name="uq_verdicts_event"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[uuid.UUID] = mapped_column()
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_scans.id", ondelete="SET NULL")
    )
    # clean | flagged | not_inspected
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, server_default="0")
    severity: Mapped[str] = mapped_column(Text, server_default="info")
    # [{rule_key, rule_version_id, count, points, samples}]
    matches: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        # Пока инцидент не закрыт, у ключа группировки он единственный.
        Index(
            "uq_incidents_open_group",
            "group_key",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    group_key: Mapped[str] = mapped_column(Text)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    artifact_sha256: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    # open | acknowledged | closed
    status: Mapped[str] = mapped_column(Text, server_default="open")
    assignee: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    events_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    event_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
