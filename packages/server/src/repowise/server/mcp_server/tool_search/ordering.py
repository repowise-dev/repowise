"""Exact-match protection and hybrid window ordering for search results."""

from __future__ import annotations

import re
from collections.abc import Container, Sequence

from repowise.server.mcp_server._page_paths import hit_file_path
from repowise.server.mcp_server._query_shape import (
    _canonical_symbol_query,
    _embedded_identifiers,
    _qual_norm,
    _symbol_matches_name,
    path_tokens,
)


def _protect_exact_symbols(query: str, symbols: list[dict]) -> list[dict]:
    """Stable-partition an exact identifier or ``path::Symbol`` hit to the head."""
    if not symbols:
        return symbols
    canonical = _canonical_symbol_query(query)
    qnorm = query.strip().lower().replace("\\", "/")

    def exact(item: dict) -> bool:
        symbol_id = (item.get("symbol_id") or "").lower().replace("\\", "/")
        if canonical:
            return symbol_id == qnorm
        return qnorm in {
            (item.get("name") or "").lower(),
            _qual_norm(item.get("qualified_name")),
        }

    protected = [item for item in symbols if exact(item)]
    return protected + [item for item in symbols if not exact(item)]


def _protect_named_symbols(candidates: list[str], symbols: list[dict]) -> list[dict]:
    """Stable-partition symbols exactly named by identifiers embedded in prose."""
    wanted = {candidate.strip().lower() for candidate in candidates if candidate.strip()}
    wanted |= {_qual_norm(candidate) for candidate in candidates if candidate.strip()}

    protected = [item for item in symbols if _symbol_matches_name(item, wanted)]
    return protected + [item for item in symbols if not _symbol_matches_name(item, wanted)]


def _protect_exact_paths(
    query: str, files: list[dict], paths: Sequence[str] = ()
) -> list[dict]:
    """Stable-partition an exact path hit ahead of basename/fuzzy neighbours."""
    tokens = path_tokens(query, paths) or [query]
    wanted = {t.strip().lower().replace("\\", "/") for t in tokens}
    protected = [item for item in files if (item.get("file") or "").lower() in wanted]
    return protected + [item for item in files if (item.get("file") or "").lower() not in wanted]


def _prose_dominates(query: str, identifiers: list[str]) -> bool:
    """True when natural-language tokens outnumber the identifier tokens a query
    carries: the query reads as prose that merely *mentions* a symbol, not a
    symbol lookup dressed in a few words. Drives hybrid ordering below."""
    ident_count = len(identifiers)
    if ident_count == 0:
        return False
    total = len(re.findall(r"[A-Za-z0-9_]+", query))
    return (total - ident_count) > ident_count


def _interleave_hybrid(
    query: str,
    symbols: list[dict],
    concepts: list[dict],
    limit: int,
    exact: bool,
    names: Container[str] | None = None,
) -> list[dict]:
    """Order one hybrid result window from the two incomparable score scales.

    Symbol SQL scores (~43, +100 on an exact-name hit) and concept relevance
    (0 to 1) can't be merge-sorted, so we interleave by block. Default: symbols
    lead, reserving up to half the window for concept pages so a flood of symbol
    matches can't truncate every page out.

    The exception is the score-scale trap. When NO returned symbol matches the
    query's identifier exactly AND the query is mostly prose, leading with fuzzy
    symbol hits buries the page the caller actually wants: the generic ``.get``
    methods that outscore the ``answer.py`` page for "how does retrieval feed
    synthesis in get_answer". There, concept pages lead and the fuzzy symbols
    fall to the tail (nothing is dropped, only reordered within the window).
    """
    if not exact and concepts and _prose_dominates(query, _embedded_identifiers(query, names)):
        reserved = min(len(symbols), limit // 2)
        return (concepts[: max(1, limit - reserved)] + symbols)[:limit]
    reserved = min(len(concepts), limit // 2)
    return (symbols[: max(1, limit - reserved)] + concepts)[:limit]


# Code-location windows over-fetch this many times ``limit`` so collapsing
# same-file hits still leaves ``limit`` distinct files to serve.
_FILE_WINDOW_OVERFETCH = 2
# Other symbol names a collapsed row carries in ``symbols``.
_MERGED_SYMBOL_CAP = 5


def _collapse_by_file(hits: list[dict]) -> list[dict]:
    """One row per (repo, file), best first; rows naming no file pass through.

    The first row of a file wins. A later symbol hit in the same file folds
    into the winner's ``symbols`` as ``name:line`` (capped) instead of taking a
    slot of its own, so ``limit`` buys distinct files; past the cap a trailing
    ``+N more`` entry counts the rest. Precedent:
    ``symbol_backed_pages`` collapses the same way for the concept tail.
    """
    first_of: dict[tuple, dict] = {}
    extra: dict[int, int] = {}
    out: list[dict] = []
    for hit in hits:
        path = hit_file_path(hit)
        if path is None:
            out.append(hit)
            continue
        key = (hit.get("repo"), path)
        first = first_of.get(key)
        if first is None:
            first_of[key] = hit
            out.append(hit)
        elif hit.get("type") == "symbol" and first.get("type") == "symbol":
            merged = first.setdefault("symbols", [])
            if len(merged) < _MERGED_SYMBOL_CAP:
                merged.append(f"{hit.get('name')}:{hit.get('start_line')}")
            else:
                extra[id(first)] = extra.get(id(first), 0) + 1
    for hit in out:
        if id(hit) in extra:
            hit["symbols"].append(f"+{extra[id(hit)]} more")
    return out


def _hybrid_window(
    query: str,
    symbols: list[dict],
    concepts: list[dict],
    limit: int,
    exact: bool,
    names: Container[str] | None = None,
) -> list[dict]:
    """The hybrid window, with no page for a file a shown symbol row covers.

    Pages are dropped only for symbols the window actually shows, then the
    window is rebuilt so the freed slots backfill. Concepts only shrink and the
    symbol share only grows, so this settles in a few passes.
    """
    while True:
        window = _interleave_hybrid(query, symbols, concepts, limit, exact, names)
        shown = {item.get("file") for item in window if item.get("type") == "symbol"}
        kept = [c for c in concepts if c.get("target_path") not in shown]
        if len(kept) == len(concepts):
            return window
        concepts = kept
