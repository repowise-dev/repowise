"""Evidence runs keep a test-timing run's per-file totals and duration histogram.

The per-run row cap keeps only the slowest tests, so what every test of the
run adds up to is folded before the cap and kept on the run. One nullable
column on ``evidence_runs``. Local SQLite stores get it from the model through
the reconciler.

Revision ID: 0143
Revises: 0142
Create Date: 2026-11-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0143"
down_revision: str | None = "0142"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("evidence_runs")}


def upgrade() -> None:
    if "timing_summary_json" not in _existing():
        op.add_column("evidence_runs", sa.Column("timing_summary_json", sa.Text, nullable=True))


def downgrade() -> None:
    if "timing_summary_json" in _existing():
        with op.batch_alter_table("evidence_runs") as batch:
            batch.drop_column("timing_summary_json")
