"""Preserve complete retired wiki page and version records.

Revision ID: 0121
Revises: 0120
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0121"
down_revision: str | None = "0120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    # init_db may already have created these through its additive reconciler.
    if "page_archive" not in tables:
        op.create_table(
            "page_archive",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("page_id", sa.Text(), nullable=False),
            sa.Column(
                "repository_id",
                sa.String(32),
                sa.ForeignKey("repositories.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("record_json", sa.Text(), nullable=False),
            sa.Column("retired_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reason", sa.String(64), nullable=False),
            sa.Column("superseded_by", sa.Text(), nullable=True),
        )
        op.create_index("ix_page_archive_repo_page", "page_archive", ["repository_id", "page_id"])
        op.create_index(
            "ix_page_archive_repo_retired", "page_archive", ["repository_id", "retired_at"]
        )
    if "page_version_archive" not in tables:
        op.create_table(
            "page_version_archive",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "archive_id",
                sa.String(32),
                sa.ForeignKey("page_archive.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("record_json", sa.Text(), nullable=False),
        )
        op.create_index(
            "ix_page_version_archive_archive_id", "page_version_archive", ["archive_id"]
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "page_version_archive" in tables:
        op.drop_table("page_version_archive")
    if "page_archive" in tables:
        op.drop_table("page_archive")
