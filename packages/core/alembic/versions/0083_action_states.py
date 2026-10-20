"""Action states: a person's dismiss, snooze, or done on a next action.

Next actions are computed on read, so this is the only stored half. Local
SQLite stores never run Alembic -- ``init_db``'s reconciler creates the table --
so this migration exists for managed Postgres and is guarded for that reason.

Revision ID: 0083
Revises: 0082
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("action_states"):
        return
    op.create_table(
        "action_states",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_id", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False, server_default=""),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "action_id", name="uq_action_states"),
    )


def downgrade() -> None:
    op.drop_table("action_states")
