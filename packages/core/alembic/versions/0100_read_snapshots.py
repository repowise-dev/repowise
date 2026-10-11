"""read_snapshots: the Fix first queue and the next-actions view, built at index time.

Both were built on the first read of every process: on a 21k-file repository
the next-actions view cost 4.6 s of file facts and 3.7 s of Fix first before
the first row printed. Index and update now store each as one row keyed by
the code that built it; a write to any store a view reads deletes the rows in
the same transaction, and a reader with no row builds live. Derived state: an
existing store upgrades empty and fills on its next index or update.

Local SQLite stores get the table from ``init_db``'s ``create_all``; this
migration covers managed Postgres.

Revision ID: 0100
Revises: 0099
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0100"
down_revision: str | None = "0099"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "read_snapshots",
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("read_snapshots")
