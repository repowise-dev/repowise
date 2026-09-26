"""``get_context``'s ``used_by`` answers for the symbol, and ranks its users.

Two contracts. **What the list is drawn from:** keyed on the symbol's own node
id, so a ``calls`` edge — symbol-to-symbol, target ``path::Name`` — can appear
in it. Keyed on the symbol's *file*, as it was until this was fixed, no call
could ever match.

**Which twenty survive:** the field is cut at ``_MAX_USED_BY`` and the agent is
never told what fell off, so the kept set is the whole of what it says. On the
42-index corpus ranking moves that set on 4,447 of the 4,743 targets over the
cap. The fixture is deliberately larger than the cap, because the defect does
not exist below it — the repo's other coverage ran on two edges, which is why
nothing caught it.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.mcp_server.tool_context.targets import _MAX_USED_BY, _resolve_one_target

_FILE = "src/auth/service.py"
_SYMBOL = "src/auth/service.py::AuthService"
_NOISE = 25


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


@pytest.fixture
async def many_users(session, populated_db) -> str:
    """``_NOISE`` peripheral users, then one central one, added last.

    ``zz_`` is load-bearing. The first cut of this fixture called the central
    file ``src/app/hub.py`` and **passed against the unranked code** — SQLite
    serves this join through the ``(repository_id, node_id)`` unique index, so
    the "unordered" query comes back in path order and a hub whose path sorts
    early survives a cut that is not ranking anything. Named to sort last, and
    inserted last, so neither path order nor insertion order can keep it: only
    the ranking can. (That also says what the old behaviour was — alphabetical
    on SQLite, nothing promised on Postgres — which is not the same as random,
    and is still the wrong list.)

    Every edge is symbol-to-symbol, which is the shape the graph really emits;
    the card folds each source back to its file, so the rows stay paths.
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
                source_node_id=f"{path}::use_auth",
                target_node_id=_SYMBOL,
                edge_type="calls",
                imported_names_json="[]",
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
            source_node_id="src/zz_hub.py::boot",
            target_node_id=_SYMBOL,
            edge_type="calls",
            imported_names_json="[]",
        )
    )
    await session.commit()
    return rid


@pytest.fixture
async def two_users(session, populated_db) -> str:
    """The base fixture joins these two files to ``service.py`` with ``imports``.

    Those say the file was imported, not that this symbol was used, which is
    why they no longer answer here.
    """
    rid = populated_db
    for i, source in enumerate(
        ("src/auth/middleware.py::require_login", "tests/test_service.py::test_login")
    ):
        session.add(
            GraphEdge(
                id=f"ge-user-{i}",
                repository_id=rid,
                source_node_id=source,
                target_node_id=_SYMBOL,
                edge_type="calls",
                imported_names_json="[]",
            )
        )
    await session.commit()
    return rid


async def _card(session, repository, target: str) -> dict:
    return await _resolve_one_target(
        session, repository, target, None, True, exclude_spec=None, repo_root=None
    )


async def test_a_call_into_the_symbol_is_a_user(session, repository, two_users) -> None:
    """The defect this file exists for: keyed on the file, this list held no calls.

    A ``calls`` edge targets ``path::Name``, so against a bare file path the one
    relationship the field is named for was the one it could never carry.
    """
    used_by = (await _card(session, repository, "AuthService"))["docs"]["used_by"]

    assert set(used_by) == {"src/auth/middleware.py", "tests/test_service.py"}


async def test_importing_the_file_is_not_using_the_symbol(session, repository) -> None:
    """The base fixture's two importers must NOT appear on their own.

    Nothing in the graph says they touched ``AuthService``, and answering as
    though it did is what made this a restatement of ``imported_by``.
    """
    docs = (await _card(session, repository, "AuthService"))["docs"]

    # Present and empty, not absent: the cap writes the key either way.
    assert docs["used_by"] == []
    # An empty list is only readable beside what the resolver actually bound.
    assert "used_by_basis" in docs


async def test_the_most_central_user_survives_the_cut(session, repository, many_users) -> None:
    docs = (await _card(session, repository, "AuthService"))["docs"]
    used_by = docs["used_by"]

    assert len(used_by) == _MAX_USED_BY
    assert used_by[0] == "src/zz_hub.py"
    assert docs["used_by_total"] == _NOISE + 1
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


async def test_two_symbols_in_one_file_are_one_user(
    session, repository, two_users, populated_db
) -> None:
    """A guard, and now a live one: the query selects edges, the field names files.

    Two calls from one file are one user. Before the rows were folded this could
    not arise, because a file imported a file at most once.
    """
    session.add(
        GraphEdge(
            id="ge-dup",
            repository_id=populated_db,
            source_node_id="src/auth/middleware.py::also_calls",
            target_node_id=_SYMBOL,
            edge_type="references",
            imported_names_json="[]",
        )
    )
    await session.commit()

    used_by = (await _card(session, repository, "AuthService"))["docs"]["used_by"]

    assert used_by.count("src/auth/middleware.py") == 1


async def test_the_symbols_own_file_is_not_listed_as_its_user(
    session, repository, populated_db
) -> None:
    """A same-file caller is a real use, but naming the file being read spends a capped row."""
    session.add(
        GraphEdge(
            id="ge-self",
            repository_id=populated_db,
            source_node_id=f"{_FILE}::login",
            target_node_id=_SYMBOL,
            edge_type="calls",
            imported_names_json="[]",
        )
    )
    await session.commit()

    used_by = (await _card(session, repository, "AuthService"))["docs"]["used_by"]

    assert _FILE not in used_by


async def test_ranking_survives_a_chunked_rank_lookup(
    session, repository, many_users, monkeypatch
) -> None:
    """The rank lookup binds one parameter per using file, so it is chunked.

    Shrinking the chunk reaches a boundary without 500 nodes. If one ever
    dropped or reordered a file, the hub would stop leading.
    """
    from repowise.server.mcp_server.tool_context import targets as targets_mod

    monkeypatch.setattr(targets_mod, "_RANK_LOOKUP_CHUNK", 3)
    docs = (await _card(session, repository, "AuthService"))["docs"]

    assert docs["used_by"][0] == "src/zz_hub.py"
    assert docs["used_by_total"] == _NOISE + 1
    assert len(docs["used_by"]) == _MAX_USED_BY
