"""A person's own words outrank a write-up of them.

``SOURCE_RANK`` put ``adr`` (8) above ``session`` (7), and the headline-promotion
branch in ``crud.decisions`` compares with ``>=``, so a transcript could never
take the headline from a mined document: 7 >= 8 is false. The effect was that a
document overwrote what a user had actually said about their own choice. The two
ranks are swapped, and because only those two values trade places the *set* of
ranks is unchanged, so ``MAX_SOURCE_RANK`` stays 9 and no other source's
confidence moves.

``decision_evidence.source_rank`` is stamped once at insert time and read
straight from the column by two ``ORDER BY`` clauses, so it cannot be derived on
read: the ladder is a value copied into every row. Without this backfill a store
would hold rows on both ladders at once and ``bulk_upsert_decisions`` would
derive headline confidence from ``max(source_rank)`` across the mixture, which
looks like a working store and is not one.

Confidence is left to the application to re-derive. ``reconcile_source_ranks``
recomputes it from the full evidence set using the same expression as the upsert
path, and runs on the persist path that every store takes — including the local
SQLite stores that ``init_db`` creates and that never see Alembic at all. Both
paths are idempotent and converge on the same values; this migration exists so a
hosted Postgres store is correct before the application next writes to it.

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE decision_evidence SET source_rank = 8 WHERE lower(source) = 'session'")
    op.execute("UPDATE decision_evidence SET source_rank = 7 WHERE lower(source) = 'adr'")


def downgrade() -> None:
    op.execute("UPDATE decision_evidence SET source_rank = 7 WHERE lower(source) = 'session'")
    op.execute("UPDATE decision_evidence SET source_rank = 8 WHERE lower(source) = 'adr'")
