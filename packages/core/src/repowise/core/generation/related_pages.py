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

from collections import Counter
from typing import Any

import structlog

from ..co_change import parse_partners
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
    partners = parse_partners(git_meta.get("co_change_partners_json"))
    return [(p.file_path, p.weight) for p in partners]


def _module_siblings(
    module_groups: list[Any] | None,
) -> dict[str, list[str]]:
    """``path -> ordered sibling paths`` from the selected module groups."""
    siblings: dict[str, list[str]] = {}
    for group in module_groups or []:
        paths = list(getattr(group, "file_paths", ()) or ())
        for path in paths:
            # First group wins. The concept partition is disjoint, so this
            # only matters if a caller ever passes overlapping groups.
            siblings.setdefault(path, [p for p in paths if p != path])
    return siblings


def _module_of_path(pages: list[Any]) -> dict[str, str]:
    """``file path -> the module page id that documents it``.

    Read from the pages' own recorded membership rather than from the run's
    module groups, because the groups exist only during a full generation and
    this has to work on the persistence-layer backfill too — which is how
    every update flavor heals.

    Ties break on the lowest page id, the same rule ``assign_page_tree`` uses
    to decide file ownership. Two records of who documents a file that
    disagreed would put a page's neighbours somewhere its children are not.
    """
    out: dict[str, str] = {}
    for page in pages:
        if getattr(page, "page_type", "") != "module_page":
            continue
        for member in page.metadata.get("file_paths") or []:
            if not isinstance(member, str):
                continue
            current = out.get(member)
            if current is None or page.page_id < current:
                out[member] = page.page_id
    return out


def _module_adjacency(
    module_of: dict[str, str],
    import_edges: list[tuple[str, str]] | None,
    git_meta_map: dict[str, dict] | None,
) -> dict[str, dict[str, Counter]]:
    """Per-module neighbour counts, keyed ``module id -> reason -> Counter``.

    A module's neighbours are its members' neighbours, lifted to whichever
    module documents them and counted. The count is the evidence: two
    subsystems joined by forty import edges are more related than two joined
    by one, and nothing else here carries a weight that means anything at
    module scale.

    Edges inside a module are dropped — a module is not related to itself, and
    on a large group the self-edges would otherwise swamp every real one.
    """
    out: dict[str, dict[str, Counter]] = {}

    def bump(mod: str, reason: str, other: str) -> None:
        if not other or other == mod:
            return
        out.setdefault(mod, {}).setdefault(reason, Counter())[other] += 1

    for src, dst in import_edges or []:
        src_mod, dst_mod = module_of.get(src), module_of.get(dst)
        if not src_mod or not dst_mod:
            continue
        bump(src_mod, "imports", dst_mod)
        bump(dst_mod, "imported-by", src_mod)

    for path, meta in (git_meta_map or {}).items():
        mod = module_of.get(path)
        if not mod:
            continue
        for partner, _count in _co_change_partners(meta):
            bump(mod, "co-changes-with", module_of.get(partner, ""))

    return out


def _inherit_subtree_neighbours(
    pages: list[Any], adjacency: dict[str, dict[str, Counter]]
) -> None:
    """Give a chapter that owns no files the neighbours of its subtree.

    A chapter heading a subsystem whose files all belong to the pages beneath
    it has no members, so it has no edges of its own and would be the one page
    in the wiki with nothing across — which is the opposite of its job. Its
    neighbours are its subsystem's: the union of its descendants' edges, minus
    everything that lands back inside the subtree, because an edge to a page
    it already links down to is not news.

    Mutates *adjacency* in place. Only chapters with no members of their own
    are filled; one that documents loose files has real edges already.
    """
    targets = {
        page.page_id: page.target_path
        for page in pages
        if getattr(page, "page_type", "") == "module_page" and page.target_path
    }
    empty = [
        page
        for page in pages
        if getattr(page, "page_type", "") == "module_page"
        and not (page.metadata.get("file_paths") or [])
        and page.target_path
    ]
    for page in empty:
        prefix = page.target_path + "/"
        inside = {
            pid for pid, tp in targets.items() if tp == page.target_path or tp.startswith(prefix)
        }
        merged: dict[str, Counter] = {}
        for pid in inside:
            for reason, counts in adjacency.get(pid, {}).items():
                bucket = merged.setdefault(reason, Counter())
                for target, n in counts.items():
                    if target not in inside:
                        bucket[target] += n
        if merged:
            adjacency[page.page_id] = merged


def attach_related_pages(
    pages: list[GeneratedPage],
    *,
    import_edges: list[tuple[str, str]] | None = None,
    git_meta_map: dict[str, dict] | None = None,
    module_groups: list[Any] | None = None,
    pagerank: dict[str, float] | None = None,
    prior_page_ids: Any = None,
) -> None:
    """Populate ``metadata['related_pages']`` on file-backed and module pages.

    Mutates each :class:`GeneratedPage` in place. Idempotent — recomputed
    from scratch on every run.

    A module page's neighbours are its members' neighbours lifted to module
    scale and counted. Module pages were excluded while the pass was gated on
    ``FILE_BACKED_PAGE_TYPES``, which left the pages that most need to name
    their collaborators as the only ones that never did: a reader on a
    subsystem page could reach its files and its parent, and nothing across.

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

    # Module neighbours, computed once over the whole page set rather than per
    # page. Skipped entirely when no module page is present, which is the
    # common case on a scoped file-page run.
    module_of = _module_of_path(pages)
    module_adj = (
        _module_adjacency(module_of, import_edges, git_meta_map) if module_of else {}
    )
    if module_adj:
        _inherit_subtree_neighbours(pages, module_adj)

    attached_pages = 0
    total_entries = 0
    for page in pages:
        is_module = page.page_type == "module_page"
        if (page.page_type not in _FILE_BACKED and not is_module) or not page.target_path:
            continue
        path = page.target_path

        # Prose links win — related fills the gaps, never duplicates.
        prose_targets = {
            link.get("target_page_id") for link in page.metadata.get("wiki_links") or []
        }

        if is_module:
            by_reason = module_adj.get(page.page_id, {})
            candidates = {
                # Already page ids, so resolution is a no-op below. Weight is
                # the number of edges crossing the boundary.
                reason: [
                    (target, float(n))
                    for target, n in by_reason.get(reason, Counter()).most_common()
                ]
                # ``same-module`` is meaningless for a module: it *is* the
                # module. Left out rather than emitted empty, so the reason
                # priority below skips straight past it.
                for reason in ("imports", "imported-by", "co-changes-with")
            }
            candidates["same-module"] = []
        else:
            candidates = {
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
                target_id = target_path if is_module else index.resolve(target_path)
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
