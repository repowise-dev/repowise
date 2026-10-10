"""graph_nodes: why a test file runs with every selected subset.

``always_run_reason`` is set at index time on a test file that lists and reads
files under a source directory, or runs the project's own command or module in
a child process. The dependency graph cannot say what such a test exercises,
so test selection runs it with every subset. NULL everywhere else; rows stored
before the column fill in on the next index or update.

Local SQLite stores get the column from ``init_db``'s additive reconciler; this
migration covers managed Postgres.

Revision ID: 0097
Revises: 0096
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("graph_nodes", sa.Column("always_run_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("graph_nodes", "always_run_reason")
