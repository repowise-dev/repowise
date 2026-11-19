"""Symbol hotness basis: the columns the derived ``symbol_hotness`` rows state.

Nullable columns only: the run's sample count and unlisted count on ``evidence_runs``, the body
digest at the commit measured on ``evidence_symbol_costs``, and the basis of
each ``symbol_hotness`` row (run, rank, samples, share interval, explained
share, unlisted count, clock, ordering-only flag, freshness). Local SQLite stores get them
from the models through the reconciler.

Revision ID: 0131
Revises: 0130
Create Date: 2026-11-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0131"
down_revision: str | None = "0130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> dict[str, tuple[sa.Column, ...]]:
    """Fresh column objects per call: one is bound to its table when added."""
    return {
        "evidence_runs": (
            sa.Column("total_samples", sa.Integer, nullable=True),
            sa.Column("unlisted_symbols", sa.Integer, nullable=True),
        ),
        "evidence_symbol_costs": (sa.Column("body_digest", sa.String(32), nullable=True),),
        "symbol_hotness": (
            sa.Column("run_id", sa.String(32), nullable=True),
            sa.Column("rank", sa.Integer, nullable=True),
            sa.Column("samples", sa.Integer, nullable=True),
            sa.Column("run_samples", sa.Integer, nullable=True),
            sa.Column("share_low", sa.Float, nullable=True),
            sa.Column("share_high", sa.Float, nullable=True),
            sa.Column("explained_share", sa.Float, nullable=True),
            sa.Column("run_unlisted", sa.Integer, nullable=True),
            sa.Column("value_kind", sa.String(16), nullable=True),
            sa.Column("ordering_only", sa.Boolean, nullable=True),
            sa.Column("freshness", sa.String(16), nullable=True),
        ),
    }


def _existing(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, columns in _columns().items():
        have = _existing(table)
        for column in columns:
            if column.name not in have:
                op.add_column(table, column)


def downgrade() -> None:
    for table, columns in _columns().items():
        have = _existing(table)
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                if column.name in have:
                    batch.drop_column(column.name)
