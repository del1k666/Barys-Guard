"""rules editor: builtin flag and updated_at

Revision ID: d4a8b27c5e91
Revises: c91f5e2a7d34
Create Date: 2026-10-06 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a8b27c5e91"
down_revision: str | None = "c91f5e2a7d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rules", sa.Column("builtin", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column(
        "rules",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.execute("UPDATE rules SET builtin = true WHERE key IN ('iin_bin', 'card', 'markings')")


def downgrade() -> None:
    op.drop_column("rules", "updated_at")
    op.drop_column("rules", "builtin")
