"""Импорт всех моделей. Alembic полагается на этот модуль для автогенерации."""

from barysguard.db.models.agent import Agent, AgentGroup, AgentStatus
from barysguard.db.models.artifact import Artifact, UploadSession
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.certificate import AgentCertificate
from barysguard.db.models.command import Command, CommandStatus, CommandType
from barysguard.db.models.config import AgentConfig, ConfigScope
from barysguard.db.models.console_session import ConsoleSession
from barysguard.db.models.enrollment import EnrollmentToken
from barysguard.db.models.event import Event
from barysguard.db.models.inspection import (
    ArtifactScan,
    Dictionary,
    DictionaryTerm,
    EventQueue,
    Incident,
    IncidentEvent,
    Rule,
    RuleVersion,
    Verdict,
)
from barysguard.db.models.user import User, UserRole

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentCertificate",
    "AgentGroup",
    "AgentStatus",
    "Artifact",
    "ArtifactScan",
    "AuditLog",
    "Command",
    "CommandStatus",
    "CommandType",
    "ConfigScope",
    "ConsoleSession",
    "Dictionary",
    "DictionaryTerm",
    "EnrollmentToken",
    "Event",
    "EventQueue",
    "Incident",
    "IncidentEvent",
    "Rule",
    "RuleVersion",
    "UploadSession",
    "User",
    "UserRole",
    "Verdict",
]
