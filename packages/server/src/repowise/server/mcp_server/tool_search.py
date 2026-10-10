"""MCP Tool 5: search_codebase — semantic search over the wiki."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Container, Sequence
from typing import Any

from sqlalchemy import select

from repowise.core.generation.structural_labels import is_structural_title
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    Page,
)
from repowise.core.persistence.search import strip_leading_headings
from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.core.test_paths import is_test_path, is_test_related_path
from repowise.server.mcp_server._answer_pipeline import (
    _FILENAME_LEG_RRF_K,
    _RRF_K,
    _RRF_SCORE_SCALE,
    _SYMBOL_LEG_RRF_K,
    _safe_filename_search,
    _safe_symbol_search,
    leg_ranks,
)
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    register_post_enforce,
    register_post_shed,
)
from repowise.server.mcp_server._helpers import (
    _VECTOR_TIMEOUT_ENV,
    _get_exclude_spec,
    _get_repo,
    _resolve_all_contexts,
    _resolve_repo_context,
    attach_ignored_arguments,
    filter_dicts_by_key,
    resolve_enum_argument,
    vector_search_timeout_s,
)
from repowise.server.mcp_server._hit_symbols import attach_hit_symbols
from repowise.server.mcp_server._line_hits import attach_line_hits
from repowise.server.mcp_server._meta import EXHAUSTIVE_SWEEP_HINT, semantic_search_state
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._page_paths import (
    FILE_ROW_TYPES,
    PAGELESS_FILE,
    add_row_paths,
    file_candidates,
    file_path_of,
    hit_file_path,
    pageless_path,
)
from repowise.server.mcp_server._query_shape import (
    _DECISION_DOWNWEIGHT,
    _MIN_RELEVANCE_SCORE,
    _VALID_MODES,
    NOT_THE_NAMED_SYMBOL,
    _canonical_symbol_query,
    _embedded_identifiers,
    _fetch_limit_for,
    _has_exact_symbol,
    _identifier_candidates,
    _is_why_shaped,
    _looks_like_code_name,
    _looks_like_exact_token,
    _mark_not_the_named_symbol,
    _names_a_path,
    _qual_norm,
    _resolve_mode,
    _symbol_matches_name,
    path_tokens,
)
from repowise.server.mcp_server._references import path_identity, symbol_identity
from repowise.server.mcp_server._retrieval_rank import (
    boost_named_paths,
    rerank_pages_first,
)
from repowise.server.mcp_server.tool_search_symbols import (
    _MAX_CANDIDATES,
    IssueFiles,
    file_path_index,
    indexed_names,
    issue_files,
    search_paths_single,
    search_symbols_single,
)

_log = logging.getLogger("repowise.mcp.search")

# Freshness tie-breaker, added (not multiplied) to a hit's fused relevance.
# The RRF-fused score spaces adjacent ranks ~0.05 apart, so this stays under
# one rank step: recency orders otherwise-comparable hits without overriding
# retrieval relevance. Recency itself scales it (0.5 for 90-day activity, 1.0
# for 30-day).
_FRESHNESS_TIEBREAK = 0.03


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


def _downweight_decisions(output: list[dict], query: str) -> None:
    """Scale decision_record relevance in place unless the query is why-shaped."""
    if _is_why_shaped(query):
        return
    for item in output:
        if item.get("page_type") == "decision_record" and item.get("relevance_score"):
            item["relevance_score"] = round(item["relevance_score"] * _DECISION_DOWNWEIGHT, 4)


# Test file pages compete against the implementation they exercise and win
# retrieval on any shared vocabulary ("conftest" for a fixtures question,
# "decision" for a downweight question) — observed live as a test file ranking
# #1 for a plain implementation query. A test is rarely a better first Read than
# the code under test, so demote it, unless the query is explicitly about tests.
_TEST_DOWNWEIGHT = 0.6

_TEST_QUERY_RE = re.compile(
    r"\b(test|tests|testing|tested|unit[\s-]?test|integration[\s-]?test|pytest|fixture|mock|spec)\b",
    re.IGNORECASE,
)


def _is_test_query(query: str) -> bool:
    """True when the query is explicitly about tests, so test pages rank naturally."""
    return bool(_TEST_QUERY_RE.search(query))


def _is_test_page(item: dict) -> bool:
    """True when a hit is a whole-file row for a test file.

    Tests only, deliberately not test support: a ``conftest.py`` or a fixture
    module is often exactly what the person searching wanted, so it keeps its
    rank. The ``kind`` filter counts both (see :func:`_classify_hit_kind`) —
    asking for tests and asking to rank tests lower are different questions.
    """
    return item.get("page_type") in FILE_ROW_TYPES and is_test_path(item.get("target_path") or "")


def _downweight_test_pages(output: list[dict], query: str) -> None:
    """Scale test file_page relevance in place unless the query is about tests."""
    if _is_test_query(query):
        return
    for item in output:
        if item.get("relevance_score") and _is_test_page(item):
            item["relevance_score"] = round(item["relevance_score"] * _TEST_DOWNWEIGHT, 4)


def _sort_demoting_noise(output: list[dict], query: str) -> None:
    """Sort by relevance, ranking retrieval noise below every real page.

    Two classes crowd the top ranks on a plain implementation query and are
    demoted absolutely for their query class (the relevance score still orders
    each class among itself):

    - decision records — short dense titles win cosine similarity by a margin
      wider than the multiplicative down-weight (observed live as 5/5 irrelevant
      decisions for one query). Rationale questions are get_why's territory, so
      a why-shaped query ranks them naturally instead.
    - test file pages — a test is rarely a better first Read than the code it
      exercises. A query explicitly about tests ranks them naturally instead.
    """
    why = _is_why_shaped(query)
    test_focused = _is_test_query(query)

    def key(item: dict) -> tuple:
        pt = item.get("page_type")
        is_decision = (not why) and pt == "decision_record"
        is_test = (not test_focused) and _is_test_page(item)
        return (1 if (is_decision or is_test) else 0, -(item.get("relevance_score") or 0.0))

    output.sort(key=key)


def _norm_decision_title(title: str) -> str:
    """Normalize a decision title to collapse near-duplicate phrasings."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _dedup_decisions(output: list[dict]) -> list[dict]:
    """Collapse near-duplicate decision records by normalized title (order-preserving).

    Live search returned five near-identical "CLI incremental update regenerates
    only affected pages" variants filling positions 3-8. Callers dedup AFTER the
    score sort, so the surviving variant is the highest-scored one; non-decision
    pages always pass through untouched.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for item in output:
        if item.get("page_type") == "decision_record":
            key = _norm_decision_title(item.get("title", ""))
            if key and key in seen:
                continue
            seen.add(key)
        out.append(item)
    return out


async def _non_decision_fallback(ctx, query: str, fetch_limit: int) -> list[dict]:
    """Wider re-fetch keeping only non-decision pages.

    Guard for the window-saturation failure: when the entire over-fetched
    window is decision records, demotion has nothing to promote and a
    non-why query returns zero implementation pages. Fetch 4x wider, drop
    decisions, and let the caller merge the survivors.
    """
    results = []
    src = "vector"
    # Skipped on a keyless index, which then falls through to the FTS branch.
    if store_has_semantic_vectors(ctx.vector_store):
        try:
            results = await asyncio.wait_for(
                ctx.vector_store.search(query, limit=fetch_limit * 4),
                timeout=vector_search_timeout_s(),
            )
        except TimeoutError:
            _log.warning("Vector re-fetch timed out; falling back to full-text")
        except Exception:
            _log.debug("Vector re-fetch failed; falling back to full-text", exc_info=True)
    if not results:
        src = "fts"
        with contextlib.suppress(Exception):
            results = await ctx.fts.search(query, limit=fetch_limit * 4)

    out = []
    for r in results:
        if r.page_type == "decision_record":
            continue
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        out.append(
            {
                "page_id": r.page_id,
                "title": r.title,
                "page_type": r.page_type,
                "snippet": r.snippet,
                "relevance_score": r.score,
                "sources": [src],
            }
        )
    return out


async def _rescue_all_decision_window(
    ctx, output: list[dict], query: str, fetch_limit: int
) -> list[dict]:
    """Merge non-decision pages into an all-decision result window.

    No-op unless the query is non-why AND every current hit is a decision
    record. Deduplicates by page_id.
    """
    if not output or _is_why_shaped(query):
        return output
    if not all(item.get("page_type") == "decision_record" for item in output):
        return output
    fallback = await _non_decision_fallback(ctx, query, fetch_limit)
    seen = {item["page_id"] for item in output}
    output.extend(item for item in fallback if item["page_id"] not in seen)
    return output


# Slots at the tail of a concept window reserved for files reached through the
# symbol index rather than through their page. A generated file page renders
# only public symbols, so a question about a private helper or a local name has
# nothing to match in either the full-text index or the embedding. The symbol
# index is where those names live. One slot in three, taken from the weakest
# end of the window, is a cheap price for reaching a class of file the page
# retrievers structurally cannot see. No-op when the symbol leg finds nothing.
_SYMBOL_TAIL_DIVISOR = 3


def _symbol_tail_reserve(limit: int) -> int:
    """How many tail slots the symbol leg may take from a window of *limit*."""
    return limit // _SYMBOL_TAIL_DIVISOR


def _reserve_symbol_tail(output: list[dict], limit: int, kind: str | None) -> list[dict]:
    """Give the weakest window slots to symbol-leg files ranked past ``limit``.

    Concept hits keep the head. Files the symbol leg reached that fusion left
    outside the window take up to :func:`_symbol_tail_reserve` slots, and the
    concept hits they displaced fall in behind them (nothing is dropped).
    """
    reserve = _symbol_tail_reserve(limit)
    if reserve <= 0 or kind in ("config", "doc"):
        # config/doc windows are asking for pages with no symbols in them.
        return output
    extra = [item for item in output[limit:] if "symbol" in item.get("sources", ())][:reserve]
    if not extra:
        return output
    head = output[: limit - len(extra)]
    taken = {id(item) for item in head + extra}
    return head + extra + [item for item in output if id(item) not in taken]


# Path-prefix heuristics for the ``kind`` filter. We classify a hit's
# target_path against these prefixes; if none match, the hit falls into
# ``other`` and is dropped only when the caller asked for a specific kind.
# Tests are not in this list: they come from ``repowise.core.test_paths``, the
# same rules that stamped the file's ``is_test`` flag at ingestion.
_CONFIG_PATH_TOKENS = (
    "pyproject.toml",
    "package.json",
    "tsconfig",
    "setup.py",
    "setup.cfg",
    "/.github/",
    "dockerfile",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    "lockfile",
    "package-lock",
    "uv.lock",
    "poetry.lock",
)


def _classify_hit_kind(target_path: str, page_type: str) -> str:
    """Bucket a hit into implementation / test / config / doc.

    Pages without a file behind them (decision records, repo overviews,
    onboarding pages — all have empty ``target_path``) are docs: letting
    them fall through to the path heuristics classified every decision
    record as "implementation", so ``kind="implementation"`` returned
    decision pages instead of filtering them out.

    ``test`` is the union of tests and test support here: someone asking for
    ``kind="implementation"`` does not want a ``conftest.py`` back, and someone
    asking for ``kind="test"`` does.

    Config wins over test, which the token list this replaced never had to
    decide: ``.github/workflows/tests.yml`` is a workflow whatever it is named,
    and dropping it from ``kind="config"`` would be the more surprising answer.
    """
    tp = (target_path or "").lower()
    if page_type in ("module_page", "symbol_spotlight") or tp.endswith(".md"):
        return "doc"
    if not tp or page_type not in FILE_ROW_TYPES:
        return "doc"
    if any(tok in tp for tok in _CONFIG_PATH_TOKENS):
        return "config"
    # Original case, not ``tp``: the camel (FooTest.java) and .NET project-dir
    # (Foo.Tests/) rules are deliberately case-sensitive.
    if is_test_related_path(target_path):
        return "test"
    return "implementation"


# Every value :func:`_classify_hit_kind` can return; anything else can only ever
# match nothing, which is why an unknown kind is dropped rather than filtered on.
_VALID_KINDS = frozenset({"implementation", "test", "config", "doc"})


def _filter_by_kind(output: list[dict], kind: str | None) -> list[dict]:
    """Keep only hits classified as ``kind`` (no-op when ``kind`` is falsy).

    Runs on the over-fetched list BEFORE the limit cut so the caller still
    gets up to ``limit`` results of the requested kind.
    """
    if not kind:
        return output
    return [
        item
        for item in output
        if _classify_hit_kind(item.get("target_path", ""), item.get("page_type", "")) == kind
    ]


def _attach_paths(output: list[dict], page_info: dict) -> None:
    """Stamp canonical file paths and split symbol ids into their own field.

    A ``symbol_spotlight``'s target_path is ``file.py::Symbol``: a page id, and
    not a file path. Preserve it as ``symbol_id`` for ``get_symbol`` and expose
    only the file portion through ``target_path`` / ``file``.

    This lives in one function because it did not used to. The concept branch
    of ``search_codebase`` set ``file``; ``_search_single_repo``, which is what
    the hybrid and federated branches call, did not. A query carrying a
    CamelCase token routes to hybrid, so the tool's headline path kept serving
    page ids in a field read as a path. Measured on the dev-fix1 predictions:
    45.4% of the python served paths still carried ``::``, and resolving them
    takes gold containment from 19 to 39 of 50 instances.
    """
    for item in output:
        item["target_path"] = page_info.get(item["page_id"], item.get("target_path", ""))
        if "::" in item["target_path"]:
            item["symbol_id"] = symbol_identity(item["target_path"])
            item["target_path"] = path_identity(item["target_path"].split("::", 1)[0])
            item["file"] = item["target_path"]


def _drop_derivable_page_ids(results: list[dict]) -> list[dict]:
    """Strip ``page_id`` from every result that can rebuild it, in place.

    A page id is ``compute_page_id(page_type, target_path)`` — literally
    ``f"{page_type}:{target_path}"`` (``core/generation/models.py``) — and both
    halves ship in the same row. So the field is 229-369 characters per
    response, 7.8-10.3% of the payload, restating two of its own siblings. It
    is ranking plumbing: every internal use above keys the fused dict on it,
    and none of that needs to reach the wire.

    Conditional rather than unconditional, and the condition is the derivation
    itself. ``_attach_paths`` writes ``target_path: ""`` for a hit whose Page
    row did not load, and a row like that cannot be rebuilt — so it keeps its
    id instead of losing one. The rule is "drop it only where it is provably
    redundant", which is also the check: if this ever stops firing, the
    invariant it rests on has changed.

    Consumers rebuild with the same expression; ``ui/src/chat/source-citations``
    does exactly that, and used to skip any row whose ``page_id`` was missing.

    ``add_row_paths`` runs first and deletes ``target_path`` wherever it equals
    the new ``path``, so the id's second half is ``target_path`` when present
    and ``path`` otherwise. Without that fallback an ordinary file row rebuilt
    as ``"file_page:"`` and kept its id.
    """
    for item in results:
        # A symbol page's id ends in ``::Symbol`` and ``_attach_paths`` moved that
        # half into ``symbol_id``, so ``target_path`` is only its file.
        target = (
            item.get("symbol_id")
            if item.get("page_type") == "symbol_spotlight"
            else None
        ) or item.get("target_path") or item.get("path", "")
        derived = f"{item.get('page_type', '')}:{target}"
        # A row for a file with no page has an id that names no page.
        if item.get("page_id") == derived or item.get("page_type") == PAGELESS_FILE:
            item.pop("page_id", None)
    return results


def _page_language(ctx) -> str:
    """The language *ctx*'s structural page titles were generated in."""
    try:
        from repowise.core.repo_config import load_repo_config

        return str(load_repo_config(ctx.path).get("language") or "en")
    except Exception:
        return "en"


def _slim_served_rows(
    rows: list[dict], languages: dict[str | None, str] | None = None
) -> list[dict]:
    """The last step before a search reply leaves: one location per row.

    ``path`` (plus ``symbol_id`` on symbol rows) is the row's only location, so
    ``file``, a ``page_id`` that rebuilds from them, and a ``title`` that is just
    the structural label wrapped around them are all dropped. Each is dropped
    only where the caller can provably rebuild it. ``sources: ["fts"]`` goes when
    the reply's own ``_meta.semantic_search`` already says retrieval is
    full-text-only; an index with embeddings is unchanged.

    *languages* maps a row's ``repo`` alias (``None`` for a single-repo reply) to
    the language its titles were generated in.
    """
    add_row_paths(rows)
    _drop_derivable_page_ids(rows)
    fts_only = semantic_search_state() is False
    for row in rows:
        target = row.get("symbol_id") or row.get("path")
        if target and is_structural_title(
            (languages or {}).get(row.get("repo"), "en"),
            row.get("page_type", ""),
            target,
            row.get("title"),
        ):
            row.pop("title", None)
        if fts_only and row.get("sources") == ["fts"]:
            row.pop("sources", None)
    return rows


def _serve_snippets(results: list[dict]) -> None:
    """Drop each snippet's leading page heading, after ranking has read it."""
    for item in results:
        if item.get("snippet"):
            item["snippet"] = strip_leading_headings(item["snippet"])


def _drop_internal_ranking_fields(results: list[dict]) -> None:
    """Keep calibration diagnostics internal to the ranking pipeline."""
    for item in results:
        item.pop("_coverage", None)
        item.pop("_coverage_multiplier", None)
        item.pop("_confidence_score_factor", None)
        item.pop("_raw_score", None)


async def _load_page_info(
    session, output: list[dict], *, with_git: bool = False
) -> tuple[dict, set, dict]:
    """Batch-load target paths, tombstones, and git.

    Returns ``(page_info, tombstoned, git_map)`` where ``page_info`` maps
    page_id -> target_path, ``tombstoned`` is the set of tombstoned page_ids,
    and ``git_map`` maps file_path -> GitMetadata (empty unless ``with_git``).
    """
    page_ids = [item["page_id"] for item in output]
    res = await session.execute(
        select(Page.id, Page.target_path, Page.freshness_status).where(Page.id.in_(page_ids))
    )
    rows = res.all()
    page_info = {row[0]: row[1] for row in rows}
    tombstoned = {row[0] for row in rows if row[2] == "tombstone"}

    git_map: dict[str, GitMetadata] = {}
    if with_git:
        target_paths = [tp for tp in page_info.values() if tp]
        if target_paths:
            git_res = await session.execute(
                select(GitMetadata).where(GitMetadata.file_path.in_(target_paths))
            )
            git_map = {g.file_path: g for g in git_res.scalars().all()}
    return page_info, tombstoned, git_map


def _apply_freshness_boost(item: dict, gm: GitMetadata | None) -> None:
    """Nudge a recently-active file up as a bounded tie-breaker (in place).

    Additive, not multiplicative: the fused relevance score is RRF-based, which
    compresses adjacent ranks to roughly ``_FRESHNESS_TIEBREAK`` apart. A
    percentage boost on that scale would let a fresh rank-2 hit leapfrog a
    stale rank-0 hit, overriding retrieval relevance with recency. Sized below
    one rank step, freshness only orders hits retrieval already ranks close
    together.
    """
    if not gm or not item.get("relevance_score"):
        return
    c30 = gm.commit_count_30d or 0
    c90 = gm.commit_count_90d or 0
    if c30 > 0:
        recency = 1.0
    elif c90 > 0:
        recency = 0.5
    else:
        recency = 0.0
    item["relevance_score"] = round(item["relevance_score"] + _FRESHNESS_TIEBREAK * recency, 4)


def _assign_confidence(output: list[dict], score_key: str, target_key: str) -> None:
    """Derive ``target_key`` for each item from its ``score_key`` position (in place)."""
    if not output:
        return
    max_score = max((item.get(score_key) or 0) for item in output)
    for item in output:
        raw = item.get(score_key) or 0
        item[target_key] = round(raw / max_score, 2) if max_score > 0 else 0.0


def _drop_derivable_confidence(rows: list[dict]) -> list[dict]:
    """Drop ``confidence_score`` where the caller can rebuild it (in place).

    ``_assign_confidence`` sets it to ``round(relevance_score / top, 2)``, so on
    a single-repo reply it restates ``relevance_score`` on a 0-1 scale. A row
    that carries ``relation`` was capped under that value and keeps its score,
    as does every row of a reply whose top score is not positive (nothing to
    divide by). A federated reply never comes through here: its confidence is
    ranked on the fused RRF score, which the row does not carry.
    """
    top = max((row.get("relevance_score") or 0 for row in rows), default=0)
    if top <= 0:
        return rows
    for row in rows:
        if "relation" in row:
            continue
        if row.get("confidence_score") == round((row.get("relevance_score") or 0) / top, 2):
            row.pop("confidence_score", None)
    return rows


async def _wait_for_vector_store(ctx) -> None:
    """Block (bounded) until the vector store signals readiness, if it tracks it."""
    if ctx.vector_store_ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.vector_store_ready.wait(), timeout=30.0)


async def _safe_fts(ctx, query: str, limit: int) -> list:
    """FTS search, bounded and failure-swallowing (returns [] on error/timeout)."""
    if ctx.fts is None:
        return []
    with contextlib.suppress(Exception):
        return await asyncio.wait_for(ctx.fts.search(query, limit=limit), timeout=5.0)
    return []


async def _safe_vector(ctx, query: str, limit: int) -> list:
    """Vector search, bounded and failure-swallowing (returns [] on error/timeout).

    Readiness is awaited by the caller (``_wait_for_vector_store``) before the
    fused retrieve runs, so this path does not re-wait.

    Returns nothing on a keyless index. ``store_has_semantic_vectors`` explains
    why the mock's vectors cannot be ranked on; the reason this is enforced here
    rather than in ``_fused_retrieve`` is that this function is the only place
    the vector leg is entered, so a later caller inherits the guard.
    """
    if ctx.vector_store is None or not store_has_semantic_vectors(ctx.vector_store):
        return []
    try:
        return await asyncio.wait_for(
            ctx.vector_store.search(query, limit=limit), timeout=vector_search_timeout_s()
        )
    except TimeoutError:
        # Not suppressed silently: a timeout here drops the whole semantic leg
        # and the caller returns full-text-only hits that look like a normal
        # result set, which is how #1678 read as "semantic search finds nothing".
        _log.warning(
            "Vector search exceeded its %gs budget; returning no semantic hits. "
            "Raise it with %s=<seconds>.",
            vector_search_timeout_s(),
            _VECTOR_TIMEOUT_ENV,
        )
    except Exception:
        _log.debug("Vector search failed; returning no semantic hits", exc_info=True)
    return []


def _symbol_leg_target(fused: dict[str, dict], page_id: str) -> str:
    """The fused page a symbol-leg file page credits: the best page of its file.

    The leg names files, while the page legs may have reached the same file
    through a symbol page. Crediting only the file page id would leave that
    hit unlifted and add a weak twin that collapsing then drops.
    """
    path = page_id.partition(":")[2]
    best = None
    for pid, entry in fused.items():
        if file_path_of(entry["page_type"], pid.partition(":")[2]) != path:
            continue
        if best is None or entry["_rrf"] > fused[best]["_rrf"]:
            best = pid
    return best or page_id


def _fused_entry(r) -> dict:
    """Seed a fused-result dict from a retriever hit (RRF score added by caller)."""
    entry = {
        "page_id": r.page_id,
        "title": r.title,
        "page_type": r.page_type,
        "snippet": r.snippet,
        "_rrf": 0.0,
        "_sources": set(),
    }
    if r.page_type == PAGELESS_FILE:
        # No page row to load it from later.
        entry["target_path"] = pageless_path(r.page_id)
    return entry


async def _fused_retrieve(ctx, query: str, fetch_limit: int, page_type: str | None) -> list[dict]:
    """Retrieve by fusing FTS, vector and symbol search via Reciprocal Rank Fusion.

    The retrievers run in parallel; a hit's score is the sum of
    ``1/(rank + k)`` over the retrievers that surfaced it, scaled into the
    BM25 range the downstream relevance gates were tuned against. Each hit
    carries a ``sources`` list (``"fts"``, ``"vector"``, ``"symbol"``,
    ``"filename"``): a page found by several retrievers is a stronger match
    than one found by one mode. The symbol and filename legs also name files
    that have no page, as ``page_type: "file"`` rows.

    This mirrors get_answer's ``hybrid_retrieve`` so the two entry points rank
    the same corpus identically. The prior path ran vector search and fell
    back to FTS only when vector returned nothing, so a page FTS ranked highly
    but vector missed never surfaced here.

    A hit is admitted only when its raw retriever score clears
    ``_MIN_RELEVANCE_SCORE`` — the fused score is rank-based and would
    otherwise sit well above the floor for every window hit, letting an
    off-topic query return its nearest-but-unrelated neighbours. Rank position
    (for RRF) is the hit's place in the retriever's own list, unchanged by the
    floor.
    """
    fts_results, vec_results, sym_results, name_results = await asyncio.gather(
        _safe_fts(ctx, query, fetch_limit),
        _safe_vector(ctx, query, fetch_limit),
        _safe_symbol_search(ctx, query, pageless=True),
        _safe_filename_search(ctx, query),
    )

    fused: dict[str, dict] = {}
    for rank, r in enumerate(vec_results):
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        entry = fused.setdefault(r.page_id, _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + _RRF_K)
        entry["_sources"].add("vector")
    for rank, r in enumerate(fts_results):
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        entry = fused.setdefault(r.page_id, _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + _RRF_K)
        entry["_sources"].add("fts")
    # Files whose symbol names the query's words spell, paged or not. Weighted
    # below the page legs, as in get_answer; it has no raw score to floor.
    for rank, r in leg_ranks(sym_results):
        entry = fused.setdefault(_symbol_leg_target(fused, r.page_id), _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + _SYMBOL_LEG_RRF_K)
        entry["_sources"].add("symbol")
    # Files whose file name the query's words spell, weighted as a name match.
    # It adds files the other legs missed and never reorders a page they found.
    # A name of three or more words the query spells in full weighs like a page hit.
    for rank, (r, full) in leg_ranks(name_results):
        target = _symbol_leg_target(fused, r.page_id)
        if target in fused and r.page_type != PAGELESS_FILE:
            continue
        entry = fused.setdefault(target, _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + (_RRF_K if full else _FILENAME_LEG_RRF_K))
        entry["_sources"].add("filename")

    output: list[dict] = []
    for entry in fused.values():
        if page_type and entry["page_type"] != page_type:
            continue
        entry["relevance_score"] = round(entry.pop("_rrf") * _RRF_SCORE_SCALE, 4)
        entry["sources"] = sorted(entry.pop("_sources"))
        output.append(entry)
    output.sort(key=lambda item: item["relevance_score"], reverse=True)
    return output


async def _search_single_repo(
    ctx, query: str, limit: int, page_type: str | None, kind: str | None = None
) -> list[dict]:
    """Run search against a single repo context.

    Fuses FTS and vector retrieval via RRF (see ``_fused_retrieve``), so each
    hit carries a ``sources`` list naming the retrievers that surfaced it
    rather than a single backend label.

    The ``kind`` filter runs here, on the over-fetched list and BEFORE the
    limit cut — filtering after per-repo truncation returned fewer than
    ``limit`` results (frequently zero) in the federated path.
    """
    await _wait_for_vector_store(ctx)

    fetch_limit = _fetch_limit_for(limit, kind)
    output = await _fused_retrieve(ctx, query, fetch_limit, page_type)

    _downweight_decisions(output, query)
    output = await _rescue_all_decision_window(ctx, output, query, fetch_limit)

    # Attach target_path and drop excluded hits per repo (so federated search
    # honours each repo's own exclude_patterns) before ranking. Runs on the
    # over-fetched list so the kind filter below has real headroom. Load
    # precedes the sort/demotion so they see the page metadata they key on
    # (test demotion needs target_path to classify a test file page).
    if output:
        async with get_session(ctx.session_factory) as session:
            page_info, tombstoned, _ = await _load_page_info(session, output)
            path_index = await file_path_index(session, (await _get_repo(session)).id)
        output = [item for item in output if item["page_id"] not in tombstoned]
        _attach_paths(output, page_info)
        output = filter_dicts_by_key(output, "target_path", _get_exclude_spec(ctx.path))
        boost_named_paths(
            output, query, path_index.word_counts, len(path_index.paths), score_key="relevance_score"
        )

    _downweight_test_pages(output, query)
    _sort_demoting_noise(output, query)
    output = _dedup_decisions(output)

    output = _filter_by_kind(output, kind)
    return _collapse_by_file(output)[:limit]


async def _federated_search(
    query: str, limit: int, page_type: str | None, kind: str | None = None
) -> dict:
    """Search across all repos using Reciprocal Rank Fusion."""
    contexts = await _resolve_all_contexts()
    all_results = []

    for ctx in contexts:
        repo_results = await _search_single_repo(ctx, query, limit, page_type, kind)
        for rank, item in enumerate(repo_results):
            item["repo"] = ctx.alias
            item["rrf_score"] = 1.0 / (rank + 60)  # RRF constant k=60
        all_results.extend(repo_results)

    # Sort by RRF score and take top N
    all_results.sort(key=lambda x: x.get("rrf_score", 0), reverse=True)
    output = all_results[:limit]

    # Derive confidence from RRF position
    _assign_confidence(output, "rrf_score", "confidence_score")

    # No freshness here: results come from several repos at once, so there is
    # no single indexed commit to compare a live HEAD against.
    response: dict = {"results": output, "_meta": _build_meta()}
    # Drawn from every repo's ranked list, not the merged cut: a workspace
    # search that spends its window on module pages should still be able to
    # name files.
    if candidates := file_candidates(all_results, limit=limit):
        response["candidates"] = candidates
    # Last, so nothing above has to know the field is on its way out.
    _slim_served_rows(output, {ctx.alias: _page_language(ctx) for ctx in contexts})
    _serve_snippets(output)
    return response


def _refresh_served_targets(response: dict, _collector: OmissionCollector | None) -> None:
    """Re-derive target-scoped freshness from the hits that survived shedding.

    ``_meta.targets`` claims which files the response served. Shedding decides
    that, so this has to run after it or the claim names rows the caller never
    received.
    """
    if response.get("truncated") and isinstance(response.get("_meta"), dict):
        response["_meta"]["targets"] = _result_paths(response.get("results") or [])


def _refresh_served_targets_final(response: dict) -> None:
    """Same claim, re-derived after the final size guard may have cut again."""
    _refresh_served_targets(response, None)


register_post_shed("search_codebase", _refresh_served_targets)
register_post_enforce("search_codebase", _refresh_served_targets_final)


def _result_paths(results: list[dict]) -> list[str]:
    """File paths a result set serves, for target-scoped freshness.

    Symbol hits carry ``file``; page hits are resolved through their page type,
    so a module page's group key and an onboarding slot resolve to nothing
    rather than being handed on as if they were files. Freshness is per-file
    git metadata, and a directory has none, so a page id reaching here could
    only ever fail to match.

    An empty result set returns ``[]`` (meaning "no file content served",
    which suppresses the repo-level stale warning by design).
    """
    paths: list[str] = []
    for item in results:
        p = hit_file_path(item)
        if p:
            paths.append(p)
    return paths


def _grep_hint_for(query: str, names: Container[str] | None = None) -> str | None:
    """Zero-result recovery hint for identifier-shaped queries, else ``None``.

    Only attached when the search produced nothing (see call sites) — a
    populated result set needs no escape hatch. Points back into the tool
    surface first; Grep is named only for the one job it genuinely wins:
    an exhaustive literal sweep of every usage (e.g. before a rename).
    """
    if _looks_like_exact_token(query):
        return (
            f"No indexed match for identifier {query!r}. Retry with "
            'mode="symbol" (or check spelling/casing). ' + EXHAUSTIVE_SWEEP_HINT
        )
    if idents := _embedded_identifiers(query, names):
        shown = ", ".join(repr(t) for t in idents[:3])
        return (
            f"Query names identifier(s) {shown} but nothing matched. Search "
            'the identifier alone with mode="symbol", then pipe the hit '
            "into get_symbol for its body. " + EXHAUSTIVE_SWEEP_HINT
        )
    return None


async def _contexts_for(repo: str | None) -> list:
    """The repo contexts a structured search runs over (one, or all in 'all')."""
    if repo == "all":
        return await _resolve_all_contexts()
    return [await _resolve_repo_context(repo)]


def _lead_candidates(response: dict, issue: IssueFiles | None, limit: int) -> None:
    """Trace files lead ``candidates``. A named identifier's defining file only
    fills a free slot after the ranked ones: ranked above them, it displaces
    better hits on ordinary questions. Capped at ``limit``."""
    if issue is None or not (issue.traced or issue.named):
        return
    ranked = [c["path"] for c in response.get("candidates") or []]
    paths = list(dict.fromkeys(issue.traced + ranked + issue.named))
    response["candidates"] = [{"path": p} for p in paths[:limit]]


def _tag_repo(items: list[dict], ctx, multi: bool) -> None:
    if multi:
        for item in items:
            item["repo"] = ctx.alias


def _missing_named_symbols(
    candidates: list[str], symbols: list[dict], canonical: bool, concepts: list[dict]
) -> list[str]:
    """Code-shaped names the query asks after that no returned symbol matches
    exactly (or returned module path carries): they do not exist here. Judged
    per name, so an indexed name beside a missing one hides nothing. Their
    fuzzy neighbours would stand in for them, so the caller keeps only exact
    symbols; the pages are marked here as related to the question, not the
    symbol."""
    if canonical:
        return []
    page_paths = [c.get("target_path") or "" for c in concepts]
    missing = [
        c
        for c in candidates
        if _looks_like_code_name(c)
        and not _has_exact_symbol([c], symbols)
        and not _names_a_path(c, page_paths)
    ]
    if missing:
        _mark_not_the_named_symbol(concepts)
    return missing


async def _structured_search(
    query: str,
    limit: int,
    page_type: str | None,
    kind: str | None,
    symbol_kind: str | None,
    repo: str | None,
    mode: str,
    grep_hint: str | None,
    names: Container[str] | None = None,
) -> dict:
    """Run symbol / path / hybrid search and shape the response.

    Honours ``repo="all"`` (federates across contexts, then re-ranks by score),
    per-repo ``exclude_patterns`` and tombstones (enforced inside the
    single-repo helpers). The grep_hint is attached only as a fallback — when
    the structural index produced nothing, the agent still has a path forward.
    """
    contexts = await _contexts_for(repo)
    multi = len(contexts) > 1

    symbols: list[dict] = []
    # Symbol rows past each repo's ``limit``: they only backfill slots that
    # collapsing same-file rows frees, and never steer the exact-match signal.
    spare: list[dict] = []
    files: list[dict] = []
    indexed_paths: list[str] = []
    concepts: list[dict] = []

    # A hybrid query is prose wrapped around an identifier ("where is X
    # defined"). The symbol scorer ranks on token overlap, so handing it the
    # raw prose lets stopword-ish tokens ("is" -> is_ci, "filter" ->
    # FilterRegistry) outrank the identifier the question is actually about,
    # which then never reaches _has_exact_symbol and the response claims the
    # symbol is unindexed. Score symbols on the extracted identifiers instead.
    symbol_query = query
    canonical_symbol = _canonical_symbol_query(query)
    if canonical_symbol:
        symbol_query = canonical_symbol[1]
    if mode == "hybrid":
        _idents = _embedded_identifiers(query, names)
        if _idents:
            symbol_query = " ".join(_idents)

    # Only the hybrid window collapses symbol rows, so only it over-fetches
    # them; path hits are file pages, already one row per file. Symbol mode
    # takes every candidate so ``fuzzy_omitted`` counts the names an exact hit hides.
    fetch = limit * _FILE_WINDOW_OVERFETCH if mode == "hybrid" else limit
    if mode == "symbol":
        fetch = _MAX_CANDIDATES
    for ctx in contexts:
        if mode in ("symbol", "hybrid"):
            s = await search_symbols_single(
                ctx, symbol_query, fetch, symbol_kind=symbol_kind, kind=kind
            )
            _tag_repo(s, ctx, multi)
            symbols.extend(s[:limit])
            spare.extend(s[limit:])
        if mode == "path":
            f = await search_paths_single(ctx, query, limit)
            async with get_session(ctx.session_factory) as session:
                index = await file_path_index(session, (await _get_repo(session)).id)
            indexed_paths.extend(index.paths)
            _tag_repo(f, ctx, multi)
            files.extend(f)
        if mode == "hybrid":
            c = await _search_single_repo(ctx, query, limit, page_type, kind)
            for item in c:
                item["type"] = "page"
            await attach_hit_symbols(ctx, query, c)
            _tag_repo(c, ctx, multi)
            concepts.extend(c)

    symbols.sort(key=lambda x: -(x.get("score") or 0.0))
    spare.sort(key=lambda x: -(x.get("score") or 0.0))
    files.sort(key=lambda x: -(x.get("score") or 0.0))
    candidates = _identifier_candidates(query, mode, names)
    if mode == "symbol":
        symbols = _protect_exact_symbols(query, symbols)
    elif mode == "hybrid" and candidates:
        symbols = _protect_named_symbols(candidates, symbols)
    if mode == "path":
        files = _protect_exact_paths(query, files, indexed_paths)

    # Whether any returned symbol matches the query's identifier(s) exactly.
    # Computed once here so the hybrid interleave and the exact-match note below
    # agree on the same signal.
    exact = _has_exact_symbol(candidates, symbols) if candidates else False
    missing = _missing_named_symbols(candidates, symbols, bool(canonical_symbol), concepts)
    if missing:
        symbols = [s for s in symbols if _has_exact_symbol(candidates, [s])]
        spare = [s for s in spare if _has_exact_symbol(candidates, [s])]

    fuzzy_omitted = 0
    if mode == "symbol":
        # An exact hit is the answer; its fuzzy neighbours only cost bytes.
        # Summed over repos when federated, and a floor past the candidate cap.
        pool = symbols + spare
        named = [s for s in pool if _has_exact_symbol(candidates, [s])] if exact else []
        if named:
            ident = (canonical_symbol[1] if canonical_symbol else query).strip().lower()
            shown = {id(s) for s in named}
            fuzzy_omitted = sum(
                1 for s in pool if id(s) not in shown and ident in (s.get("name") or "").lower()
            )
        results = (named or symbols)[:limit]
    elif mode == "path":
        results = files[:limit]
    else:  # hybrid: interleave symbol matches and concept pages for new files
        # Federation appends per-repo concept lists in repo order — re-rank by
        # relevance so a strong page in repo B isn't buried under repo A's weak
        # ones. (Single-repo: already sorted upstream; this is a no-op.)
        concepts.sort(key=lambda x: -(x.get("relevance_score") or 0.0))
        window = _hybrid_window(query, symbols, concepts, limit, exact, names)
        # One row per file, so ``limit`` buys distinct files (mode "symbol"
        # stays row-per-symbol: overloads there are the answer). Collapsing
        # keeps the window's order and frees the slots same-file rows took;
        # the next pages, then the next symbols, fill them.
        served = {id(item) for item in window}
        rest = [item for item in concepts + symbols + spare if id(item) not in served]
        results = _collapse_by_file(window + rest)[:limit]
        # A code-location query wants files to open, so a module, onboarding
        # or decision page is dropped from the window. Its slot is not
        # refilled: on the retrieval guard a refill bought +0.01 coverage at
        # limit 5 for -0.01 precision at limit 10. Kept when the caller asked
        # for pages by type or kind="doc".
        if not page_type and kind != "doc":
            results = [item for item in results if hit_file_path(item)]

    repository = None
    if not multi:
        async with get_session(contexts[0].session_factory) as session:
            repository = await _get_repo(session)

    response: dict = {
        "results": results,
        "mode": mode,
        "_meta": _build_meta(repository=repository, targets=_result_paths(results)),
    }
    if fuzzy_omitted:
        response["fuzzy_omitted"] = fuzzy_omitted
    # Symbols first, then everything else the window holds: in symbol and
    # hybrid modes the ranked pool leads with symbol hits, and those are the
    # entries most likely to collapse onto one another (several symbols of one
    # file). Deduping them is the point.
    #
    # Bound to its own name, NOT to ``candidates``: that one holds the query's
    # identifiers, which the exact-match note below quotes. Rebinding it here
    # made the note quote file paths as if they were the identifiers asked for,
    # and — worse — made its gate true for any query with results at all, so a
    # prose query that names no identifier was told no symbol matched it.
    if file_cands := file_candidates(results, limit=limit):
        response["candidates"] = file_cands
    # Exact-match honesty: an identifier-shaped query whose target names no
    # indexed symbol still returns fuzzy neighbours. Say so, or the agent
    # anchors on a wrong hit that looks authoritative (their Alamofire
    # 44-overload read-spiral). Emit the boolean either way; a note only when
    # there is no exact hit to distinguish from the fuzz. ``candidates`` /
    # ``exact`` were computed above so ordering and this note stay consistent.
    if candidates:
        response["exact_match"] = exact and not missing
        if missing:
            shown = ", ".join(repr(c) for c in missing[:3])
            # An empty window has no page to qualify, and its grep_hint
            # already carries the sweep advice.
            pages = f" Any page here is {NOT_THE_NAMED_SYMBOL}." if results else ""
            sweep = "" if (grep_hint and not results) else " " + EXHAUSTIVE_SWEEP_HINT
            response["note"] = (
                f"No indexed symbol is named {shown}, so no symbol is returned "
                f"for it.{pages} Recheck the spelling, or search a shorter part "
                f"of the name.{sweep}"
            )
        elif not exact:
            shown = ", ".join(repr(c) for c in candidates[:3])
            response["note"] = (
                f"No indexed symbol exactly matches {shown}. The results are "
                "fuzzy neighbours ranked by token overlap — confirm a hit names "
                "what you meant before relying on it. If you expected an exact "
                "symbol, recheck spelling/casing. " + EXHAUSTIVE_SWEEP_HINT
            )
        elif fuzzy_omitted:
            response["note"] = (
                f"{fuzzy_omitted} other symbols contain this name; search a "
                "longer or partial name to list them."
            )
    if grep_hint and not results:
        response["grep_hint"] = grep_hint
    # Last, so nothing above has to know the field is on its way out. Paths
    # first, so a page whose target_path is dropped keeps its page_id.
    _slim_served_rows(
        results,
        {(ctx.alias if multi else None): _page_language(ctx) for ctx in contexts},
    )
    _serve_snippets(results)
    return response


@mcp.tool(
    surface_order=40,
    artifact_type="search_results",
    presentation="search_results",
    evidence_basis="inferred",
    recipes=(
        ToolRecipe(
            "find_raw_hits",
            'search_codebase(query="identifier or path", mode="auto")',
            ("search_codebase",),
        ),
    ),
)
async def search_codebase(
    query: str = "",
    limit: int = 5,
    page_type: str | None = None,
    kind: str | None = None,
    repo: str | None = None,
    mode: str = "auto",
    symbol_kind: str | None = None,
    pattern: str | None = None,
) -> dict:
    """Find code by concept, symbol, or path — hybrid codebase search.

    For questions (how/where/why), call get_answer instead: it runs this
    retrieval internally and returns a cited answer. Use this for raw
    ranked hits: enumerating matches, resolving an identifier to a symbol_id,
    or scoping get_context.

    mode="auto" routes by query shape: identifiers search symbols (symbol_id,
    path, line bounds for get_symbol), paths resolve files (for get_context),
    prose runs wiki-semantic search, and mixed queries run hybrid (symbol hits,
    then file-backed pages only). Decision records rank below file pages
    unless the query is why-shaped.

    Rows naming a file carry `path`; concept pages add `symbols`
    (name:line) the query matches; the top 3 add `matched_lines`
    (line, text), sample lines sharing its words. `candidates` lists up to `limit`
    distinct files to Read, best first. Identifier or literal queries also
    return `lines` (path, line, kind, text; definitions first) read from live
    files; when `complete` is true they are every match, so no grep is needed.

    Args:
        query: identifier, path, or natural language.
        limit: max results (default 5); distinct files outside mode="symbol"
            (same-file symbols in `symbols`).
        page_type: file_page (per-file docs) or module_page (subsystem
            pages); any stored page type filters.
        kind: implementation | test | config | doc (concept/symbol modes).
        repo: alias, or "all" for workspace-wide.
        mode: auto | concept | symbol | path | hybrid.
        symbol_kind: filter symbol hits (function|class|method|...).
        pattern: alias for query.
    """
    ignored: list[dict[str, Any]] = []
    # Hosts that defer tool schemas let a model guess grep's argument name.
    if pattern is not None and not query.strip():
        query = pattern
    elif pattern is not None:
        ignored.append(
            {"argument": "pattern", "values": [pattern], "valid": [], "superseded_by": "query"}
        )
    if not query.strip():
        return {
            "results": [],
            "error": "search_codebase requires `query` (or its alias `pattern`).",
            "_meta": _build_meta(),
        }
    # An unknown kind used to take the same ``return False`` as a kind that is
    # simply inapplicable, so a typo and a real empty result looked identical.
    kind = resolve_enum_argument(kind, _VALID_KINDS, argument="kind", ignored=ignored)
    # An unknown mode used to become ``auto`` with nothing said, so the caller
    # got a different search than it asked for. Modes have always been
    # case-insensitive; only a genuine miss is dropped, and under its own spelling.
    if mode is not None and mode.lower() in _VALID_MODES:
        mode = mode.lower()
    mode = resolve_enum_argument(mode, _VALID_MODES, argument="mode", ignored=ignored)
    # Loaded once so routing, candidates, ordering and the hint validate alike.
    names = await indexed_names(await _contexts_for(repo), query)
    grep_hint = _grep_hint_for(query, names)
    resolved_mode = _resolve_mode(query, mode, names)
    # Single repo only: a federated list has no one tree to name files in.
    issue: IssueFiles | None = None
    if repo != "all":
        try:
            issue = await issue_files(await _resolve_repo_context(repo), query, names)
        except Exception:
            _log.warning("search_codebase: issue file lookup failed", exc_info=True)

    if resolved_mode in ("symbol", "path", "hybrid"):
        structured = await _structured_search(
            query, limit, page_type, kind, symbol_kind, repo, resolved_mode, grep_hint, names
        )
        _lead_candidates(structured, issue, limit)
        await attach_line_hits(structured, query, resolved_mode, names, repo)
        attach_ignored_arguments(structured, ignored)
        return structured

    if repo == "all":
        # kind is filtered per-repo inside _search_single_repo, before each
        # repo's limit cut, so the fused list is already kind-pure and full.
        federated = await _federated_search(query, limit, page_type, kind)
        if grep_hint and not federated.get("results"):
            federated["grep_hint"] = grep_hint
        attach_ignored_arguments(federated, ignored)
        return federated

    ctx = await _resolve_repo_context(repo)

    async with get_session(ctx.session_factory) as session:
        # Validate repo exists in DB
        repository = await _get_repo(session)

    await _wait_for_vector_store(ctx)

    # Fuse FTS + vector via RRF (see _fused_retrieve) so a page either mode
    # ranks highly surfaces here, matching get_answer's retrieval. Always
    # over-fetch (see _fetch_limit_for): post-filters trim hits, and decision
    # down-weighting needs file pages inside the window to promote.
    fetch_limit = _fetch_limit_for(limit, kind)
    output = await _fused_retrieve(ctx, query, fetch_limit, page_type)

    _downweight_decisions(output, query)
    output = await _rescue_all_decision_window(ctx, output, query, fetch_limit)

    # Batch-lookup page target paths for the kind filter + git freshness boost
    if output:
        async with get_session(ctx.session_factory) as session:
            page_info, tombstoned, git_map = await _load_page_info(session, output, with_git=True)
            path_index = await file_path_index(session, repository.id)
        # Tombstoned pages document deleted/renamed files — never results.
        output = [item for item in output if item["page_id"] not in tombstoned]

        # Attach target_path to each item so the kind filter (path-prefix
        # heuristic) and downstream get_context callers can act on it, and
        # ``file`` wherever the page id is symbol-qualified.
        _attach_paths(output, page_info)

        output = filter_dicts_by_key(output, "target_path", _get_exclude_spec(ctx.path))

        for item in output:
            # Freshness boost: recently-active files rank higher
            target_path = page_info.get(item["page_id"])
            gm = git_map.get(target_path) if target_path else None
            _apply_freshness_boost(item, gm)

        # Re-sort by adjusted relevance with retrieval noise (decisions on
        # non-why queries, test pages on non-test queries) hard-demoted, then
        # collapse near-duplicate decisions to one.
        output = rerank_pages_first(output, query, score_key="relevance_score", floor=0.5)
        # After the coverage rerank, whose window-relative weights cannot tell
        # a word rare across the repo from one rare in these few hits.
        boost_named_paths(
            output, query, path_index.word_counts, len(path_index.paths), score_key="relevance_score"
        )
        _downweight_test_pages(output, query)
        _sort_demoting_noise(output, query)
        output = _dedup_decisions(output)

    output = _filter_by_kind(output, kind)
    # A file page and a symbol page of one file are one place to look.
    output = _collapse_by_file(output)
    # Files the page retrievers structurally cannot see (a private helper, a
    # local name, anything a file page's public-symbol table omits) get the
    # weakest tail slots. No-op when the symbol leg names nothing new.
    output = _reserve_symbol_tail(output, limit, kind)
    _drop_internal_ranking_fields(output)
    # The full ranked pool, kept before the caller's cut so ``candidates``
    # below can reach past it. See its comment for why that matters.
    ranked = list(output)
    output = output[:limit]
    await attach_hit_symbols(ctx, query, output)

    # Derive confidence_score from relative position in the result set.
    _assign_confidence(output, "relevance_score", "confidence_score")

    response: dict = {
        "results": output,
        "_meta": _build_meta(repository=repository, targets=_result_paths(output)),
    }
    if candidates := file_candidates(ranked, limit=limit):
        response["candidates"] = candidates
    _lead_candidates(response, issue, limit)
    if grep_hint and not output:
        response["grep_hint"] = grep_hint
    await attach_line_hits(response, query, resolved_mode, names, repo)
    attach_ignored_arguments(response, ignored)
    # Last, so nothing above has to know the field is on its way out.
    _slim_served_rows(output, {None: _page_language(ctx)})
    _drop_derivable_confidence(output)
    _serve_snippets(output)
    return response
