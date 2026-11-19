"""Evidence time split: where a profile run's time went.

Nullable columns only: on ``evidence_runs`` the run's value in production repo
code itself, blocked in waits and in libraries (``evidence.profile_convert``);
on ``symbol_hotness`` the same three as shares of the basis run. Local SQLite
stores get them from the models through the reconciler.

Revision ID: 0134
Revises: 0133
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0134"
down_revision: str | None = "0133"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> dict[str, tuple[sa.Column, ...]]:
    """Fresh column objects per call: one is bound to its table when added."""
    return {
        "evidence_runs": (
            sa.Column("repo_self_value", sa.Float, nullable=True),
            sa.Column("wait_value", sa.Float, nullable=True),
            sa.Column("library_value", sa.Float, nullable=True),
        ),
        "symbol_hotness": (
            sa.Column("repo_self_share", sa.Float, nullable=True),
            sa.Column("wait_share", sa.Float, nullable=True),
            sa.Column("library_share", sa.Float, nullable=True),
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
