"""Add the ``symbol_lineage_events`` table.

One row per lineage decision a run makes when it rewrites ``function_facts``:
a function carried across a file rename, a move or a rename, or a ``broken``
lineage with the reason no candidate was taken. Newest 5,000 per repository
are kept.

Local SQLite stores get the table from ``init_db``; this migration covers
managed Postgres.

Revision ID: 0126
Revises: 0125
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0126"
down_revision: str | None = "0125"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "symbol_lineage_events"


def _exists() -> bool:
    return _TABLE in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # init_db may already have created it through its additive reconciler.
    if _exists():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lineage_id", sa.String(16), nullable=False),
        sa.Column("from_symbol_id", sa.Text(), nullable=False),
        sa.Column("to_symbol_id", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(24), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_symbol_lineage_events_repo_from",
        _TABLE,
        ["repository_id", "from_symbol_id"],
    )


def downgrade() -> None:
    if _exists():
        op.drop_index("ix_symbol_lineage_events_repo_from", table_name=_TABLE)
        op.drop_table(_TABLE)
