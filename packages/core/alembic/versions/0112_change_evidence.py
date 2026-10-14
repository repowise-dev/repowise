"""Cached evidence checklist on ``changes``.

``changes.evidence_json`` holds a change's evidence checklist and the index
commit it read; it is built on first read and rebuilt on request.

Local SQLite stores never run Alembic; ``init_db`` adds the column there from
the model.

Revision ID: 0112
Revises: 0111
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0112"
down_revision: str | None = "0111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("changes", sa.Column("evidence_json", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("changes", "evidence_json")
