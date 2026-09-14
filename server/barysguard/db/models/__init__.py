"""Импорт всех моделей. Alembic полагается на этот модуль для автогенерации."""

from barysguard.db.models.agent import Agent, AgentGroup, AgentStatus
from barysguard.db.models.audit import AuditLog
from barysguard.db.models.certificate import AgentCertificate
from barysguard.db.models.config import AgentConfig, ConfigScope
from barysguard.db.models.enrollment import EnrollmentToken
from barysguard.db.models.user import User, UserRole

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentCertificate",
    "AgentGroup",
    "AgentStatus",
    "AuditLog",
    "ConfigScope",
    "EnrollmentToken",
    "User",
    "UserRole",
]
