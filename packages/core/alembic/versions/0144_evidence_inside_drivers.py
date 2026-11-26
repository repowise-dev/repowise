"""Measured functions keep what their time went to and what drove it.

Nullable columns only: ``inside_json`` and ``drivers_json`` on
``evidence_symbol_costs`` (written at ingest from the profile's stacks) and on
``symbol_hotness`` (copied from the basis run). Local SQLite stores get them
from the models through the reconciler.

Revision ID: 0144
Revises: 0143
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0144"
down_revision: str | None = "0143"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("evidence_symbol_costs", "symbol_hotness")
_COLUMNS = ("inside_json", "drivers_json")


def _existing(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in _TABLES:
        have = _existing(table)
        for name in _COLUMNS:
            if name not in have:
                op.add_column(table, sa.Column(name, sa.Text, nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        have = _existing(table)
        with op.batch_alter_table(table) as batch:
            for name in reversed(_COLUMNS):
                if name in have:
                    batch.drop_column(name)
