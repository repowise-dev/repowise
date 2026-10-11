"""Agent-provenance columns on git_commits + git_metadata.

Per-commit: which coding agent (if any) authored the commit, its autonomy tier
(1 near-autonomous bot account · 2 human-driven agent · 3 assisted), the
attribution channel, and a confidence band. Per-file: the rollup share of
agent-attributed commits and the tier breakdown. Classification is
deterministic local-git pattern matching (identity fields, message footers,
co-author trailers) riding the existing commit-index walk — no extra git pass,
no network. All columns are nullable / defaulted and back-populated on the
next index.

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("git_commits", sa.Column("agent_name", sa.String(32), nullable=True))
    op.add_column("git_commits", sa.Column("agent_autonomy_tier", sa.Integer, nullable=True))
    op.add_column("git_commits", sa.Column("agent_channel", sa.String(32), nullable=True))
    op.add_column("git_commits", sa.Column("agent_confidence", sa.String(8), nullable=True))

    op.add_column(
        "git_metadata",
        sa.Column("agent_commit_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("git_metadata", sa.Column("agent_authored_pct", sa.Float, nullable=True))
    op.add_column(
        "git_metadata",
        sa.Column("agent_tier_counts_json", sa.Text, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "agent_tier_counts_json")
    op.drop_column("git_metadata", "agent_authored_pct")
    op.drop_column("git_metadata", "agent_commit_count")
    op.drop_column("git_commits", "agent_confidence")
    op.drop_column("git_commits", "agent_channel")
    op.drop_column("git_commits", "agent_autonomy_tier")
    op.drop_column("git_commits", "agent_name")
