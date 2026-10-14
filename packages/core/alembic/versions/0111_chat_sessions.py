"""Headless chat sessions on ``conversations`` and send keys on ``chat_messages``.

``conversations`` gains the caller's opaque thread key (``namespace`` plus
``external_ref``, unique together so a thread resumes its conversation), the
session scope, the principal that opened it, the policy frozen at open, and
running token and turn counts. ``chat_messages.idempotency_key`` is unique per
conversation, so a retried send returns the stored reply.

Local SQLite stores never run Alembic; ``init_db`` adds the columns and indexes
there from the model.

Revision ID: 0111
Revises: 0110
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0111"
down_revision: str | None = "0110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("namespace", sa.String(128), nullable=True))
    op.add_column("conversations", sa.Column("external_ref", sa.String(512), nullable=True))
    op.add_column("conversations", sa.Column("scope_json", sa.Text, nullable=True))
    op.add_column("conversations", sa.Column("principal_id", sa.String(256), nullable=True))
    op.add_column("conversations", sa.Column("policy_json", sa.Text, nullable=True))
    for column in ("input_tokens", "output_tokens", "turns"):
        op.add_column(
            "conversations",
            sa.Column(column, sa.Integer, nullable=False, server_default="0"),
        )
    op.create_index(
        "uq_conversations_external_ref",
        "conversations",
        ["namespace", "external_ref"],
        unique=True,
    )
    op.add_column("chat_messages", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.create_index(
        "uq_chat_messages_idempotency_key",
        "chat_messages",
        ["conversation_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_chat_messages_idempotency_key", table_name="chat_messages")
    op.drop_column("chat_messages", "idempotency_key")
    op.drop_index("uq_conversations_external_ref", table_name="conversations")
    for column in (
        "turns",
        "output_tokens",
        "input_tokens",
        "policy_json",
        "principal_id",
        "scope_json",
        "external_ref",
        "namespace",
    ):
        op.drop_column("conversations", column)
