"""A concept query does not lead with a page about one constant that only shares its words.

A spotlight on a constant or a one-line symbol, matched by full-text search
alone, carries the query's vocabulary but not its subject. It ranks below the
real pages; a query that names the symbol keeps it, and nothing is dropped.
"""

from __future__ import annotations

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server.tool_search.ranking import (
    _minor_spotlights,
    _sort_demoting_noise,
)

_FILE = "src/health/engine.py"


def _symbol(rid: str, name: str, kind: str, start: int, end: int) -> WikiSymbol:
    return WikiSymbol(
        id=f"eng-{name}",
        repository_id=rid,
        file_path=_FILE,
        symbol_id=f"{_FILE}::{name}",
        name=name,
        qualified_name=name,
        kind=kind,
        signature=name,
        start_line=start,
        end_line=end,
        visibility="public",
        is_async=False,
        complexity_estimate=1,
    )


def _hit(name: str, sources: list[str], score: float) -> dict:
    return {
        "page_id": f"symbol_spotlight:{_FILE}::{name}",
        "page_type": "symbol_spotlight",
        "sources": sources,
        "relevance_score": score,
    }


async def test_full_text_only_value_spotlights_sink(session, populated_db) -> None:
    rid = populated_db
    session.add(_symbol(rid, "ANALYZER_VERSION", "constant", 10, 10))
    session.add(_symbol(rid, "score_file", "function", 20, 60))
    session.add(_symbol(rid, "noop", "function", 70, 70))
    await session.flush()
    rows = [
        _hit("ANALYZER_VERSION", ["fts"], 9.0),
        _hit("noop", ["fts"], 8.0),
        _hit("score_file", ["fts"], 2.0),
        {"page_id": "file_page:src/health/scoring.py", "page_type": "file_page",
         "sources": ["fts", "vector"], "relevance_score": 3.0},
    ]

    query = "what does the health engine score"
    _sort_demoting_noise(rows, query, await _minor_spotlights(session, rows, query, rid))

    order = [row["page_id"].rsplit("::", 1)[-1].rsplit("/", 1)[-1] for row in rows]
    assert order == ["scoring.py", "score_file", "ANALYZER_VERSION", "noop"]


async def test_a_spotlight_the_query_names_or_vector_found_keeps_its_rank(
    session, populated_db
) -> None:
    rid = populated_db
    session.add(_symbol(rid, "ANALYZER_VERSION", "constant", 10, 10))
    session.add(_symbol(rid, "LIMIT", "constant", 11, 11))
    await session.flush()
    rows = [_hit("ANALYZER_VERSION", ["fts"], 9.0), _hit("LIMIT", ["fts", "vector"], 8.0)]

    assert not await _minor_spotlights(session, rows, "where is analyzer_version bumped", rid)
