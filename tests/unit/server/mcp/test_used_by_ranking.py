"""``get_context``'s ``used_by`` keeps the most central users, not the first 20.

The field is cut at ``_MAX_USED_BY`` and the agent is never told what fell off,
so which twenty survive is the whole of what this list says. Unordered, they
were whichever rows the table handed back. On the 42-index corpus 4,743 symbol
targets have more users than the cap and ranking moves the kept set on 4,447 of
them, a median of 7 of the 20.

The fixture is deliberately larger than the cap — this defect does not exist
below it, and the repo's other ``used_by`` coverage runs on two edges, which is
why nothing caught this.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.mcp_server.tool_context.targets import _MAX_USED_BY, _resolve_one_target

_FILE = "src/auth/service.py"
_NOISE = 25


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


@pytest.fixture
async def many_users(session, populated_db) -> str:
    """``_NOISE`` peripheral importers, then one central one, added last.

    ``zz_`` is load-bearing. The first cut of this fixture called the central
    file ``src/app/hub.py`` and **passed against the unranked code** — SQLite
    serves this join through the ``(repository_id, node_id)`` unique index, so
    the "unordered" query comes back in path order and a hub whose path sorts
    early survives a cut that is not ranking anything. Named to sort last, and
    inserted last, so neither path order nor insertion order can keep it: only
    the ranking can. (That also says what the old behaviour was — alphabetical
    on SQLite, nothing promised on Postgres — which is not the same as random,
    and is still the wrong list.)
    """
    rid = populated_db
    for i in range(_NOISE):
        path = f"src/leaf/leaf{i:03d}.py"
        session.add(
            GraphNode(
                id=f"gn-leaf-{i}",
                repository_id=rid,
                node_id=path,
                node_type="file",
                language="python",
                symbol_count=1,
                is_test=False,
                is_entry_point=False,
                # Rising with the name, so path order and rank order disagree
                # on every pair. Equal ranks would let an alphabetical list
                # satisfy "descending by PageRank" for free.
                pagerank=0.001 * (i + 1),
                betweenness=0.0,
                community_id=1,
            )
        )
        session.add(
            GraphEdge(
                id=f"ge-leaf-{i}",
                repository_id=rid,
                source_node_id=path,
                target_node_id=_FILE,
                edge_type="imports",
                imported_names_json='["AuthService"]',
            )
        )
    session.add(
        GraphNode(
            id="gn-hub",
            repository_id=rid,
            node_id="src/zz_hub.py",
            node_type="file",
            language="python",
            symbol_count=9,
            is_test=False,
            is_entry_point=True,
            pagerank=0.9,
            betweenness=0.5,
            community_id=1,
        )
    )
    session.add(
        GraphEdge(
            id="ge-hub",
            repository_id=rid,
            source_node_id="src/zz_hub.py",
            target_node_id=_FILE,
            edge_type="imports",
            imported_names_json='["AuthService"]',
        )
    )
    await session.commit()
    return rid


async def _card(session, repository, target: str) -> dict:
    return await _resolve_one_target(
        session, repository, target, None, True, exclude_spec=None, repo_root=None
    )


async def test_the_most_central_user_survives_the_cut(session, repository, many_users) -> None:
    docs = (await _card(session, repository, "AuthService"))["docs"]
    used_by = docs["used_by"]

    assert len(used_by) == _MAX_USED_BY
    assert used_by[0] == "src/zz_hub.py"
    assert docs["used_by_total"] == _NOISE + 3
    assert docs["used_by_emitted"] == _MAX_USED_BY
    assert docs["used_by_reduced_reason"] == "construction_cap"


async def test_used_by_is_ordered_by_centrality(session, repository, many_users) -> None:
    """Not just "the hub is in there" — the whole list is a ranking."""
    used_by = (await _card(session, repository, "AuthService"))["docs"]["used_by"]

    ranks = {}
    for path in used_by:
        node = await session.execute(
            GraphNode.__table__.select().where(GraphNode.node_id == path)
        )
        row = node.first()
        ranks[path] = row.pagerank if row else 0.0
    assert list(ranks.values()) == sorted(ranks.values(), reverse=True)


async def test_a_user_list_under_the_cap_keeps_everything(session, repository) -> None:
    """Ranking reorders; it must not drop anyone who used to fit.

    The base fixture has two importers of this file, which is the population
    every other test in this directory runs on.
    """
    used_by = (await _card(session, repository, "AuthService"))["docs"]["used_by"]

    assert set(used_by) == {"src/auth/middleware.py", "tests/test_service.py"}


async def test_one_user_joined_by_two_edge_types_is_listed_once(
    session, repository, populated_db
) -> None:
    """A guard, not a fix: the measured corpus holds no duplicate row.

    It exists because the query selects edges and the field names files, and
    nothing but this test states that those are different quantities.
    """
    session.add(
        GraphEdge(
            id="ge-dup",
            repository_id=populated_db,
            source_node_id="src/auth/middleware.py",
            target_node_id=_FILE,
            edge_type="calls",
            imported_names_json="[]",
        )
    )
    await session.commit()

    used_by = (await _card(session, repository, "AuthService"))["docs"]["used_by"]

    assert used_by.count("src/auth/middleware.py") == 1
