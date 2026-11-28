"""Advisory table: ``package_advisories``.

The last advisory document ``repowise packages import`` read for a repository:
one row per (package, advisory) with severity, affected symbols, affected ranges
and fixed versions, each stamped with the document's format and file hash. A new
table only.

Local SQLite stores get it from the models through ``init_db``; this migration
exists for managed Postgres.

Revision ID: 0151
Revises: 0150
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0151"
down_revision: str | None = "0150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # init_db may already have created it through create_all and the reconciler.
    if "package_advisories" in set(inspector.get_table_names()):
        return
    op.create_table(
        "package_advisories",
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ecosystem", sa.String(32), primary_key=True),
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("advisory_id", sa.Text, primary_key=True),
        sa.Column("versions_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("direct", sa.Boolean, nullable=True),
        sa.Column("aliases_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("affected_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("fixed_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("ranges_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("versions_affected_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("source_format", sa.String(16), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("source", sa.Text, nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_with_rowid=False,
    )


def downgrade() -> None:
    if "package_advisories" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("package_advisories")
