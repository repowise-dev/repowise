"""coverage_ingests.scope_json: what each ingest measured, for comparing two.

A project coverage delta compares the ingest at a change's base with the one at
its head, and that is only honest when both read the same kind of reports with
the same ``coverage.ignore``. ``scope_json`` records it per ingest. NULL on rows
written before it: "scope unknown", which the delta reports as incomparable.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0084
Revises: 0083
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "coverage_ingests"
_COLUMN = "scope_json"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added it on Postgres.
    if _COLUMN not in _columns():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns():
        op.drop_column(_TABLE, _COLUMN)
