"""Noise demotion, decision rescue, symbol tail and kind filtering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re

from repowise.core.test_paths import is_test_path, is_test_related_path

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_search as _pkg
from repowise.server.mcp_server._helpers import vector_search_timeout_s
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES
from repowise.server.mcp_server._query_shape import (
    _DECISION_DOWNWEIGHT,
    _MIN_RELEVANCE_SCORE,
    _is_why_shaped,
)

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
      wider than the multiplicative down-weight. Rationale questions are
      get_why's territory, so a why-shaped query ranks them naturally instead.
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
