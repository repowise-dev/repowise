"""Noise demotion, decision rescue, symbol tail and kind filtering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Container, Mapping

from repowise.core.ingestion.models import VALUE_SYMBOL_KINDS
from repowise.core.test_paths import is_test_related_path

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_search as _pkg
from repowise.server.mcp_server._helpers import vector_search_timeout_s
from repowise.server.mcp_server._origin_rank import (
    TEST_QUERY_RE,
    demoted_origins,
    is_demoted_row,
    is_test_query,
)
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES
from repowise.server.mcp_server._query_shape import (
    _DECISION_DOWNWEIGHT,
    _MIN_RELEVANCE_SCORE,
    _is_why_shaped,
)
from repowise.server.mcp_server._symbol_lookup import spotlight_owners

_log = logging.getLogger("repowise.mcp.search")


def _downweight_decisions(output: list[dict], query: str) -> None:
    """Scale decision_record relevance in place unless the query is why-shaped."""
    if _is_why_shaped(query):
        return
    for item in output:
        if item.get("page_type") == "decision_record" and item.get("relevance_score"):
            item["relevance_score"] = round(item["relevance_score"] * _DECISION_DOWNWEIGHT, 4)


# Test pages win retrieval on vocabulary shared with the code they exercise, but
# are rarely a better first Read: demote them unless the query is about tests.
_TEST_DOWNWEIGHT = 0.6

_TEST_QUERY_RE = TEST_QUERY_RE


def _is_test_query(query: str) -> bool:
    """True when the query is explicitly about tests, so test pages rank naturally."""
    return is_test_query(query)


_TEST_ORIGIN = frozenset({"test"})


def _is_test_page(item: dict, origins: Mapping[str, str] | None = None) -> bool:
    """True when a hit is a whole-file row for test code (``code_origin`` test).

    Test support (a mock, a fixture module, ``conftest.py``) counts: a query
    that wants one names it ("mock", "fixtures"), which makes it a test query
    and ranks every test row naturally. *origins* holds the stored origins.
    """
    return is_demoted_row(item, _TEST_ORIGIN, origins)



def _downweight_test_pages(
    output: list[dict], query: str, origins: Mapping[str, str] | None = None
) -> None:
    """Scale test, generated and vendored file rows in place unless the query asks for them."""
    demoted = demoted_origins(query)
    for item in output:
        if item.get("relevance_score") and is_demoted_row(item, demoted, origins):
            item["relevance_score"] = round(item["relevance_score"] * _TEST_DOWNWEIGHT, 4)


_REPO_WIDE_PAGE_TYPES = frozenset({"repo_overview", "architecture_diagram", "onboarding"})
_REPO_WIDE_QUERY = re.compile(
    r"\b(?:overview|architecture|onboarding|getting\s+started|how\s+it\s+works)\b", re.IGNORECASE
)


def _sort_demoting_noise(
    output: list[dict],
    query: str,
    minor: Container[str] = frozenset(),
    origins: Mapping[str, str] | None = None,
) -> None:
    """Sort by relevance, ranking retrieval noise below every real page.

    Two classes crowd the top ranks on a plain implementation query and are
    demoted absolutely for their query class (the relevance score still orders
    each class among itself):

    - decision records — short dense titles win cosine similarity by a margin
      wider than the multiplicative down-weight. Rationale questions are
      get_why's territory, so a why-shaped query ranks them naturally instead.
    - test, generated and vendored file rows (``code_origin``) — rarely a
      better first Read than the code that ships. A query asking for that kind
      of file (tests, mocks, generated code) ranks them naturally instead.
    - *minor* spotlight page ids (:func:`_minor_spotlights`) — a page about one
      constant or one-line symbol that only full-text search matched shares the
      query's words, not its subject.
    - repo-wide pages (overview, getting started) on an index with no embedder:
      they hold most of the repo's common words, so keyword matching ranks them
      on any question (#5049). A query asking for an overview ranks them naturally.
    """
    why = _is_why_shaped(query)
    demoted = demoted_origins(query)
    repo_wide_noise = _pkg.semantic_search_state() is False and not _REPO_WIDE_QUERY.search(query)

    def key(item: dict) -> tuple:
        pt = item.get("page_type")
        is_decision = (not why) and pt == "decision_record"
        is_test = is_demoted_row(item, demoted, origins)
        is_minor = item.get("page_id") in minor
        is_repo_wide = repo_wide_noise and pt in _REPO_WIDE_PAGE_TYPES
        noise = is_decision or is_test or is_minor or is_repo_wide
        return (1 if noise else 0, -(item.get("relevance_score") or 0.0))

    output.sort(key=key)


async def _minor_spotlights(session, output: list[dict], query: str, repo_id: str) -> set[str]:
    """Page ids of full-text-only spotlights on a value or one-line symbol the query does not name."""
    words = set(re.findall(r"[\w$]+", query.lower()))
    pending: dict[tuple[str, str], str] = {}
    for item in output:
        if item.get("page_type") != "symbol_spotlight" or item.get("sources") != ["fts"]:
            continue
        path, _, name = item["page_id"].partition(":")[2].partition("::")
        if name and name.lower() not in words:
            pending[(path, name)] = item["page_id"]
    owners = await spotlight_owners(session, repo_id, set(pending))
    return {pending[key] for key, sym in owners.items() if _is_minor_symbol(sym)}


def _is_minor_symbol(sym) -> bool:
    """A stored value, or a body on one line."""
    if sym.kind in VALUE_SYMBOL_KINDS:
        return True
    return sym.start_line is not None and sym.end_line is not None and sym.end_line <= sym.start_line


def _norm_decision_title(title: str) -> str:
    """Normalize a decision title to collapse near-duplicate phrasings."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _dedup_decisions(output: list[dict]) -> list[dict]:
    """Collapse near-duplicate decision records by normalized title (order-preserving).

    Callers dedup AFTER the score sort, so the surviving variant is the
    highest-scored one; non-decision pages always pass through untouched.
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
    if _pkg.store_has_semantic_vectors(ctx.vector_store):
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
