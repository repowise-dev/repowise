"""decision_records: whether a record's rule still holds once its files moved.

Three nullable columns: the verdict (holds, drifted, unknown), its basis and
the commit it was read at. NULL means not checked yet, which keeps the churn
reading; the next index fills them. Local SQLite stores get them from the
model through the reconciler.

Revision ID: 0137
Revises: 0136
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0137"
down_revision: str | None = "0136"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("rule_check", sa.String(16)),
    ("rule_check_basis", sa.Text()),
    ("rule_checked_commit", sa.String(64)),
)


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("decision_records")}


def upgrade() -> None:
    existing = _existing()
    for name, kind in _COLUMNS:
        if name not in existing:
            op.add_column("decision_records", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    existing = _existing()
    with op.batch_alter_table("decision_records") as batch:
        for name, _kind in reversed(_COLUMNS):
            if name in existing:
                batch.drop_column(name)
