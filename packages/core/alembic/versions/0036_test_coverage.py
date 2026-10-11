"""test_coverage table - the per-test test-to-code map.

Persists one ``(test, source file)`` coverage fact per row so the reverse
index "given changed lines, which tests hit them" is a straight query.
Where ``coverage_files`` stores per-file aggregate coverage (merged across
every test), this keeps the test dimension. Point-in-time only: overwritten
per ingest run, no history - mirroring ``coverage_files``.

A table rather than a graph edge: the first consumer is a CI lookup keyed by
changed source file + lines. Populated only from context-carrying reports
(coverage.py contexts / lcov TN records), which are opt-in.

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_coverage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_id", sa.Text, nullable=False),
        sa.Column("test_file", sa.Text, nullable=True),
        sa.Column("source_file", sa.Text, nullable=False),
        sa.Column("covered_lines_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("source_format", sa.String(32), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ingested_commit_sha", sa.String(40), nullable=True),
        sa.UniqueConstraint("repository_id", "test_id", "source_file", name="uq_test_coverage"),
    )
    op.create_index(
        "ix_test_coverage_repo_source",
        "test_coverage",
        ["repository_id", "source_file"],
    )
    op.create_index(
        "ix_test_coverage_repo_test",
        "test_coverage",
        ["repository_id", "test_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_coverage_repo_test", table_name="test_coverage")
    op.drop_index("ix_test_coverage_repo_source", table_name="test_coverage")
    op.drop_table("test_coverage")
