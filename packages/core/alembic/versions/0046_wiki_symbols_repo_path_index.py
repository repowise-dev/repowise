"""Index ``wiki_symbols`` on ``(repository_id, file_path)``.

Every file-scoped symbol lookup joins on this pair, but the only index covering
it was the one behind ``uq_wiki_symbol``, keyed ``(repository_id, symbol_id)``.
A query filtering by ``file_path`` could therefore seek on ``repository_id``
alone and then filter the rest in memory — on the repowise index that is 28,175
rows scanned to return 6,937.

Measured there for a 400-path ``IN`` (the shape ``_attach_symbol_ids`` issues
when the health dashboard resolves findings to symbols): 33.3ms -> 11.7ms, with
the plan changing from ``SEARCH ... USING INDEX sqlite_autoindex_wiki_symbols_2
(repository_id=?)`` to a keyed seek on the new index, returning the same rows.

One caveat worth recording, because it is a property of adding *any* index
here. A query with a ``LIMIT`` and no ``ORDER BY`` returns whichever rows the
chosen index reaches first, so changing which index the planner picks changes
the answer. ``augment_cmd``'s symbol rescue had two such queries; both now
order by ``(file_path, name)``, which is what the ``uq_wiki_symbol`` autoindex
was giving them implicitly.

Declared in ``WikiSymbol.__table_args__`` too. Local stores are created by
``init_db`` and never run Alembic, while hosted only ever sees Alembic, so both
declarations are needed and they converge on the same index. Same split as the
``health_findings`` indexes in 0045.

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_wiki_symbols_repo_path"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "wiki_symbols",
        ["repository_id", "file_path"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="wiki_symbols", if_exists=True)
