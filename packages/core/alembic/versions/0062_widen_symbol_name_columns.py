"""Widen the symbol-name columns to ``Text`` (PostgreSQL only).

Six columns store a symbol name, or a label built from one, under
``VARCHAR(255)``. A symbol name has no length bound — generated code
(protobuf/OpenAPI bindings, minified bundles) routinely emits identifiers past
255 characters — and PostgreSQL enforces the declared length where SQLite
ignores it. So the narrow type is invisible on the default backend and aborts
the run on the one the architecture docs recommend for production, in the
persistence phase, after the entire index has been computed (issue #1565).

Reproduced on a repository with one 365-character function name:
``StringDataRightTruncationError`` on ``INSERT INTO graph_nodes``.

Only PostgreSQL is altered. SQLite does not enforce ``VARCHAR`` length — the
columns already behave as ``TEXT`` there — and local SQLite stores never run
Alembic anyway (``init_db``'s reconciler is additive-only), so a
``batch_alter_table`` would rebuild six tables to change nothing.

Revision ID: 0062
Revises: 0061
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, nullable) — nullability is preserved, only the type changes.
_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("graph_nodes", "name", True),
    ("wiki_symbols", "name", False),
    ("wiki_symbols", "parent_name", True),
    ("dead_code_findings", "symbol_name", True),
    ("health_findings", "function_name", True),
    ("refactoring_suggestions", "target_symbol", False),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    """Narrow the columns back.

    This fails on a database that already stores a value longer than 255
    characters, which is the correct outcome: casting with ``left(col, 255)``
    would silently destroy the name the row is about in order to make the
    downgrade look clean.
    """
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=nullable,
        )
