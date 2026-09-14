"""seed global agent config

Revision ID: bcae04c6f76a
Revises: dc0d578700db
Create Date: 2026-09-14 23:52:18.579895
"""
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'bcae04c6f76a'
down_revision: str | None = 'dc0d578700db'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Значения дублируются здесь намеренно: миграция обязана оставаться
# воспроизводимой, а импорт из приложения привязал бы её к текущей
# редакции AgentConfigDocument.
DEFAULT_DOCUMENT = {
    "transport": {
        "heartbeat_interval_seconds": 30,
        "event_batch_max": 500,
        "event_batch_max_bytes": 4194304,
        "backoff_base_seconds": 1,
        "backoff_max_seconds": 300,
    },
    "buffer": {"max_bytes": 524288000, "max_age_days": 7},
    "logging": {"level": "info"},
    "policies": {},
}


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO agent_configs (id, scope, group_id, document, updated_at) "
            "VALUES (CAST(:id AS uuid), 'GLOBAL', NULL, CAST(:document AS jsonb), now())"
        ).bindparams(id=str(uuid.uuid4()), document=json.dumps(DEFAULT_DOCUMENT))
    )


def downgrade() -> None:
    op.execute("DELETE FROM agent_configs WHERE scope = 'GLOBAL'")
