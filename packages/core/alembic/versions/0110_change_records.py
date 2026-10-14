"""Change Records: the ``changes`` table and a stored commit category.

One row per merged change reconstructed from git (see
``repowise.core.changes``). ``git_commits.category`` keeps the single-label
category the evolution timeline computed on read; existing rows stay NULL
until the next index, which the git-history stamp forces.

Local SQLite stores never run Alembic; ``init_db`` creates the table and adds
the column there.

Revision ID: 0110
Revises: 0109
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0110"
down_revision: str | None = "0109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("git_commits", sa.Column("category", sa.String(16), nullable=True))
    op.create_table(
        "changes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_id", sa.String(96), nullable=False),
        sa.Column("identity_kind", sa.String(16), nullable=False),
        sa.Column("identity_value", sa.String(64), nullable=False),
        sa.Column("shape", sa.String(16), nullable=False),
        sa.Column("forge", sa.String(16), nullable=False),
        sa.Column("forge_ref", sa.String(32), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("author_name", sa.String(255), nullable=False),
        sa.Column("author_email", sa.String(255), nullable=False),
        sa.Column("commit_shas_json", sa.Text, nullable=False),
        sa.Column("files_json", sa.Text, nullable=False),
        sa.Column("files_changed", sa.Integer, nullable=False),
        sa.Column("lines_added", sa.Integer, nullable=False),
        sa.Column("lines_deleted", sa.Integer, nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("provenance", sa.String(16), nullable=True),
        sa.Column("provenance_agent", sa.Integer, nullable=True),
        sa.Column("provenance_known", sa.Integer, nullable=True),
        sa.Column("metadata_source", sa.String(32), nullable=True),
        sa.Column("reviewers_json", sa.Text, nullable=True),
        sa.Column("approvals", sa.Integer, nullable=True),
        sa.Column("labels_json", sa.Text, nullable=True),
        sa.Column("linked_issues_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "change_id", name="uq_change"),
    )
    op.create_index("ix_changes_repo_merged", "changes", ["repository_id", "merged_at"])


def downgrade() -> None:
    op.drop_index("ix_changes_repo_merged", table_name="changes")
    op.drop_table("changes")
    op.drop_column("git_commits", "category")
