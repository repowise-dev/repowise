"""``git_commit_files``: one row per commit x file, with that file's churn.

``git_commits`` keeps only the Kamei aggregates the change-risk model reads, so
every per-commit surface had to shell out to git to answer "which files?" — and
a server with no checkout could not answer at all. The rows come free: the
commit walk already parses ``git log --numstat`` per commit and throws the
per-file tuples away once the aggregates are derived.

No change-type column. ``--numstat`` gives paths and line counts only, so
add / modify / delete / rename cannot be separated without a second git pass.

New table only — no existing table is touched, so a pre-0074 wiki.db upgrades
without rewriting anything and reads an empty table until the next index.

Revision ID: 0074
Revises: 0073
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_commit_files",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("lines_added", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lines_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "sha", "file_path", name="uq_git_commit_file"),
    )
    op.create_index("ix_git_commit_files_repo_sha", "git_commit_files", ["repository_id", "sha"])
    op.create_index(
        "ix_git_commit_files_repo_path", "git_commit_files", ["repository_id", "file_path"]
    )


def downgrade() -> None:
    op.drop_index("ix_git_commit_files_repo_path", table_name="git_commit_files")
    op.drop_index("ix_git_commit_files_repo_sha", table_name="git_commit_files")
    op.drop_table("git_commit_files")
