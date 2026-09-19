"""decision_records: pin the quote a decision's identity is keyed on.

A decision's id derives from its identity, and identity is now the evidence
rather than the title: the files it governs, the span it was read from, and
the quote it was captured with. The quote is the part a re-extraction can
change without the decision changing at all -- a model rewords the same
sentence and, with the quote re-derived each run, the id moves and every
reference held outside the row stops resolving.

So it is written once, at first capture, and never revised. This column is
where it lives. Empty on a record captured before it existed; the runtime id
migration backfills those from the strongest evidence row the record already
holds, which is the span it would have pinned had the column been there.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns, and an added non-nullable column with a
server default is exactly what it handles -- so this migration exists for
managed Postgres.

Revision ID: 0069
Revises: 0068
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_records",
        sa.Column(
            "identity_quote",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("decision_records", "identity_quote")
