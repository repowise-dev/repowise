"""Widen evidence commit SHA columns to String(64) for SHA-256 support.

Three columns that store evidence commit SHAs were declared String(40):
- evidence_runs.commit_sha
- evidence_test_stats.last_commit_sha
- symbol_hotness.evidence_commit_sha

The v1 parser accepts 64-character SHA-256 commit hashes, but on PostgreSQL
the 40-character columns fail at INSERT.

Revision ID: 0150
Revises: 0149
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0150"
down_revision: str | None = "0149"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("evidence_runs", "commit_sha"),
    ("evidence_test_stats", "last_commit_sha"),
    ("symbol_hotness", "evidence_commit_sha"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                existing_type=sa.String(40),
                type_=sa.String(64),
                existing_nullable=True,
            )


def downgrade() -> None:
    for table, column in _COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                existing_type=sa.String(64),
                type_=sa.String(40),
                existing_nullable=True,
            )
