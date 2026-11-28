"""Add to ``external_call_sites`` the arguments a call passes and where its name sits.

``argument_count`` selects one of several overloads of the called name.
``name_line`` and ``name_column`` place the called name, which may be on a
later line than the call's receiver. Local SQLite stores get the columns from
``init_db``; this migration covers managed Postgres. Values appear on the next
graph build of each repository.

Revision ID: 0156
Revises: 0155
Create Date: 2026-11-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0156"
down_revision: str | None = "0155"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("external_call_sites")}


_COLUMNS = ("argument_count", "name_line", "name_column")


def upgrade() -> None:
    # A store the startup reconciler already reached holds the columns.
    existing = _existing()
    for name in _COLUMNS:
        if name not in existing:
            op.add_column("external_call_sites", sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    existing = _existing()
    with op.batch_alter_table("external_call_sites") as batch:
        for name in reversed(_COLUMNS):
            if name in existing:
                batch.drop_column(name)
