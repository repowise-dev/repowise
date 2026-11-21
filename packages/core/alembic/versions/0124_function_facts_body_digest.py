"""Add ``body_digest``, ``body_tokens`` and ``lineage_id`` to ``function_facts``.

``body_digest`` and ``body_tokens`` describe each function's normalized body
(comments, whitespace and its own name dropped), so a later run can recognize
the same body under a new name or path. ``lineage_id`` carries a function's
identity across such moves. All three are nullable: values appear on the next
analysis, and ``lineage_id`` once an update assigns it.

Local SQLite stores get the columns from ``init_db``; this migration covers
managed Postgres.

Revision ID: 0124
Revises: 0123
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0124"
down_revision: str | None = "0123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("body_digest", sa.String(32)),
    ("body_tokens", sa.Integer()),
    ("lineage_id", sa.String(16)),
)


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("function_facts")}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added them on Postgres.
    existing = _existing()
    for name, kind in _COLUMNS:
        if name not in existing:
            op.add_column("function_facts", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    existing = _existing()
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("function_facts", name)
