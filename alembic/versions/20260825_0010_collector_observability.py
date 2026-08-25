"""Add collector run history for ingestion observability.

Revision ID: 20260825_0010
Revises: 20260820_0009
Create Date: 2026-08-25
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0010"
down_revision: str | Sequence[str] | None = "20260820_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collector_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("days_requested", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "collected_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial_failure', "
            "'failed', 'suspicious', 'skipped')",
            name="ck_collector_runs_status",
        ),
    )
    op.create_index("ix_collector_runs_source", "collector_runs", ["source"])
    op.create_index(
        "ix_collector_runs_source_started_at",
        "collector_runs",
        ["source", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collector_runs_source_started_at", table_name="collector_runs"
    )
    op.drop_index("ix_collector_runs_source", table_name="collector_runs")
    op.drop_table("collector_runs")
