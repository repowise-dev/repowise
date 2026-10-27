"""Coverage provenance: an exact covered count, and a record of each ingest.

``coverage_files.covered_line_count`` is the size of the covered-line set, so
repo and module aggregates count covered lines without reading the blob or
deriving them back from a rounded percentage. NULL on older rows.

``coverage_ingests`` holds one row per repository describing the ingest that
wrote its coverage rows: every report format merged, how many of the report's
paths matched, did not match or tied, a capped sample of the misses, whether
the mapping was partial, and the commit. The coverage rows keep only matches,
so these counts exist nowhere else.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler creates new
tables and adds missing columns -- so this migration exists for managed
Postgres. Both steps are guarded because the reconciler may have run first.

Revision ID: 0080
Revises: 0079
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.engine.reflection.Inspector:
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    columns = {c["name"] for c in _inspector().get_columns("coverage_files")}
    if "covered_line_count" not in columns:
        op.add_column(
            "coverage_files",
            sa.Column("covered_line_count", sa.Integer(), nullable=True),
        )
    if not _inspector().has_table("coverage_ingests"):
        op.create_table(
            "coverage_ingests",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "repository_id",
                sa.String(32),
                sa.ForeignKey("repositories.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_formats_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("report_path_count", sa.Integer(), nullable=True),
            sa.Column("matched_path_count", sa.Integer(), nullable=True),
            sa.Column("unmatched_path_count", sa.Integer(), nullable=True),
            sa.Column("ambiguous_path_count", sa.Integer(), nullable=True),
            sa.Column("unmatched_sample_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("mapping_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "ingested_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("ingested_commit_sha", sa.String(40), nullable=True),
            sa.UniqueConstraint("repository_id", name="uq_coverage_ingests"),
        )


def downgrade() -> None:
    if _inspector().has_table("coverage_ingests"):
        op.drop_table("coverage_ingests")
    columns = {c["name"] for c in _inspector().get_columns("coverage_files")}
    if "covered_line_count" in columns:
        op.drop_column("coverage_files", "covered_line_count")
