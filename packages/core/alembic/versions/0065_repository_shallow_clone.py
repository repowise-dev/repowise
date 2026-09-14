"""Record whether the indexed checkout was a shallow clone.

Every whole-history figure on ``repositories`` (commit count, first commit date,
contributor count, lifetime churn) is capped at the graft point on a shallow
clone, and nothing said so, so a truncated history was reported in the same
shape as a complete one. A typed column rather than ``settings_json`` because
this is captured at index time, not user configuration. See
``Repository.is_shallow_clone`` for why it is nullable rather than a defaulted
boolean.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones.

Revision ID: 0065
Revises: 0064
Create Date: 2026-09-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("is_shallow_clone", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "is_shallow_clone")
