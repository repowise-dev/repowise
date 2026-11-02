"""Allow ``health_file_metrics`` score columns to be NULL (PostgreSQL only).

A file whose language health has no dialect for is never walked, so it is now
stored with no ``score``, ``max_ccn`` or ``max_nesting`` instead of a 10.0 and
a complexity of 1 that nothing measured.

Local SQLite stores never run Alembic; ``init_db`` rebuilds that table there
when the columns are still NOT NULL, and new stores get them from the model.

Revision ID: 0094
Revises: 0093
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (("score", sa.Float()), ("max_ccn", sa.Integer()), ("max_nesting", sa.Integer()))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, kind in _COLUMNS:
        op.alter_column("health_file_metrics", name, existing_type=kind, nullable=True)


def downgrade() -> None:
    """Restore NOT NULL with the values the old schema wrote for such files."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("UPDATE health_file_metrics SET score = 10.0 WHERE score IS NULL")
    op.execute("UPDATE health_file_metrics SET max_ccn = 1 WHERE max_ccn IS NULL")
    op.execute("UPDATE health_file_metrics SET max_nesting = 0 WHERE max_nesting IS NULL")
    for name, kind in _COLUMNS:
        op.alter_column("health_file_metrics", name, existing_type=kind, nullable=False)
