"""Record who dismissed a candidate, or asked for it to be split.

``decision_candidate_meta.dismissed_by``, ``dismissed_by_kind`` and
``dismissed_session`` sign ``dismissed_reason`` the way an acceptance row is
signed. On the candidate's review row, because an acceptance row would make a
never-accepted candidate a decision. Nullable: null means "not recorded".

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns -- so this migration exists for managed
Postgres.

Revision ID: 0118
Revises: 0117
Create Date: 2026-10-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0118"
down_revision: str | None = "0117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "decision_candidate_meta"
_COLUMNS = (
    ("dismissed_by", sa.Text()),
    ("dismissed_by_kind", sa.String(16)),
    ("dismissed_session", sa.String(64)),
)


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added them on Postgres.
    existing = _columns()
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    existing = _columns()
    for name, _type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
