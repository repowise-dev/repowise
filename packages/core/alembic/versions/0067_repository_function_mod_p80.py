"""repositories: store the full-repo function-mod p80.

``function_hotspot`` gates on the repo-wide 80th percentile of per-function
modification counts. A full index computes that over every walked function; an
incremental update cannot, because it walks the changed files alone, so it
reads a stored value back instead (issue #1484).

The only stored repo-wide source until now was the ``git_function_blame``
rollup, read back by ``load_stored_function_mod_p80``. That table is keyed
``(repository_id, symbol_id)`` with ``symbol_id = "{path}::{name}"``, so every
same-named function in a file collapses to a single row: the percentile was
taken over a population missing those samples, and the surviving sample is
whichever occurrence the walker emitted last rather than a max or a sum. The
gate therefore still moved between ``init`` and ``update`` -- the flip #1484
exists to prevent, arriving through a different door.

This column holds the number the full index actually measured. Nullable, so a
store written before this migration keeps working: the reader falls back to the
rollup percentile until the next full index fills it in.

Local SQLite stores never run Alembic -- ``init_db``'s reconciler issues
additive DDL for missing columns, and an added nullable column is exactly what
it handles -- so this migration exists for managed Postgres.

Revision ID: 0067
Revises: 0066
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("function_mod_p80", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "function_mod_p80")
