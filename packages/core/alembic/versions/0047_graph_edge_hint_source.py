"""Persist ``graph_edges.hint_source`` so cohesion survives rehydration.

Several resolver passes synthesise file-level edges to record that two files
belong to one compilation unit — Go package siblings, JVM same-package classes,
C# ``partial`` fragments and global usings, Swift same-module files, and the
C/C++ header/implementation pair. Each pass stamps ``hint_source`` on the edge.

Cycle detection reads that stamp to drop those edges: files in one compilation
unit cannot depend on each other, so counting them turns every cohesive package
into a fabricated import cycle (issue #1294).

The stamp only lived in the in-memory NetworkX graph. Anything running against a
graph rehydrated from ``graph_edges`` — the health engine's break-cycle detector,
incremental updates — saw cohesion edges as ordinary imports and re-reported the
false cycles. Persisting the column closes that gap.

Nullable with no default: NULL means "a real import/using directive", which is
the correct reading for every pre-existing row.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones. The two converge on the same shape.

Renumbered 0046 -> 0047. This landed as ``0046`` at the same time as the
``wiki_symbols`` index migration, so the directory carried **two** revisions
declaring ``revision = "0046"`` and ``down_revision = "0045"``. Alembic reported
``heads: ['0046', '0046']`` behind a ``Revision 0046 is present more than once``
warning, which makes the chain unusable — one of the two is unreachable and no
further revision can name a single parent. Renumbering is safe precisely because
the chain was broken: nothing could run to a well-defined head while it was.

Because it is ambiguous which of the two a database that already reports
``0046`` actually applied, this revision repairs **both** halves of that
ambiguity and each step is idempotent:

* If the store ran *this* migration as ``0046``, ``hint_source`` is already
  there and the column step is skipped.
* If it ran the ``wiki_symbols`` one as ``0046``, then ``0046`` is marked
  applied and ``ix_wiki_symbols_repo_path`` would **never** be created — the
  index would be permanently missing, silently, and every file-scoped symbol
  lookup would keep the scan plan that migration exists to remove. So this one
  also creates that index ``if_not_exists``.

A store that got both is unaffected; a store that got neither gets both.

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SYMBOL_INDEX = "ix_wiki_symbols_repo_path"


def _has_hint_source() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == "hint_source" for c in inspector.get_columns("graph_edges"))


def upgrade() -> None:
    if not _has_hint_source():
        op.add_column(
            "graph_edges",
            sa.Column("hint_source", sa.String(length=64), nullable=True),
        )
    # The other half of the duplicate-0046 repair; see the note above.
    op.create_index(
        _SYMBOL_INDEX,
        "wiki_symbols",
        ["repository_id", "file_path"],
        if_not_exists=True,
    )


def downgrade() -> None:
    # The index is left in place: 0046 owns it, and dropping it here would
    # take it away from a store that legitimately ran 0046.
    if _has_hint_source():
        op.drop_column("graph_edges", "hint_source")
