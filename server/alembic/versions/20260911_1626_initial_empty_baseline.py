"""initial empty baseline

Revision ID: 28e6d06930c4
Revises: 
Create Date: 2026-09-11 16:26:44.795671
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '28e6d06930c4'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
