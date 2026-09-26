"""Add decision_records.kind, splitting agreements from architectural decisions.

A working agreement ("never commit to main") and an architectural decision
("get_why never calls an LLM") were one noun. They are not the same object: a
diff can violate the second and can never violate the first, which is why an
agreement could not satisfy the acceptance contract's scope requirement at all.

Additive, and ``architectural`` for every existing row, so a store that has not
been reclassified behaves exactly as it did. Classification happens in the
runtime migration rather than here, because a data fix that lives in a
migration and a data fix that lives in the code eventually disagree, and only
one of them runs on an existing store.

Revision ID: 0067
Revises: 0066
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_records",
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            server_default="architectural",
        ),
    )


def downgrade() -> None:
    op.drop_column("decision_records", "kind")
