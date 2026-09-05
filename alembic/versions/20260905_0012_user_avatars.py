"""Add optional avatar URLs to user profiles.

Revision ID: 20260905_0012
Revises: 20260826_0011
Create Date: 2026-09-05
"""

from typing import Sequence

from alembic import op


revision: str = "20260905_0012"
down_revision: str | Sequence[str] | None = "20260826_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500)")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_url")
