"""Component snapshots per index run, and the typed events diffed from them.

``snapshot_runs`` holds one row per index run with the analyzer and parser
stamp it ran under; ``component_snapshots`` the facts per component of that
run; ``events`` the typed events two consecutive same-stamp runs produced
(see ``repowise.core.events``).

Local SQLite stores never run Alembic; ``init_db`` creates the tables there.

Revision ID: 0113
Revises: 0112
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0113"
down_revision: str | None = "0112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "snapshot_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("stamp", sa.String(64), nullable=False),
        sa.Column("compacted", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_snapshot_runs_repo_taken", "snapshot_runs", ["repository_id", "taken_at"])
    op.create_table(
        "component_snapshots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("snapshot_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("component", sa.String(255), nullable=False),
        sa.Column("facts_json", sa.Text, nullable=False),
        sa.UniqueConstraint("run_id", "component", name="uq_component_snapshot"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("component", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("base_commit_sha", sa.String(40), nullable=True),
        sa.Column("stamp", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_events_repo_occurred", "events", ["repository_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_events_repo_occurred", table_name="events")
    op.drop_table("events")
    op.drop_table("component_snapshots")
    op.drop_index("ix_snapshot_runs_repo_taken", table_name="snapshot_runs")
    op.drop_table("snapshot_runs")
