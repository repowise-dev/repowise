"""Make coverage_files.line_coverage_pct nullable.

A file with zero coverable lines (an lcov record that reports ``LF:0``, as
``@vitest/coverage-v8`` emits for a TypeScript barrel of ``export interface``
declarations) has nothing to measure. It was stored as ``0.0``, which is
indistinguishable from a file with coverable lines of which none were hit, so
``coverage_gradient`` and ``untested_hotspot`` both fired on a file containing
no executable code at all, and ``fix_first`` ranked it as the repo's runner-up
defect (issue #2193).

NULL is the "not applicable" answer, matching ``branch_coverage_pct`` in the
same table, which has always been nullable and has always reported NULL when
there are no branches. Existing ``0.0`` rows are left alone: a stored zero
comes either from a real uncovered file or from a pre-fix ingest of an empty
one, and nothing recorded at write time can tell them apart after the fact.
The next ``repowise coverage add`` rewrites the table from the report, which
is where the distinction is recovered.

Local SQLite stores never run Alembic. They get the widened nullability from
``init_db``'s model-driven reconciler, which drops NOT NULL on any column the
model has made nullable.

Revision ID: 0066
Revises: 0065
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``batch_alter_table`` rather than a bare ``alter_column``: the whole
    # migration chain runs against SQLite in this repo's own tests
    # (``alembic upgrade head`` on a temp file), and SQLite has no
    # ``ALTER COLUMN``. Batch mode is a no-op reimplementation there and
    # rebuilds the table, carrying the rows, UNIQUE constraint and FK with it.
    with op.batch_alter_table("coverage_files") as batch_op:
        batch_op.alter_column(
            "line_coverage_pct",
            existing_type=sa.Float(),
            nullable=True,
            existing_server_default="0.0",
            server_default=None,
        )


def downgrade() -> None:
    """Restore NOT NULL, defaulting the NULLs the fix introduced.

    A row that is NULL under the new schema is one with no coverable lines, so
    ``0.0`` is the value the old schema would have written for it anyway.
    """
    op.execute("UPDATE coverage_files SET line_coverage_pct = 0.0 WHERE line_coverage_pct IS NULL")
    with op.batch_alter_table("coverage_files") as batch_op:
        batch_op.alter_column(
            "line_coverage_pct",
            existing_type=sa.Float(),
            nullable=False,
            server_default="0.0",
        )
