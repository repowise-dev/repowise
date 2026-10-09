"""Add pinning and recoverable deletion to chat conversations.

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("conversations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_conversations_repo_pinned_updated", "conversations", ["repository_id", "pinned", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_conversations_repo_pinned_updated", table_name="conversations")
    op.drop_column("conversations", "deleted_at")
    op.drop_column("conversations", "pinned")
