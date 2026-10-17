"""Served-row shaping: paths, freshness, slimming and freshness claims."""

from __future__ import annotations

from collections.abc import Container

from sqlalchemy import select

from repowise.core.generation.structural_labels import is_structural_title
from repowise.core.persistence.models import (
    GitMetadata,
    Page,
)

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_search as _pkg
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    register_post_enforce,
    register_post_shed,
)
from repowise.server.mcp_server._meta import EXHAUSTIVE_SWEEP_HINT
from repowise.server.mcp_server._page_paths import (
    PAGELESS_FILE,
    add_row_paths,
    hit_file_path,
)
from repowise.server.mcp_server._query_shape import (
    _embedded_identifiers,
    _looks_like_exact_token,
)
from repowise.server.mcp_server._references import path_identity, symbol_identity

# Freshness tie-breaker, added (not multiplied) to a hit's fused relevance.
# The RRF-fused score spaces adjacent ranks ~0.05 apart, so this stays under
# one rank step: recency orders otherwise-comparable hits without overriding
# retrieval relevance. Recency itself scales it (0.5 for 90-day activity, 1.0
# for 30-day).
_FRESHNESS_TIEBREAK = 0.03


def _attach_paths(output: list[dict], page_info: dict) -> None:
    """Stamp canonical file paths and split symbol ids into their own field.

    A ``symbol_spotlight``'s target_path is ``file.py::Symbol``: a page id, and
    not a file path. Preserve it as ``symbol_id`` for ``get_symbol`` and expose
    only the file portion through ``target_path`` / ``file``. Every search
    branch calls this one function so they agree on the row's location.
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
    halves ship in the same row, so the field restates two of its siblings. It
    is ranking plumbing: internal uses key the fused dict on it, and none of
    that needs to reach the wire.

    Conditional rather than unconditional, and the condition is the derivation
    itself. ``_attach_paths`` writes ``target_path: ""`` for a hit whose Page
    row did not load, and a row like that cannot be rebuilt — so it keeps its
    id instead of losing one. The rule is "drop it only where it is provably
    redundant", which is also the check: if this ever stops firing, the
    invariant it rests on has changed.

    Consumers rebuild with the same expression (``ui/src/chat/source-citations``).

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
    fts_only = _pkg.semantic_search_state() is False
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
            item["snippet"] = _pkg.strip_leading_headings(item["snippet"])


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
