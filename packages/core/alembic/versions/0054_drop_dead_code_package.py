"""Drop ``dead_code_findings.package``.

It was ``Path(file_path).parts[0]`` — the path's own first segment, equal to it
on 445/445 findings on this repo's index — and no schema, router, MCP tool or
component ever read it back. Nothing is lost that ``file_path`` does not
already show; for a zombie-package finding ``file_path`` *is* the package.

``commit_count_90d`` was audited alongside it and deliberately kept: it is NOT
NULL, and local SQLite stores never run Alembic (``init_db``'s reconciler is
additive-only), so removing it from the model would fail every insert against
an index already on disk. It is surfaced on the response instead.

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("dead_code_findings", "package")


def downgrade() -> None:
    op.add_column(
        "dead_code_findings",
        sa.Column("package", sa.String(length=255), nullable=True),
    )
