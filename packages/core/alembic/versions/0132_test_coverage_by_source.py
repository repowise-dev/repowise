"""Per-test coverage map stored one row per source file.

``test_coverage`` kept a row per ``(test, source file)`` pair, repeating the
test id and source path in the row and in three indexes: about 1 KB a pair, so
a 50k-test suite (about 1.2M pairs) needed over a gigabyte. It is replaced by
``test_coverage_tests`` (each test once, numbered within its repository) and
``test_coverage_sources`` (one row per covered source file, listing its tests
by number with their lines as ranges). The map is derived from a coverage
report, so the old rows are dropped rather than copied: the next
``repowise coverage add`` rebuilds it.

Revision ID: 0132
Revises: 0131
Create Date: 2026-11-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0132"
down_revision: str | None = "0131"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _repository_id(primary_key: bool) -> sa.Column:
    return sa.Column(
        "repository_id",
        sa.String(32),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "test_coverage_tests",
        _repository_id(True),
        sa.Column("test_no", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("test_id", sa.Text, nullable=False),
        sa.Column("test_file", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_test_coverage_tests_repo_test",
        "test_coverage_tests",
        ["repository_id", "test_id"],
    )
    op.create_table(
        "test_coverage_sources",
        _repository_id(True),
        sa.Column("source_file", sa.Text, primary_key=True),
        sa.Column("tests_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("test_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source_format", sa.String(32), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ingested_commit_sha", sa.String(40), nullable=True),
    )
    op.drop_index("ix_test_coverage_repo_test", table_name="test_coverage")
    op.drop_index("ix_test_coverage_repo_source", table_name="test_coverage")
    op.drop_table("test_coverage")


def downgrade() -> None:
    op.create_table(
        "test_coverage",
        sa.Column("id", sa.String(32), primary_key=True),
        _repository_id(False),
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
        "ix_test_coverage_repo_source", "test_coverage", ["repository_id", "source_file"]
    )
    op.create_index("ix_test_coverage_repo_test", "test_coverage", ["repository_id", "test_id"])
    op.drop_table("test_coverage_sources")
    op.drop_index("ix_test_coverage_tests_repo_test", table_name="test_coverage_tests")
    op.drop_table("test_coverage_tests")
