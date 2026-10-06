"""inspection: queue, rules, scans, verdicts, incidents

Revision ID: c91f5e2a7d34
Revises: a4d2f6c81b53
Create Date: 2026-10-06 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c91f5e2a7d34"
down_revision: str | None = "a4d2f6c81b53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> sa.sql.elements.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.add_column("events", sa.Column("verdict_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_events_artifact_sha256",
        "events",
        ["artifact_sha256"],
        postgresql_where=sa.text("artifact_sha256 IS NOT NULL"),
    )

    op.create_table(
        "event_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.UniqueConstraint("event_occurred_at", "event_id", name="uq_event_queue_event"),
    )
    op.create_index("ix_event_queue_claim", "event_queue", ["state", "locked_until", "id"])

    op.create_table(
        "rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )
    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "rule_id", sa.Uuid(), sa.ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("rule_id", "version", name="uq_rule_versions_version"),
    )
    op.create_table(
        "dictionaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
    )
    op.create_table(
        "dictionary_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "dictionary_id",
            sa.Uuid(),
            sa.ForeignKey("dictionaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("term", sa.Text(), nullable=False),
        sa.UniqueConstraint("dictionary_id", "term", name="uq_dictionary_terms_term"),
    )

    op.create_table(
        "artifact_scans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("ruleset_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("artifact_sha256", "ruleset_hash", name="uq_artifact_scans_ruleset"),
    )
    op.create_index("ix_artifact_scans_artifact_sha256", "artifact_scans", ["artifact_sha256"])

    op.create_table(
        "verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "scan_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_scans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("severity", sa.Text(), nullable=False, server_default="info"),
        sa.Column(
            "matches", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("event_occurred_at", "event_id", name="uq_verdicts_event"),
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column(
            "assignee", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("events_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_incidents_last_event_at", "incidents", ["last_event_at"])
    op.create_index(
        "uq_incidents_open_group",
        "incidents",
        ["group_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'closed'"),
    )

    op.create_table(
        "incident_events",
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("incident_events")
    op.drop_table("incidents")
    op.drop_table("verdicts")
    op.drop_table("artifact_scans")
    op.drop_table("dictionary_terms")
    op.drop_table("dictionaries")
    op.drop_table("rule_versions")
    op.drop_table("rules")
    op.drop_table("event_queue")
    op.drop_index("ix_events_artifact_sha256", table_name="events")
    op.drop_column("events", "verdict_id")
