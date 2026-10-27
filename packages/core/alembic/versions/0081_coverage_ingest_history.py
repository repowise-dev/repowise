"""coverage_ingests: keep one row per ingest, with its repo-wide figures.

The one-row-per-repository unique constraint is dropped so each ingest adds a
row, and an index on ``(repository_id, ingested_at)`` serves the newest-first
read. ``line_coverage_pct``, ``branch_coverage_pct``, ``covered_lines`` and
``total_lines`` hold what that ingest measured, so the coverage trend reads
one row per report. NULL on rows written before them.

Local SQLite stores never run Alembic: ``init_db``'s reconciler adds the
columns and the index but never drops a constraint, so ``save_coverage_files``
keeps the single-row behaviour on a store that still carries it. Every step is
guarded because the reconciler may have run first.

Revision ID: 0081
Revises: 0080
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "coverage_ingests"
_UNIQUE = "uq_coverage_ingests"
_INDEX = "ix_coverage_ingests_repo_ingested"
_FIGURES = (
    ("line_coverage_pct", sa.Float()),
    ("branch_coverage_pct", sa.Float()),
    ("covered_lines", sa.Integer()),
    ("total_lines", sa.Integer()),
)


def _inspector() -> sa.engine.reflection.Inspector:
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    columns = {c["name"] for c in _inspector().get_columns(_TABLE)}
    for name, type_ in _FIGURES:
        if name not in columns:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))
    uniques = {u["name"] for u in _inspector().get_unique_constraints(_TABLE)}
    if _UNIQUE in uniques:
        # Batch mode rebuilds the table on SQLite, which cannot drop a constraint.
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_UNIQUE, type_="unique")
    if _INDEX not in {i["name"] for i in _inspector().get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["repository_id", "ingested_at"])


def downgrade() -> None:
    if _INDEX in {i["name"] for i in _inspector().get_indexes(_TABLE)}:
        op.drop_index(_INDEX, table_name=_TABLE)
    # Restoring the constraint needs one row per repository: keep the newest.
    op.execute(
        sa.text(
            f"DELETE FROM {_TABLE} WHERE id NOT IN ("
            f"SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
            f"PARTITION BY repository_id ORDER BY ingested_at DESC, id DESC) AS rn "
            f"FROM {_TABLE}) ranked WHERE rn = 1)"
        )
    )
    with op.batch_alter_table(_TABLE) as batch:
        if _UNIQUE not in {u["name"] for u in _inspector().get_unique_constraints(_TABLE)}:
            batch.create_unique_constraint(_UNIQUE, ["repository_id"])
        columns = {c["name"] for c in _inspector().get_columns(_TABLE)}
        for name, _ in _FIGURES:
            if name in columns:
                batch.drop_column(name)
