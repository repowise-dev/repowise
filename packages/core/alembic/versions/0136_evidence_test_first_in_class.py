"""Test timings mark the first test a run reports for each class.

One nullable column on ``evidence_test_timings``: a fixture shared by a class
or module runs inside its first test, so that test's time may carry it. Local
SQLite stores get it from the model through the reconciler.

Revision ID: 0136
Revises: 0135
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0136"
down_revision: str | None = "0135"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("evidence_test_timings")}


def upgrade() -> None:
    if "first_in_class" not in _existing():
        op.add_column(
            "evidence_test_timings", sa.Column("first_in_class", sa.Boolean, nullable=True)
        )


def downgrade() -> None:
    if "first_in_class" in _existing():
        with op.batch_alter_table("evidence_test_timings") as batch:
            batch.drop_column("first_in_class")
