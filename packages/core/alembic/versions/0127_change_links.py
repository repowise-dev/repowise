"""``change_commits`` and ``change_files``: a change's commits and paths as rows.

The delta and hotspot filters join a change to its commits' stored deltas and
to the hotspot flags in SQL, rather than binding every matching change id.
Existing changes are linked here from their stored JSON by
``link_unlinked_changes``, which reads them in keyset pages and which the
SQLite reconciler also runs when it creates the tables.

Revision ID: 0127
Revises: 0126
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from repowise.core.persistence.crud.changes import link_unlinked_changes

revision: str = "0127"
down_revision: str | None = "0126"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create(name: str, value: sa.Column, unique: str, index: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_id", sa.String(96), nullable=False),
        value,
        sa.UniqueConstraint("repository_id", "change_id", value.name, name=unique),
    )
    op.create_index(index, name, ["repository_id", value.name])


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    # init_db may already have created them through its additive reconciler.
    if "change_commits" not in existing:
        _create(
            "change_commits",
            sa.Column("sha", sa.String(40), nullable=False),
            "uq_change_commit",
            "ix_change_commits_repo_sha",
        )
    if "change_files" not in existing:
        _create(
            "change_files",
            sa.Column("file_path", sa.Text, nullable=False),
            "uq_change_file",
            "ix_change_files_repo_path",
        )
    if "changes" in existing:
        # Links only changes with no commit rows yet, so a store init_db
        # already linked is left alone.
        link_unlinked_changes(bind)


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in ("change_files", "change_commits"):
        if name in existing:
            op.drop_table(name)
