"""Add external_systems.io_kind: the I/O-boundary type.

Tags each third-party dependency by the *kind* of side effect it performs at a
process boundary: ``io_kind in {db, network, filesystem, subprocess, lock}``.
Nullable and backward-compatible: a NULL ``io_kind`` means "untyped" and every
consumer (the C4 architecture view today; a future perf / security layer)
degrades gracefully. Populated by
``repowise.core.ingestion.external_systems.io_kind``.

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-20

NOTE: if a parallel branch lands a migration on top of 0031 before this merges,
rebase ``down_revision`` onto that revision so the chain stays linear.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_systems") as batch_op:
        batch_op.add_column(sa.Column("io_kind", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("external_systems") as batch_op:
        batch_op.drop_column("io_kind")
