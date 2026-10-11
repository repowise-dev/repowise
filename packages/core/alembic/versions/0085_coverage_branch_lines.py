"""coverage_files.branch_lines_json: branches per line, for changed-line branch coverage.

``branch_lines_json`` holds ``{"line": [taken, total]}`` for the lines a report
gives branch counts for, so patch coverage can report partly taken branches on
changed lines. NULL when the report carried no per-line branch data (or the row
predates the column).

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0085
Revises: 0084
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "coverage_files"
_COLUMN = "branch_lines_json"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added it on Postgres.
    if _COLUMN not in _columns():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns():
        op.drop_column(_TABLE, _COLUMN)
