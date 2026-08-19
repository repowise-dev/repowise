"""get_context must resolve the symbol ids get_symbol hands out.

Two tools, one id vocabulary. An agent reads a ``symbol_id`` out of one
response and pastes it into the other; if only one of them understands the
string, the id looks broken rather than the tool. These tests pin the three
ways that went wrong:

* a qualified target written with a different separator than the index stores
* a symbol-shaped target being typed as a *file* by the graph-node fallback,
  which produced a confident card describing the symbol as an empty file
* an unresolvable symbol half throwing away the resolvable file half
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target

_FILE = "src/auth/service.py"


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


async def _card(session, repository, target, *, repo_root=None) -> dict:
    return await _resolve_one_target(
        session, repository, target, None, True, exclude_spec=None, repo_root=repo_root
    )


# --- separator normalisation, both directions ------------------------------


@pytest.mark.parametrize(
    "target",
    [
        f"{_FILE}::AuthService.login",
        f"{_FILE}::AuthService::login",
        f"{_FILE}::AuthService/login",
    ],
)
async def test_qualified_symbol_resolves_in_every_separator_style(
    session, repository, target
) -> None:
    """Class.method, Class::method and Class/method are the same symbol.

    The fixture stores this symbol as ``src/auth/service.py::login`` with a
    dotted ``qualified_name``, so none of these three targets matches the
    index verbatim. Which one a caller writes is a fact about their language,
    not about the codebase, and must not decide whether the lookup works.
    """
    card = await _card(session, repository, target)
    assert "error" not in card, card.get("error")
    assert card["type"] == "symbol"
    assert card["docs"]["name"] == "login"
    assert card["docs"]["file_path"] == _FILE


async def test_dot_and_colon_forms_return_the_same_card(session, repository) -> None:
    """The stronger claim: not merely 'both work' but 'both agree'."""
    dot = await _card(session, repository, f"{_FILE}::AuthService.login")
    colon = await _card(session, repository, f"{_FILE}::AuthService::login")
    # ``target`` echoes the caller's string by design; everything describing
    # the resolved symbol must be identical.
    assert dot["docs"] == colon["docs"]
    assert dot["type"] == colon["type"] == "symbol"


async def test_bare_symbol_name_still_resolves(session, repository) -> None:
    """The unqualified rung is untouched; qualified handling is additive."""
    card = await _card(session, repository, "AuthService")
    assert card["type"] == "symbol"
    assert card["docs"]["name"] == "AuthService"


# --- the other direction: a real miss must still be a miss -----------------


async def test_absent_symbol_in_an_absent_file_still_errors(session, repository) -> None:
    """Normalisation must not turn every string into a hit.

    Nothing about this target exists, so the tool has nothing to degrade to
    and must say so rather than inventing a card.
    """
    card = await _card(session, repository, "src/nope/missing.py::Ghost")
    assert "error" in card
    assert "not found" in card["error"].lower()
    assert card.get("type") is None


async def test_absent_symbol_name_is_not_reported_as_found(session, repository) -> None:
    """Degrading to the file card must not claim the symbol resolved."""
    card = await _card(session, repository, f"{_FILE}::NoSuchMethod")
    assert card.get("type") != "symbol"
    assert card.get("docs", {}).get("name") != "NoSuchMethod"


# --- degrade to the file card ----------------------------------------------


async def test_unresolvable_symbol_degrades_to_its_file_card(session, repository) -> None:
    """The file half is still an answer, and it carries the real symbol list.

    A bare "Target not found" here is the worst reply available: the caller
    named a real, indexed file, and the list of ids they should have used is
    sitting in that file's card.
    """
    card = await _card(session, repository, f"{_FILE}::NoSuchMethod")
    assert "error" not in card
    assert card["type"] == "file"
    assert card["resolved_to"] == _FILE
    assert "NoSuchMethod" in card["note"]
    # The point of the degrade: the ids the caller needs are in the reply.
    names = {s["name"] for s in card["docs"]["symbols"]}
    assert {"AuthService", "login"} <= names


async def test_degrade_does_not_fire_for_an_excluded_file(session, repository) -> None:
    """Exclusion is a boundary rather than a formatting rule; it survives the degrade."""
    import pathspec

    excluded = pathspec.PathSpec.from_lines("gitwildmatch", ["src/auth/*"])
    card = await _resolve_one_target(
        session, repository, f"{_FILE}::NoSuchMethod", None, True, exclude_spec=excluded
    )
    assert "error" in card
    assert "excluded" in card["error"]


# --- the graph-node fallback must not type a symbol as a file --------------


async def test_symbol_graph_node_is_not_served_as_a_file_card(session, repository) -> None:
    """A symbol node id matched the file rung and produced a confident lie.

    Call-graph symbol nodes are keyed ``path::Class::method``. The fallback
    that exists for index-only mode matched that id, typed it ``file``, then
    looked for symbols whose *file_path* was the whole id, found none, and
    summarised the method as "empty or non-symbol file". Wrong with
    confidence is worse than a miss: the reply looks answered.

    The node is not discarded. In index-only mode it is the only record of
    the symbol, and the callers/callees blocks resolve from it. It is typed as
    what it is, and its file is the node's file rather than its own id.
    """
    ghost = f"{_FILE}::AuthService::ghost_method"
    session.add(
        GraphNode(
            id="gn-ghost",
            repository_id=repository.id,
            node_id=ghost,
            node_type="symbol",
            name="ghost_method",
            file_path=_FILE,
            language="python",
        )
    )
    await session.flush()

    card = await _card(session, repository, ghost)
    assert "empty or non-symbol file" not in card.get("docs", {}).get("summary", "")
    assert card["type"] == "symbol"
    assert card["docs"]["name"] == "ghost_method"
    # The file is the node's file, not the whole symbol id.
    assert card["docs"]["file_path"] == _FILE
    # Fields the node genuinely lacks are absent rather than blank-filled.
    assert "signature" not in card["docs"]


async def test_graph_only_symbol_still_resolves_its_call_graph(session, repository) -> None:
    """Typing the node correctly must not cost the enrichment it existed for.

    In index-only mode the graph node is the only record of the symbol, and
    the callers block resolves from it. Dropping the node to avoid the bad
    file card would have taken this with it.
    """
    ghost = f"{_FILE}::AuthService::ghost_method"
    caller = f"{_FILE}::AuthService::caller"
    for nid, nm in ((ghost, "ghost_method"), (caller, "caller")):
        session.add(
            GraphNode(
                id=f"gn-{nm}",
                repository_id=repository.id,
                node_id=nid,
                node_type="symbol",
                name=nm,
                file_path=_FILE,
                language="python",
            )
        )
    session.add(
        GraphEdge(
            id="ge-ghost",
            repository_id=repository.id,
            source_node_id=caller,
            target_node_id=ghost,
            edge_type="calls",
            confidence=0.95,
        )
    )
    await session.flush()

    card = await _resolve_one_target(
        session, repository, ghost, {"callers"}, True, exclude_spec=None
    )
    assert [c["symbol_id"] for c in card["callers"]] == [caller]
