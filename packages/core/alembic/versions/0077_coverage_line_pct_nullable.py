"""``coverage_files.line_coverage_pct``: let a file with nothing to cover say so.

A report record with no coverable lines (``LF:0`` in lcov, an empty
``<lines>`` in Cobertura or Clover) was ingested as ``0.0``, which downstream
is indistinguishable from a file whose lines were all missed. ``coverage_gradient``
and ``untested_hotspot`` therefore fired on files that contain no executable
code at all — a TypeScript barrel of ``export type`` declarations scored −4.00
and ranked second in ``high_leverage_files`` (issue #2193).

The parsers now answer ``None`` for that case, the way ``branch_coverage_pct``
has always answered ``None`` for "no branches", and this column has to be able
to hold it.

The column only ever held a percentage, so nothing needs rewriting: the rows
that should read NULL are re-derived on the next ingest, which replaces the
whole table for the repository anyway.

**SQLite stores that never run Alembic are not covered by this, by design.**
``init_db``'s reconciler is additive-only, so a store written before this change
keeps the ``NOT NULL``. ``save_coverage_files`` reflects the live column and
omits the not-applicable rows there rather than failing the ingest; an absent
row and a NULL percentage read identically everywhere downstream.

Revision ID: 0077
Revises: 0076
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table so the SQLite path (table rebuild) works too, for the
    # stores that do run migrations; it is a plain ALTER on PostgreSQL.
    with op.batch_alter_table("coverage_files") as batch:
        batch.alter_column(
            "line_coverage_pct",
            existing_type=sa.Float(),
            nullable=True,
            existing_server_default=None,
        )


def downgrade() -> None:
    """Restore ``NOT NULL``, writing the NULLs back as ``0.0``.

    That reintroduces the bug the upgrade fixes — a file with nothing to cover
    reads as 0% covered again — which is what going back to the old schema
    means. The alternative, dropping those rows, would lose the record that the
    file appeared in the report at all.
    """
    op.execute("UPDATE coverage_files SET line_coverage_pct = 0.0 WHERE line_coverage_pct IS NULL")
    with op.batch_alter_table("coverage_files") as batch:
        batch.alter_column(
            "line_coverage_pct",
            existing_type=sa.Float(),
            nullable=False,
            existing_server_default=None,
        )
