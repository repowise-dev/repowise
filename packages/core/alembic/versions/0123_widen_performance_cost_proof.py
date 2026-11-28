"""Widen ``performance_opportunities.cost_proof`` to ``VARCHAR(32)`` (PostgreSQL only).

The column was declared ``VARCHAR(16)`` when it held ``proven`` or
``unproven``. It also stores ``background_unproven`` (19 characters), a
scheduled job whose loop is not shown to grow, so PostgreSQL rejected the
insert and the run failed while persisting performance opportunities. SQLite
does not enforce ``VARCHAR`` length, so it never showed there.

Only PostgreSQL is altered: the column already behaves as ``TEXT`` on SQLite,
and ``init_db`` widens it in place on a PostgreSQL database that never ran
Alembic.

Revision ID: 0123
Revises: 0122
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0123"
down_revision: str | None = "0122"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "performance_opportunities",
        "cost_proof",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="proven",
    )


def downgrade() -> None:
    """Narrow the column back. Fails while a row holds ``background_unproven``,
    which is correct: truncating it would store a proof the code never wrote."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "performance_opportunities",
        "cost_proof",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
        existing_server_default="proven",
    )
