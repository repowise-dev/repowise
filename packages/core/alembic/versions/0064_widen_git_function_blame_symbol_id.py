"""Widen git_function_blame.symbol_id to ``Text`` (PostgreSQL only).

``symbol_id`` stores ``"{path}::{name}"`` and mirrors ``WikiSymbol.symbol_id``,
which is unbounded ``Text`` — but this column was declared ``VARCHAR(512)``.
A deeply-nested generated file (protobuf/OpenAPI bindings, minified bundles)
routinely produces a ``path::name`` combination past 512 characters, and
PostgreSQL enforces the declared length where SQLite ignores it. So the
narrow type is invisible on the default backend and aborts the run on the one
the architecture docs recommend for production, in the persistence phase,
after the whole blame index has already been computed (issue #2162).

Same class of bug as #1565, fixed for six other columns by
``0062_widen_symbol_name_columns.py``. This column predates that migration —
``git_function_blame`` was created in ``0029_git_function_blame.py`` — but
``0062`` targeted columns holding a symbol *name* at ``VARCHAR(255)``, and
this one holds a symbol *id* at ``VARCHAR(512)``, so it fell outside the
pattern that migration was matching.

This is the last bounded symbol-id-shaped column in ``models.py``, so this
closes the class rather than starting another sweep.

Only PostgreSQL is altered. SQLite does not enforce ``VARCHAR`` length — the
column already behaves as ``TEXT`` there — and local SQLite stores never run
Alembic anyway (``init_db``'s reconciler is additive-only), so a
``batch_alter_table`` would rebuild the table to change nothing.

Revision ID: 0064
Revises: 0063
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "git_function_blame",
        "symbol_id",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Narrow the column back.

    This fails on a database that already stores a value longer than 512
    characters, which is the correct outcome: casting with ``left(col, 512)``
    would silently destroy the key the row is about in order to make the
    downgrade look clean.
    """
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "git_function_blame",
        "symbol_id",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
