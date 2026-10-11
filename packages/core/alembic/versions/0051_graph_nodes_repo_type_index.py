"""Index ``graph_nodes`` on ``(repository_id, node_type)``.

``node_type == "file"`` is the most-issued predicate on this table — the health
dashboard's language map and test-path set, the overview and stats routes, the
C4 builder, the answer pipeline's PageRank lookups — and nothing covered it. The
only index on the pair was the one behind ``uq_graph_node``, keyed
``(repository_id, node_id)``, so a ``node_type``-filtered read seeked on the
repository and then filtered the rest in memory: on the repowise index that is
36,480 rows scanned to return 3,449.

Measured there for the two reads ``get_health`` issues per dashboard call:
the language map 29.0ms -> 9.2ms and the test-path set 27.2ms -> 8.4ms.

The LIMIT-without-ORDER-BY hazard 0046 records applies to any new index and was
audited here: every ``node_type``-filtered query in the tree that carries a
``LIMIT`` also carries an ``ORDER BY`` (pagerank or in_degree), so no answer can
change with the plan.

Declared in ``GraphNode.__table_args__`` too. Local stores are created by
``init_db`` (whose additive reconciler back-fills declared indexes) and never
run Alembic, while hosted only ever sees Alembic, so both declarations are
needed and they converge on the same index. Same split as 0045 and 0046.

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_graph_nodes_repo_type"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "graph_nodes",
        ["repository_id", "node_type"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="graph_nodes", if_exists=True)
