"""Make canonical movie ratings TMDB-specific.

Revision ID: 20260826_0011
Revises: 20260825_0010
Create Date: 2026-08-26
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_0011"
down_revision: str | Sequence[str] | None = "20260825_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Render can briefly overlap old/new instances during a deploy. IF NOT
    # EXISTS also lets this migration recover if a previous attempt created a
    # column but stopped before Alembic recorded the revision.
    op.execute(
        "ALTER TABLE movies ADD COLUMN IF NOT EXISTS "
        "rating_vote_count INTEGER"
    )
    op.execute(
        "ALTER TABLE movies ADD COLUMN IF NOT EXISTS "
        "rating_source VARCHAR(50)"
    )
    op.alter_column(
        "movies",
        "rating",
        existing_type=sa.Float(),
        nullable=True,
        server_default=None,
    )
    # Provider scores used incompatible semantics. Keep only values which were
    # already imported from TMDB; the enrichment command will refresh them.
    op.execute(
        "UPDATE movies SET rating = NULL "
        "WHERE tmdb_id IS NULL OR metadata_source IS DISTINCT FROM 'tmdb'"
    )
    op.execute(
        "UPDATE movies SET rating_source = 'tmdb' "
        "WHERE tmdb_id IS NOT NULL AND rating IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("UPDATE movies SET rating = 0 WHERE rating IS NULL")
    op.alter_column(
        "movies",
        "rating",
        existing_type=sa.Float(),
        nullable=False,
        server_default="0",
    )
    op.execute("ALTER TABLE movies DROP COLUMN IF EXISTS rating_source")
    op.execute("ALTER TABLE movies DROP COLUMN IF EXISTS rating_vote_count")
