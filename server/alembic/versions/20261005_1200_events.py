"""events

Revision ID: e7a1c3b94d20
Revises: 3263ff47aa7a
Create Date: 2026-10-05 12:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'e7a1c3b94d20'
down_revision: str | None = '3263ff47aa7a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Границы разделов считаются по UTC независимо от пояса сессии.
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute(
        """
        CREATE TABLE events (
            event_id uuid NOT NULL,
            agent_id uuid NOT NULL,
            schema_version smallint NOT NULL,
            channel text NOT NULL,
            action text NOT NULL,
            occurred_at timestamptz NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            actor jsonb NOT NULL DEFAULT '{}',
            process jsonb NOT NULL DEFAULT '{}',
            subject jsonb NOT NULL DEFAULT '{}',
            labels jsonb NOT NULL DEFAULT '{}',
            artifact_sha256 text,
            severity text NOT NULL DEFAULT 'info',
            CONSTRAINT pk_events PRIMARY KEY (occurred_at, event_id),
            CONSTRAINT fk_events_agent_id_agents FOREIGN KEY (agent_id)
                REFERENCES agents (id) ON DELETE CASCADE
        ) PARTITION BY RANGE (occurred_at)
        """
    )
    # Страховка: событие с отметкой вне созданных разделов (часы агента неверны)
    # не должно теряться и не должно ронять приём всего пакета.
    op.execute("CREATE TABLE events_default PARTITION OF events DEFAULT")
    op.execute("CREATE INDEX ix_events_agent_occurred ON events (agent_id, occurred_at DESC)")
    op.execute("CREATE INDEX ix_events_channel_occurred ON events (channel, occurred_at DESC)")

    # Текущий месяц и два следующих. Дальше разделы создаёт приложение при старте
    # и команда barysguard-admin ensure-partitions.
    op.execute(
        """
        DO $$
        DECLARE
            month_start date;
        BEGIN
            FOR i IN 0..2 LOOP
                month_start := (date_trunc('month', now() AT TIME ZONE 'UTC')
                                + make_interval(months => i))::date;
                EXECUTE format(
                    'CREATE TABLE events_%s PARTITION OF events '
                    'FOR VALUES FROM (%L) TO (%L)',
                    to_char(month_start, 'YYYY_MM'),
                    month_start::timestamptz,
                    (month_start + interval '1 month')::timestamptz
                );
            END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE events CASCADE")
