"""Index ``wiki_symbols`` and ``graph_nodes`` on ``(repository_id, name)``.

Neither table had an index that leads with ``name`` after ``repository_id``,
so exact name lookups (e.g. in MCP resolution, call graphs, callers, pointers,
flows) scanned every row for the target repository.

Revision ID: 0124
Revises: 0115
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0124"
down_revision: str | None = "0123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WIKI_SYMBOLS_INDEX = "ix_wiki_symbols_repo_name"
_GRAPH_NODES_INDEX = "ix_graph_nodes_repo_name"


def upgrade() -> None:
    op.create_index(
        _WIKI_SYMBOLS_INDEX,
        "wiki_symbols",
        ["repository_id", "name"],
        if_not_exists=True,
    )
    op.create_index(
        _GRAPH_NODES_INDEX,
        "graph_nodes",
        ["repository_id", "name"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(_GRAPH_NODES_INDEX, table_name="graph_nodes", if_exists=True)
    op.drop_index(_WIKI_SYMBOLS_INDEX, table_name="wiki_symbols", if_exists=True)
