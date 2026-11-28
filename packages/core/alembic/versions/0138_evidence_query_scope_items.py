"""Query counts record how many items their scope ran over.

One nullable column on ``evidence_query_counts``: the input size of the test
or request a count belongs to, so two scopes can show a call site's
statements growing with it or staying flat. Local SQLite stores get it from
the model through the reconciler.

Revision ID: 0138
Revises: 0137
Create Date: 2026-11-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0138"
down_revision: str | None = "0137"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("evidence_query_counts")}


def upgrade() -> None:
    if "scope_items" not in _existing():
        op.add_column("evidence_query_counts", sa.Column("scope_items", sa.Integer, nullable=True))


def downgrade() -> None:
    if "scope_items" in _existing():
        with op.batch_alter_table("evidence_query_counts") as batch:
            batch.drop_column("scope_items")
