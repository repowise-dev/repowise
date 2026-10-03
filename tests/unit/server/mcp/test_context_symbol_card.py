"""The compact file card lists a file's most useful symbols, not its first ones.

A file's head is mostly constants and type vars, so a start-line slice spent
the card on them. The card now ranks by kind, then PageRank, caps the list,
names how to get the rest, and drops a ``symbol_id`` only when it is exactly
``path::name``.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GraphNode, Repository, WikiSymbol
from repowise.server.mcp_server.tool_context.targets import _SYMBOL_CAP, _resolve_one_target

_FILE = "src/dense.py"
_DEFAULT = {"docs", "freshness"}


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


@pytest.fixture
async def dense_file(session, populated_db) -> None:
    """18 module variables first, then 3 classes, 4 functions and 2 variants."""
    rid = populated_db
    session.add(
        GraphNode(
            id="gn-dense", repository_id=rid, node_id=_FILE, node_type="file", language="python"
        )
    )
    rows: list[tuple[str, str, str]] = [(f"VAR_{i}", "variable", f"VAR_{i}") for i in range(18)]
    rows += [(f"Klass{i}", "class", f"Klass{i}") for i in range(3)]
    rows += [(f"fn_{i}", "function", f"fn_{i}") for i in range(4)]
    rows += [("run", "method", "Klass0::run"), ("fn_0", "function", "fn_0#2")]
    for line, (name, kind, tail) in enumerate(rows, start=1):
        sid = f"{_FILE}::{tail}"
        session.add(
            WikiSymbol(
                id=f"dense-{line}",
                repository_id=rid,
                file_path=_FILE,
                symbol_id=sid,
                name=name,
                qualified_name=name,
                kind=kind,
                signature=name,
                start_line=line,
                end_line=line,
                visibility="public",
                is_async=False,
                complexity_estimate=1,
            )
        )
    # fn_3 is the most central function, so it leads the functions.
    session.add(
        GraphNode(
            id="gn-fn3",
            repository_id=rid,
            node_id=f"{_FILE}::fn_3",
            node_type="symbol",
            file_path=_FILE,
            pagerank=0.5,
        )
    )
    await session.flush()


async def _card(session, repository, target, include=_DEFAULT) -> dict:
    return await _resolve_one_target(session, repository, target, include, True, exclude_spec=None)


async def test_default_card_is_capped_and_ranked(session, repository, dense_file) -> None:
    docs = (await _card(session, repository, _FILE))["docs"]
    symbols = docs["symbols"]
    assert len(symbols) == _SYMBOL_CAP == 15
    assert docs["symbols_truncated"]["shown"] == 15
    assert docs["symbols_truncated"]["total"] == 27
    assert "include=['symbols']" in docs["symbols_truncated"]["hint"]
    kinds = [s["kind"] for s in symbols]
    assert kinds[:3] == ["class"] * 3
    assert set(kinds[3:9]) == {"function", "method"}
    assert kinds[9:] == ["variable"] * 6
    # Centrality orders within a kind, start line breaks ties.
    assert [s["name"] for s in symbols[3:5]] == ["fn_3", "fn_0"]
    assert [s["name"] for s in symbols[9:11]] == ["VAR_0", "VAR_1"]


async def test_include_symbols_returns_every_symbol(session, repository, dense_file) -> None:
    docs = (await _card(session, repository, _FILE, _DEFAULT | {"symbols"}))["docs"]
    assert len(docs["symbols"]) == 27
    assert "symbols_truncated" not in docs


async def test_symbol_id_dropped_only_when_derivable(session, repository, dense_file) -> None:
    docs = (await _card(session, repository, _FILE, _DEFAULT | {"symbols"}))["docs"]
    by_id = {s.get("symbol_id"): s for s in docs["symbols"] if "symbol_id" in s}
    # A method and an overload variant cannot be rebuilt from path::name.
    assert set(by_id) == {f"{_FILE}::Klass0::run", f"{_FILE}::fn_0#2"}
    plain = [s for s in docs["symbols"] if "symbol_id" not in s]
    assert len(plain) == 25


async def test_unresolved_symbol_degrade_lists_every_symbol(
    session, repository, dense_file
) -> None:
    """The caller is hunting for a name, so the fallback card is uncapped."""
    card = await _card(session, repository, f"{_FILE}::Missing")
    assert card["resolved_to"] == _FILE
    assert len(card["docs"]["symbols"]) == 27
