"""Workload hotness: the ranked functions of each workload's newest profile run.

A new derived table, one row per (run, function), rebuilt whole with
``symbol_hotness``. Local SQLite stores get it from the models through
``init_db``; neither path can change a key later, so the key here is final.

Revision ID: 0135
Revises: 0134
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0135"
down_revision: str | None = "0134"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workload_hotness"


def upgrade() -> None:
    # init_db may already have created it through create_all.
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("run_id", sa.String(32), primary_key=True),
        sa.Column("symbol_id", sa.Text, primary_key=True),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("total_share", sa.Float, nullable=True),
        sa.Column("self_share", sa.Float, nullable=True),
        sa.Column("share_low", sa.Float, nullable=True),
        sa.Column("share_high", sa.Float, nullable=True),
        sa.Column("samples", sa.Integer, nullable=True),
        sa.Column("freshness", sa.String(16), nullable=False),
        sa.Column("ordering_only", sa.Boolean, nullable=False),
        sqlite_with_rowid=False,
    )


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(_TABLE)
