"""The fact row and direct test links a symbol card carries.

``get_symbol`` and the ``get_context`` symbol card both serve these, so an agent
learns what a function does and which tests run it without opening a test file.

Everything here is a positive statement. The stores behind it are floors:
``tests_json`` misses tests reached through dispatch tables, monkeypatching and
callbacks, and the call graph misses calls it could not resolve, so an empty
store says nothing. Nothing is printed when a store is empty or unmapped.

Measured on a hand-checked sample of this repository's index:

* stored test links one hop out are right every time; two hops out are right
  when labelled with the function in between; three hops out reach code the
  test never runs, so they are never shown;
* call edges from the origins in :data:`STRONG_CALL_ORIGINS` were all real
  calls; ``import_merged`` and ``global_unique`` were often wrong, and
  ``global_unique`` was wrong for every TypeScript edge checked.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.execution_graph import (
    file_of_symbol,
    is_reliable_execution_edge,
    module_node_id,
)
from repowise.core.code_origin import path_origin
from repowise.core.ingestion.models import (
    EXECUTION_EDGE_TYPES,
    EXTENSION_TO_LANGUAGE,
    TS_JS_LANGUAGES,
)
from repowise.core.persistence.models import FunctionFact, GraphEdge, GraphNode, HealthFileMetric
from repowise.server.mcp_server._references import symbol_identity

if TYPE_CHECKING:
    from repowise.core.analysis.function_test_map import MappedTest

#: Resolution origins whose call edges measured clean in the hand-checked
#: sample. Origins outside it may be right too; they are not counted.
STRONG_CALL_ORIGINS = frozenset(
    {
        "same_file",
        "import_scoped",
        "self_scope",
        "receiver_typed_same_file",
        "receiver_typed_import",
        "module_alias",
        "return_type_import",
    }
)

#: Test links the card shows, nearest first.
MAX_TEST_LINKS = 3
#: Assigned fields named in the fact row; the rest are counted.
_MAX_FIELDS_NAMED = 4


def _language(path: str) -> str | None:
    return EXTENSION_TO_LANGUAGE.get(PurePosixPath(path).suffix)


def shows_call_origin(origin: str | None, target_path: str) -> bool:
    """Whether an edge of *origin* into a symbol in *target_path* may be shown.

    A ``global_unique`` edge binds a call to the only symbol with that name; in
    TypeScript and JavaScript that name is usually a library import the graph
    does not hold (icons, hooks, ``dynamic``), so the edge is wrong.
    """
    return not (origin == "global_unique" and _language(target_path) in TS_JS_LANGUAGES)


def _body_facts(fact: FunctionFact | None) -> list[str]:
    """What the stored walk saw the body do, positive facts only."""
    if fact is None:
        return []
    parts: list[str] = []
    if fact.awaits:
        parts.append("awaits")
    if fact.is_generator:
        parts.append("generator")
    fields = _assigned_fields(fact.receiver_assigns_json)
    if fields:
        named = ", ".join(fields[:_MAX_FIELDS_NAMED])
        more = len(fields) - _MAX_FIELDS_NAMED
        parts.append(f"assigns fields {named}" + (f" (+{more} more)" if more > 0 else ""))
    if fact.ccn is not None:
        parts.append(f"complexity {fact.ccn}")
    return parts


def _assigned_fields(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        fields = json.loads(value)
    except (TypeError, ValueError):
        return []
    return sorted(f for f in fields if isinstance(f, str)) if isinstance(fields, list) else []


async def _caller_fact(session: AsyncSession, repo_id: str, symbol_id: str) -> str | None:
    """``"N resolved callers, M in tests"`` over strong-origin ``calls`` edges.

    Test callers are judged by the caller file's stored code origin, read in the
    same query, and by its path where the file has no stored row.
    """
    rows = await session.execute(
        select(GraphEdge.source_node_id, HealthFileMetric.code_origin, HealthFileMetric.is_test)
        .outerjoin(
            GraphNode,
            (GraphNode.repository_id == GraphEdge.repository_id)
            & (GraphNode.node_id == GraphEdge.source_node_id),
        )
        .outerjoin(
            HealthFileMetric,
            (HealthFileMetric.repository_id == GraphEdge.repository_id)
            & (HealthFileMetric.file_path == GraphNode.file_path),
        )
        .where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id == symbol_id,
            GraphEdge.edge_type == "calls",
            GraphEdge.resolution_origin.in_(sorted(STRONG_CALL_ORIGINS)),
        )
    )
    from repowise.core.analysis.finding_registry import stored_origin

    origins: dict[str, str] = {}
    for source, code_origin, is_test in rows.all():
        if source == symbol_id:
            continue
        has_row = code_origin is not None or is_test is not None
        origins[source] = (
            stored_origin(code_origin, is_test) if has_row else path_origin(file_of_symbol(source))
        )
    if not origins:
        return None
    in_tests = sum(1 for origin in origins.values() if origin == "test")
    noun = "caller" if len(origins) == 1 else "callers"
    fact = f"{len(origins)} resolved {noun}"
    return fact + (f", {in_tests} in tests" if in_tests else "")


def _test_node(selector: str) -> str:
    """The graph node a selector's walk starts from: the test, or a JS/TS test file's module."""
    return selector if "::" in selector else module_node_id(selector)


async def _via_symbols(
    session: AsyncSession, repo_id: str, symbol_id: str, two_hop: list[MappedTest]
) -> dict[str, str]:
    """``{selector: X}`` for tests that run *symbol_id* through one function X.

    X is a caller of the symbol the test calls directly, over the same reliable
    execution edges the test map walked. A test with no such X found is left
    out rather than shown unlabelled.
    """
    if not two_hop:
        return {}
    inbound = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.edge_type, GraphEdge.resolution_origin).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id == symbol_id,
            GraphEdge.edge_type.in_(sorted(EXECUTION_EDGE_TYPES)),
        )
    )
    middles = {
        source
        for source, edge_type, origin in inbound.all()
        if source != symbol_id and is_reliable_execution_edge(edge_type, origin)
    }
    if not middles:
        return {}
    starts = {_test_node(t.selector): t.selector for t in two_hop}
    outbound = await session.execute(
        select(
            GraphEdge.source_node_id,
            GraphEdge.target_node_id,
            GraphEdge.edge_type,
            GraphEdge.resolution_origin,
        ).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.source_node_id.in_(sorted(starts)),
            GraphEdge.target_node_id.in_(sorted(middles)),
            GraphEdge.edge_type.in_(sorted(EXECUTION_EDGE_TYPES)),
        )
    )
    via: dict[str, str] = {}
    # Sorted, so the same index always names the same X.
    for source, target, edge_type, origin in sorted(outbound.all()):
        if is_reliable_execution_edge(edge_type, origin):
            via.setdefault(starts[source], symbol_identity(target))
    return via


async def _test_links(
    session: AsyncSession, repo_id: str, symbol_id: str, fact: FunctionFact | None
) -> list[dict[str, str]]:
    """Runnable selectors of the tests that call the symbol, or call it through one function."""
    from repowise.core.analysis.function_test_map import decode_tests

    mapped = decode_tests(fact.tests_json if fact is not None else None) or ()
    direct = [t for t in mapped if t.hops == 1]
    two_hop = [t for t in mapped if t.hops == 2][: max(0, MAX_TEST_LINKS - len(direct))]
    links = [{"run": t.selector} for t in direct[:MAX_TEST_LINKS]]
    via = await _via_symbols(session, repo_id, symbol_id, two_hop)
    links += [
        {"run": t.selector, "via": _short_via(via[t.selector], t.selector, symbol_id)}
        for t in two_hop
        if t.selector in via
    ]
    return links


def _short_via(via: str, selector: str, symbol_id: str) -> str:
    """*via* by its name alone when it sits in the test's file or the symbol's own."""
    path, _, name = via.partition("::")
    if path in (file_of_symbol(selector), file_of_symbol(symbol_id)):
        return name
    return via


async def symbol_card_facts(session: AsyncSession, repo_id: str, symbol_id: str) -> dict[str, Any]:
    """``facts`` (one line) and ``tests`` for *symbol_id*; keys absent when there is nothing true to say."""
    fact = await session.get(FunctionFact, (repo_id, symbol_id))
    parts = _body_facts(fact)
    callers = await _caller_fact(session, repo_id, symbol_id)
    if callers:
        parts.append(callers)
    out: dict[str, Any] = {}
    if parts:
        out["facts"] = "; ".join(parts)
    tests = await _test_links(session, repo_id, symbol_id, fact)
    if tests:
        out["tests"] = tests
    return out


__all__ = ["MAX_TEST_LINKS", "STRONG_CALL_ORIGINS", "shows_call_origin", "symbol_card_facts"]
