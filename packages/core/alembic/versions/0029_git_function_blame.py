"""Create git_function_blame table for the per-function blame rollup.

Persists the function-granular git signals derived from the per-line blame
index during FULL-tier health analysis (modification count, median line age,
recent-modification count, blame owner) — previously computed and discarded.
Bounded by the number of modified functions; raw per-line blame is not stored.

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_function_blame",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("repository_id", sa.String(32), nullable=False),
        sa.Column("symbol_id", sa.String(512), nullable=False),
        sa.Column("file_path", sa.Text, nullable=False, server_default=""),
        sa.Column("function_name", sa.Text, nullable=False, server_default=""),
        sa.Column("start_line", sa.Integer, nullable=False, server_default="0"),
        sa.Column("end_line", sa.Integer, nullable=False, server_default="0"),
        sa.Column("line_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("mod_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("recent_mod_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("median_author_time", sa.Integer, nullable=True),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("owner_email", sa.String(255), nullable=True),
        sa.Column("owner_line_pct", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("repository_id", "symbol_id", name="uq_git_function_blame"),
    )
    op.create_index(
        "ix_git_function_blame_repo_mods",
        "git_function_blame",
        ["repository_id", "mod_count"],
    )


def downgrade() -> None:
    op.drop_index("ix_git_function_blame_repo_mods", table_name="git_function_blame")
    op.drop_table("git_function_blame")
