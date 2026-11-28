"""Issue link table: ``issue_links``.

One row per (commit, tracker issue) an ``issue-source`` returned: the issue's
type, status, title, url and how the commit names it. A ``bug`` or
``incident`` link makes its commit a fix. A new table only.

Local SQLite stores get it from the models through ``init_db``; this migration
exists for managed Postgres.

Revision ID: 0155
Revises: 0154
Create Date: 2026-11-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0155"
down_revision: str | None = "0154"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # init_db may already have created it through create_all and the reconciler.
    if "issue_links" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "issue_links",
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("commit_sha", sa.String(40), primary_key=True),
        sa.Column("issue_key", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("issue_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default=""),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("url", sa.Text, nullable=False, server_default=""),
        sa.Column("basis", sa.Text, nullable=False, server_default=""),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_with_rowid=False,
    )


def downgrade() -> None:
    if "issue_links" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("issue_links")
