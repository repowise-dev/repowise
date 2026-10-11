"""Allow ``dead_code_findings.lines`` to be NULL (PostgreSQL only).

The analyzer used to estimate a file's lines as ``symbol_count * 10``. It now
counts them, and a count it cannot make is stored as NULL rather than guessed,
so the column must accept NULL.

Only PostgreSQL is altered: local SQLite stores never run Alembic
(``init_db``'s reconciler is additive-only), and new SQLite stores get the
nullable column from the model.

Revision ID: 0086
Revises: 0085
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "dead_code_findings",
        "lines",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    """Restore NOT NULL. Unknown counts become 0, which is what the old
    schema could express."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("UPDATE dead_code_findings SET lines = 0 WHERE lines IS NULL")
    op.alter_column(
        "dead_code_findings",
        "lines",
        existing_type=sa.Integer(),
        nullable=False,
    )
