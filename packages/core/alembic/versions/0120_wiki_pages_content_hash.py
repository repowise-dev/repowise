"""Add content_hash to wiki_pages.

The cross-run reuse key for a model-written page: a stable digest of the
page's subject (file bytes, group membership, repo name) folded with a
renderer fingerprint. Unlike ``source_hash`` it survives RAG-context drift
between runs, so an unchanged page is reused instead of re-billed on every
full run — the gap that made a provider-outage-interrupted run impossible to
top up cheaply (issue #1089). Existing rows default to empty, which the
reuse gate treats as "no subject key yet" and falls back to the prompt hash.

Revision ID: 0120
Revises: 0118
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0120"
down_revision: str | None = "0118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("wiki_pages")}


def upgrade() -> None:
    # Guarded: ``init_db``'s reconciler may already have added it on Postgres.
    if "content_hash" not in _columns():
        op.add_column(
            "wiki_pages",
            sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if "content_hash" in _columns():
        op.drop_column("wiki_pages", "content_hash")
