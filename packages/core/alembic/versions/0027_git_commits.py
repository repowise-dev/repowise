"""Create git_commits table for per-commit change-risk tracking.

Stores one row per commit in the indexed window: SHA, author, timestamp,
subject, Kamei change features (diff size + diffusion), and the calibrated
just-in-time ``change_risk`` score/level. Written during the existing
repo-wide commit-index walk (no extra git pass); bounded by the indexer's
commit_limit. Foundation for a commits/change-risk surface and change trends.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_commits",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("repository_id", sa.String(32), nullable=False),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("author_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("author_email", sa.String(255), nullable=False, server_default=""),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject", sa.Text, nullable=False, server_default=""),
        sa.Column("lines_added", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lines_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_changed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dirs_changed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("subsystems_changed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("entropy", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("is_fix", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("change_risk_score", sa.Float, nullable=True),
        sa.Column("change_risk_level", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("repository_id", "sha", name="uq_git_commit"),
    )
    op.create_index(
        "ix_git_commits_repo_risk",
        "git_commits",
        ["repository_id", "change_risk_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_git_commits_repo_risk", table_name="git_commits")
    op.drop_table("git_commits")
