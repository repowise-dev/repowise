"""Evidence runs record how many tests failed in a profiled test suite.

One nullable column on ``evidence_runs``. Local SQLite stores get it from the
model through the reconciler.

Revision ID: 0133
Revises: 0132
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0133"
down_revision: str | None = "0132"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("evidence_runs")}


def upgrade() -> None:
    if "tests_failed" not in _existing():
        op.add_column("evidence_runs", sa.Column("tests_failed", sa.Integer, nullable=True))


def downgrade() -> None:
    if "tests_failed" in _existing():
        with op.batch_alter_table("evidence_runs") as batch:
            batch.drop_column("tests_failed")
