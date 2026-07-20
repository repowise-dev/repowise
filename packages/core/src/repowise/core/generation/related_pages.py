"""Post-generation related pages — connect pages by graph evidence.

Runs after ``interlinking`` in the post-generation pass chain. Where
``wiki_links`` depend on the LLM mentioning a file in prose, this pass
derives neighbors deterministically from signals the pipeline already
computed: import edges, co-change partners, and module membership.
Resolved hits land in ``page.metadata["related_pages"]``; the reader's
Related rail merges them with the prose-derived links.

No LLM call — pure dict lookups over the run's page set, so a page whose
prose never names its collaborators is still connected to them.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from .interlinking import FILE_BACKED_PAGE_TYPES as _FILE_BACKED
from .interlinking import LinkIndex
from .models import GeneratedPage

log = structlog.get_logger(__name__)

# Reasons in priority order — a target reachable via several signals is
# reported once, under the strongest one.
_REASON_PRIORITY = ("imports", "imported-by", "co-changes-with", "same-module")

_PER_REASON_CAP = 5
_TOTAL_CAP = 12


def _co_change_partners(git_meta: dict | None) -> list[tuple[str, float]]:
    """``(partner_path, co_change_count)`` pairs, strongest first."""
    if not git_meta:
        return []
    raw = git_meta.get("co_change_partners_json") or "[]"
    try:
        partners = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: list[tuple[str, float]] = []
    for p in partners:
        if not isinstance(p, dict):
            continue
        path = p.get("file_path")
        if not path:
            continue
        out.append((path, float(p.get("co_change_count") or 0.0)))
    out.sort(key=lambda t: -t[1])
    return out


def _module_siblings(
    module_groups: list[Any] | None,
) -> dict[str, list[str]]:
    """``path -> ordered sibling paths`` from the selected module groups."""
    siblings: dict[str, list[str]] = {}
    for group in module_groups or []:
        paths = list(getattr(group, "file_paths", ()) or ())
        for path in paths:
            # First group wins — curated groups precede fallbacks upstream.
            siblings.setdefault(path, [p for p in paths if p != path])
    return siblings


def attach_related_pages(
    pages: list[GeneratedPage],
    *,
    import_edges: list[tuple[str, str]] | None = None,
    git_meta_map: dict[str, dict] | None = None,
    module_groups: list[Any] | None = None,
    pagerank: dict[str, float] | None = None,
    prior_page_ids: Any = None,
) -> None:
    """Populate ``metadata['related_pages']`` on file-backed pages.

    Mutates each :class:`GeneratedPage` in place. Idempotent — recomputed
    from scratch on every run.

    ``prior_page_ids`` widens resolution beyond this run's page set: on an
    incremental update only the affected pages are regenerated, so without
    the persisted ids every neighbor outside the diff would fail to resolve
    and the update would overwrite good metadata with near-empty lists.
    Current-run pages always win over a prior id; the reader drops entries
    whose target no longer exists, so stale prior ids are harmless.
    """
    if not pages:
        return

    index = LinkIndex.build(pages)
    index.add_prior_page_ids(prior_page_ids)
    titles = {p.page_id: p.title for p in pages}
    for pid in prior_page_ids or ():
        _, _, tpath = str(pid).partition(":")
        if tpath:
            titles.setdefault(str(pid), tpath)
    pr = pagerank or {}

    # Adjacency from the import graph: src imports dst.
    imports_of: dict[str, list[str]] = {}
    imported_by: dict[str, list[str]] = {}
    for src, dst in import_edges or []:
        imports_of.setdefault(src, []).append(dst)
        imported_by.setdefault(dst, []).append(src)

    siblings = _module_siblings(module_groups)

    attached_pages = 0
    total_entries = 0
    for page in pages:
        if page.page_type not in _FILE_BACKED or not page.target_path:
            continue
        path = page.target_path

        # Prose links win — related fills the gaps, never duplicates.
        prose_targets = {
            link.get("target_page_id") for link in page.metadata.get("wiki_links") or []
        }

        candidates: dict[str, list[tuple[str, float]]] = {
            # Order within a reason: strongest evidence first. Import edges
            # carry no weight of their own, so central targets go first.
            "imports": sorted(
                ((p, pr.get(p, 0.0)) for p in imports_of.get(path, ())),
                key=lambda t: -t[1],
            ),
            "imported-by": sorted(
                ((p, pr.get(p, 0.0)) for p in imported_by.get(path, ())),
                key=lambda t: -t[1],
            ),
            "co-changes-with": _co_change_partners((git_meta_map or {}).get(path)),
            "same-module": [(p, 0.0) for p in siblings.get(path, ())],
        }

        seen: set[str] = {page.page_id} | prose_targets
        related: list[dict] = []
        for reason in _REASON_PRIORITY:
            kept = 0
            for target_path, weight in candidates[reason]:
                if kept >= _PER_REASON_CAP or len(related) >= _TOTAL_CAP:
                    break
                target_id = index.resolve(target_path)
                if target_id is None or target_id in seen:
                    continue
                seen.add(target_id)
                kept += 1
                related.append(
                    {
                        "target_page_id": target_id,
                        "title": titles.get(target_id, target_path),
                        "reason": reason,
                        "weight": round(weight, 4),
                    }
                )
            if len(related) >= _TOTAL_CAP:
                break

        page.metadata["related_pages"] = related
        if related:
            attached_pages += 1
            total_entries += len(related)

    log.info(
        "related_pages.attached",
        pages_with_related=attached_pages,
        total_entries=total_entries,
    )


def file_import_edges(graph_builder: Any) -> list[tuple[str, str]]:
    """``(src, dst)`` import edges between file nodes (src imports dst).

    Shared by the persistence-layer backfill call sites; mirrors the
    orchestrator's ``_GenerationRun._file_import_edges``.
    """
    edges: list[tuple[str, str]] = []
    try:
        for src, dst in graph_builder.graph().edges():
            if isinstance(src, str) and isinstance(dst, str):
                edges.append((src, dst))
    except Exception:
        pass
    return edges


__all__ = ["attach_related_pages", "file_import_edges"]
