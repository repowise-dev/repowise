"""Symbol retrieval keyed on the words a question is asked in.

RepoWise has always been able to search its structural symbol index, but only
when the caller happened to *type* a symbol: ``search_codebase`` routed to the
symbol leg on a bare identifier or on a snake_case / CamelCase token embedded
in prose, and ``get_answer`` anchored symbols through the same shape test. Ask
the identical question in English and the symbol index is never consulted:

    "_persist_symbols"                                    -> symbol leg runs
    "how does an incremental update persist symbols"      -> symbol leg silent

Those two callers want the same file. The shape of the sentence is not a
property of the codebase, so it should not decide which indexes get read.

This module removes the shape test. It takes the content words of a question
and searches ``wiki_symbols`` on them directly, because a symbol name is
*built out of* the same words a developer uses to describe it
(``persist`` + ``symbols`` -> ``_persist_symbols``).

Two guards keep that from turning into noise, both borrowed from how lexical
code search engines handle the same problem:

* **Saturation down-weighting**. A term matching a large share of the
  candidate window carries almost no information about which symbol is meant
  (``get``, ``config``, ``file``). Rather than hand-maintaining a blocklist of
  "generic" programming words, we measure it: a term whose per-term window
  came back full is worth a quarter of a term whose window did not. This is a
  cheap stand-in for IDF that needs no corpus statistics on disk.
* **Corroboration**. A single common word is not enough to seed a hit. A
  symbol has to cover either two distinct query terms, or one term that *is*
  its leaf name. So "how does the update command work" cannot drag in every
  symbol named ``update``, but "how does an incremental update persist
  symbols" can reach ``_persist_symbols``.

Per-term candidate windows (rather than one OR-clause with a global cap) are
load-bearing: with a single window, one common term's matches evict every
other term's before scoring ever runs, which is the failure that makes naive
prose-to-symbol search useless.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, Page, WikiSymbol
from repowise.core.test_paths import is_test_path, is_test_related_path
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    _get_exclude_spec,
    _get_repo,
    escape_like,
    is_excluded,
)
from repowise.server.mcp_server._query_terms import content_terms as content_terms
from repowise.server.mcp_server.tool_search_symbols import (
    _symbol_kind_for_request_kind,
    _symbol_result,
    _tokens,
    _tombstoned_paths,
)

# Rows pulled per term. Bounded per term so a common word cannot evict the
# rare word that actually identifies the symbol; small enough that a
# many-term question stays a handful of cheap scans.
_PER_TERM_CANDIDATES = 80

# A term whose window came back full matched at least this many rows, so we
# learn nothing from it about *which* symbol is meant. Kept as a multiplier
# rather than a drop: a saturated term is weak evidence, not none.
_SATURATED_TERM_WEIGHT = 0.25

# Score awarded per distinct query term a symbol's tokens cover, scaled by
# that term's weight. The absolute number is arbitrary, since these scores are
# only ever compared against each other, then fused by rank.
_TERM_MATCH_SCORE = 10.0

# Bonus when a term IS the symbol's whole name (``persist`` -> ``persist``),
# as opposed to being one token inside it (``persist`` -> ``_persist_symbols``).
_LEAF_NAME_BONUS = 15.0

# These names are common because they describe an operation, not a domain.
# They remain valid evidence when the owner, module, or path also agrees with
# the question; the bare member name alone is deliberately insufficient.
_GENERIC_MEMBER_NAMES = frozenset({"get", "run", "main"})


async def _candidates_for_term(session, repo_id: str, term: str) -> tuple[list[WikiSymbol], bool]:
    """Up to :data:`_PER_TERM_CANDIDATES` symbols whose identity mentions *term*.

    Returns ``(rows, saturated)``. Shorter names first: a term is a larger
    share of a short name, so ``persist`` identifies ``persist_pages`` far
    more strongly than it identifies ``_persist_symbols_for_changed_files``,
    and the window should not be spent on the latter.
    """
    esc = escape_like(term)
    stmt = (
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == repo_id,
            or_(
                WikiSymbol.name.ilike(f"%{esc}%", escape=LIKE_ESCAPE),
                WikiSymbol.qualified_name.ilike(f"%{esc}%", escape=LIKE_ESCAPE),
                WikiSymbol.file_path.ilike(f"%{esc}%", escape=LIKE_ESCAPE),
            ),
        )
        .order_by(func.length(WikiSymbol.name))
        .limit(_PER_TERM_CANDIDATES)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return rows, len(rows) >= _PER_TERM_CANDIDATES


def _score(
    row: WikiSymbol,
    gnode: GraphNode | None,
    covered: dict[str, float],
) -> float:
    """Rank one candidate by how much of the question its name accounts for.

    ``covered`` maps each matched term to its weight. Graph centrality breaks
    ties the same way it does in :mod:`tool_search_symbols`, bounded so a
    central file cannot outrank a real name match.
    """
    name = (row.name or "").lower()
    score = sum(_TERM_MATCH_SCORE * w for w in covered.values())
    if name in covered:
        score += _LEAF_NAME_BONUS
    if gnode is not None:
        score += min(gnode.pagerank or 0.0, 0.1) * 50.0
        score += min(gnode.betweenness or 0.0, 0.1) * 20.0
        if gnode.is_entry_point:
            score += 3.0
    if (gnode is not None and gnode.is_test) or is_test_path(row.file_path or "", row.language):
        score -= 5.0
    return score


def _corroborated(row: WikiSymbol, covered: dict[str, float], saturated: set[str]) -> bool:
    """Whether *row* has enough independent evidence to enter the pool.

    One term is enough when it is the symbol's whole specific name and it is not
    saturated: that is a caller naming the thing, just without the underscores.
    Generic members such as ``get`` remain valid only when an owner/module/path
    term also agrees. Otherwise **two informative terms** must land. Saturated
    terms do not count toward that pair: two common words agreeing is not
    corroboration, it is the same non-signal twice.
    """
    if not covered:
        return False
    name = (row.name or "").lower()
    informative = {term for term in covered if term not in saturated}
    if name in _GENERIC_MEMBER_NAMES:
        return any(term != name for term in informative)
    if name in informative:
        return True
    return len(informative) >= 2


async def search_symbols_by_terms(
    ctx: Any,
    terms: list[str],
    limit: int,
    *,
    kind: str | None = None,
) -> list[dict]:
    """Rank indexed symbols against the content words of a question.

    The prose-side counterpart to
    :func:`tool_search_symbols.search_symbols_single`, which needs the caller
    to have typed an identifier. Returns the same result shape, so a caller
    can interleave the two without special-casing either.
    """
    terms = [t for t in terms if t]
    if not terms:
        return []

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        by_id: dict[str, WikiSymbol] = {}
        matched: dict[str, set[str]] = {}
        saturated: set[str] = set()
        for term in terms:
            rows, is_saturated = await _candidates_for_term(session, repository.id, term)
            if is_saturated:
                saturated.add(term)
            for row in rows:
                by_id[row.symbol_id] = row
                matched.setdefault(row.symbol_id, set()).add(term)
        if not by_id:
            return []

        gres = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.node_id.in_(list(by_id)),
            )
        )
        gmap = {g.node_id: g for g in gres.scalars().all()}
        tombstoned = await _tombstoned_paths(
            session, repository.id, {r.file_path for r in by_id.values()}
        )

    spec = _get_exclude_spec(ctx.path)
    scored: list[tuple[float, WikiSymbol]] = []
    for symbol_id, row in by_id.items():
        if is_excluded(row.file_path, spec) or row.file_path in tombstoned:
            continue
        gnode = gmap.get(symbol_id)
        is_test = (gnode is not None and gnode.is_test) or is_test_related_path(
            row.file_path or "", row.language
        )
        if not _symbol_kind_for_request_kind(kind, is_test):
            continue
        # A term counts as covered when it survives tokenisation of the symbol
        # name, not merely as a substring: ``update`` should match
        # ``update_index``, not ``groupdater``.
        stoks = _tokens(row.name) | _tokens(row.qualified_name) | _tokens(row.file_path)
        covered = {
            t: (_SATURATED_TERM_WEIGHT if t in saturated else 1.0)
            for t in matched.get(symbol_id, ())
            if t in stoks
        }
        if not _corroborated(row, covered, saturated):
            continue
        scored.append((_score(row, gnode, covered), row))

    scored.sort(key=lambda pair: (-pair[0], pair[1].symbol_id or ""))
    return [_symbol_result(row, score) for score, row in scored[:limit]]


async def symbol_backed_pages(
    ctx: Any,
    question: str,
    *,
    max_files: int,
    symbol_limit: int = 20,
) -> list[dict]:
    """File pages whose indexed symbols the words of *question* name.

    The shared entry point for both retrieval surfaces. Returns page rows in
    symbol-rank order, at most one per file: several symbol hits inside one
    file are one candidate to read, not several.

    Why this exists at all: a generated file page renders an overview
    sentence, the **public** symbol table and dependency paths. Function
    bodies, private helpers and local names never appear, so they are absent
    from both the full-text index and the embedding. The symbol index is the
    only place those names are searchable, and until now it was consulted only
    when the caller happened to type one.

    Never raises: a missing or slow symbol index leaves the page retrievers to
    answer on their own, which is the behaviour that shipped before this.
    """
    terms = content_terms(question)
    if not terms or max_files <= 0:
        return []
    try:
        symbols = await search_symbols_by_terms(ctx, terms, symbol_limit)
    except Exception:
        return []
    if not symbols:
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for hit in symbols:
        path = hit.get("file")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    paths = paths[:max_files]
    if not paths:
        return []

    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(Page.id, Page.target_path, Page.title, Page.summary, Page.page_type).where(
                Page.target_path.in_(paths),
                Page.page_type == "file_page",
                Page.freshness_status != "tombstone",
            )
        )
        by_path = {
            row[1]: {
                "page_id": row[0],
                "target_path": row[1],
                "title": row[2] or f"File: {row[1]}",
                "summary": row[3] or "",
                "page_type": row[4] or "file_page",
            }
            for row in res.all()
        }
    # A symbol whose file has no page cannot be read, so it contributes
    # nothing and is dropped rather than ranked.
    return [by_path[p] for p in paths if p in by_path]
