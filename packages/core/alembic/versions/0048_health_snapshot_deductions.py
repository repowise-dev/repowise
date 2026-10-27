"""Record how deep a floored file actually is, per snapshot.

``health_snapshots.per_file_scores_json`` stores the **clamped** score, and the
score clamps at 1.0. On the repowise index 28 of the 29 floored files have a
completely flat 1.0 series across every snapshot, with 9.06 to 12.91 points of
deduction hidden behind that 1.0 — so improving the worst file is invisible
until roughly a third of the work is already done.

This adds a sibling column rather than changing the shape of the existing one.
``per_file_scores_json`` is ``{path: score}`` and three readers parse it as
such; a value that became a dict would blank every file's series in
``trends.file_score_series`` silently and 500 the trend route loudly, while
``get_health_snapshot_file_counts``' ``len(parsed)`` would keep working and hide
both. A second column cannot do any of that.

Only files whose score sits at the floor are recorded — for every other file the
deduction is exactly ``10 - score``, so this stores what the clamp destroys and
nothing else. Measured on the repowise index: 1,895 B against a 187,597 B score
map, versus 142,812 B if every file with findings were written.

Rows written before this lands have no deduction data and keep reporting their
clamped score. That is correct: the depth was never captured, and inventing one
would be worse than a flat line.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones. The two converge on the same shape.

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        c["name"] == "per_file_deductions_json"
        for c in inspector.get_columns("health_snapshots")
    )


def upgrade() -> None:
    # Guarded because ``init_db``'s additive reconciler adds any model column
    # missing from a live table, and it runs against whatever URL the server is
    # booted with — Postgres included. A deployment where the app started
    # before the migration ran would otherwise wedge the chain on a
    # duplicate-column error.
    if _has_column():
        return
    op.add_column(
        "health_snapshots",
        sa.Column(
            "per_file_deductions_json",
            sa.Text,
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    if _has_column():
        op.drop_column("health_snapshots", "per_file_deductions_json")
