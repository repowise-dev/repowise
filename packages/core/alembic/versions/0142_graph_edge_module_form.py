"""Persist ``graph_edges.module_form`` so static-only import cycles stay labelled.

A Python ``import b`` whose name no module-level code mentions does not fail at
import inside a cycle; ``from b import B`` does. A cycle made only of the first
kind is reported as static only, and a graph rehydrated from SQL must keep the
mark.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0142
Revises: 0141
Create Date: 2026-11-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0142"
down_revision: str | None = "0141"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "graph_edges"
_COLUMN = "module_form"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    # init_db may already have added it through create_all.
    if _COLUMN not in _columns():
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    if _COLUMN in _columns():
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
