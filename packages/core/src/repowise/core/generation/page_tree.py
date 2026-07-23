"""Place every generated page in one tree.

The wiki has always had a hierarchy; it just lived in the reader. The web app
built a four-section spine in TypeScript, the editor extension reused that
component, breadcrumbs were re-derived from path strings a second time, and
the MCP server could not see any of it. Three readers deriving a tree from the
same flat list is three chances to disagree, and the agent surface got nothing.

This computes the tree once, from the pages themselves, and writes it onto
them. Parents are real pages rather than synthetic section rows, so the tree
adds no pages and changes no ids: re-indexing an unchanged repo still produces
exactly the rows it did before.

The shape follows what the readers were already doing:

    repo overview
      onboarding pages          (canonical reading order)
      architecture diagram
      layer pages               (dependency order from the layer spine)
        module pages            (grouped under their dominant layer)
          file pages            (under the nearest module, by member or path)
          api contracts, infra pages
            symbol spotlights   (under the file they document)
        cycle pages
      anything unplaced

A rung only appears when the pages for it exist. A repo indexed without a
curated knowledge graph has no layer pages, so its modules sit directly under
the overview and the tree is two levels shallower.

Everything is ordered by a total sort key, never by dict insertion, because a
tree that reshuffles between two runs of the same commit is the same defect as
an id that moves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import GeneratedPage


@dataclass
class TreeNode:
    """The part of a page the tree cares about.

    Lets the placement run over rows loaded from the store as well as over
    freshly generated pages, so an incremental update can rebuild the tree
    from the complete page set rather than from the handful it regenerated.
    ``GeneratedPage`` already carries these attributes and is passed directly.
    """

    page_id: str
    page_type: str
    target_path: str
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_page_id: str | None = None
    display_order: int = 0
    section_number: str | None = None


# Rank of each page type among siblings sharing a parent. Lower sorts first.
# Onboarding is ranked by its slot rather than this table.
_TYPE_RANK: dict[str, int] = {
    "onboarding": 0,
    "architecture_diagram": 1,
    "layer_page": 2,
    "module_page": 3,
    "scc_page": 4,
    "api_contract": 5,
    "infra_page": 6,
    "file_page": 7,
    "symbol_spotlight": 8,
}

_UNRANKED = len(_TYPE_RANK)


def _onboarding_rank(page: GeneratedPage) -> int:
    """Position of an onboarding page in the canonical reading order."""
    from .onboarding.slots import ONBOARDING_ORDER

    slot = str(page.metadata.get("onboarding_slot") or "")
    if not slot:
        # The slot is the tail of the target path for dedicated onboarding pages.
        slot = page.target_path.rsplit("/", 1)[-1]
    try:
        return ONBOARDING_ORDER.index(slot)
    except ValueError:
        return len(ONBOARDING_ORDER)


def _sort_key(page: GeneratedPage, layer_rank: dict[str, int]) -> tuple:
    """Total order among siblings.

    Ends in the page id so two pages that tie on every meaningful signal still
    have a defined order, which is what keeps the tree stable across runs.
    """
    if page.page_type == "onboarding":
        rank = _onboarding_rank(page)
    elif page.page_type == "layer_page":
        rank = layer_rank.get(page.target_path, len(layer_rank))
    else:
        rank = 0
    return (
        _TYPE_RANK.get(page.page_type, _UNRANKED),
        rank,
        page.target_path,
        page.page_id,
    )


def _dominant_layer(paths: Iterable[str], layer_of_file: dict[str, str]) -> str:
    """The layer most of these files belong to.

    Ties break on the layer id so the winner does not depend on iteration
    order. A module straddling two layers has to land somewhere, and landing
    in the same place every run matters more than which one it picks.
    """
    counts: dict[str, int] = {}
    for path in paths:
        layer = layer_of_file.get(path)
        if layer:
            counts[layer] = counts.get(layer, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _nearest_module(path: str, module_paths: list[str]) -> str:
    """The deepest module page whose target path contains *path*.

    ``module_paths`` must be sorted longest first so the first match is the
    most specific one.
    """
    for mod in module_paths:
        if path == mod or path.startswith(mod + "/"):
            return mod
    return ""


def assign_page_tree(
    pages: list[GeneratedPage] | list[TreeNode], layer_order_ids: list[str] | None = None
) -> None:
    """Fill in ``parent_page_id``, ``display_order`` and ``section_number``.

    Mutates the pages in place. Every parent it assigns is a page in the set:
    each rule resolves against a different source, so the result is checked
    rather than trusted. On a partial set that means a page whose real parent
    was not regenerated gets the nearest ancestor that is present, or none,
    which is why callers working from a subset rebuild from the store instead.
    """
    by_type: dict[str, list[GeneratedPage]] = {}
    for page in pages:
        by_type.setdefault(page.page_type, []).append(page)

    # Sorted, not "whichever came first": repo_overview is not swept, so a
    # store can hold a stale one from a renamed repo, and a root that flips
    # between runs reshuffles the whole tree.
    overviews = sorted(by_type.get("repo_overview", []), key=lambda p: p.page_id)
    root = overviews[0] if overviews else None
    root_id = root.page_id if root is not None else None

    layer_rank = {lid: i for i, lid in enumerate(layer_order_ids or [])}
    layer_ids = {p.target_path for p in by_type.get("layer_page", [])}

    # A file's layer comes from the provenance stamped on it at generation
    # time. Keyed on layer_id, never the display name, which drifts.
    layer_of_file: dict[str, str] = {}
    for page in by_type.get("file_page", []):
        lid = page.metadata.get("layer_id")
        if isinstance(lid, str) and lid:
            layer_of_file[page.target_path] = lid

    def layer_parent(layer_id: str) -> str | None:
        return f"layer_page:{layer_id}" if layer_id in layer_ids else root_id

    # --- Modules sit under their dominant layer ----------------------------
    module_paths: list[str] = []
    # Which module claims each file. A module's target_path is a directory
    # only when a curated knowledge graph named one; otherwise it is a
    # clustering ordinal like "community-3", which no file path can ever be a
    # prefix of. The member list is the only signal that works in both cases,
    # so it is the primary one and the path prefix is the fallback for pages
    # generated before members were recorded.
    module_of_file: dict[str, str] = {}
    module_pages = by_type.get("module_page", [])
    for page in module_pages:
        members = page.metadata.get("file_paths") or page.metadata.get("files") or []
        members = [m for m in members if isinstance(m, str)]
        module_paths.append(page.target_path)
        for member in members:
            # A file claimed by two modules goes to the one whose id sorts
            # first, so the winner does not depend on iteration order.
            current = module_of_file.get(member)
            if current is None or page.page_id < current:
                module_of_file[member] = page.page_id
    module_paths.sort(key=len, reverse=True)

    def file_parent(path: str) -> str | None:
        owner = module_of_file.get(path)
        if owner:
            return owner
        mod = _nearest_module(path, module_paths)
        if mod:
            return f"module_page:{mod}"
        layer = layer_of_file.get(path)
        if layer:
            return layer_parent(layer)
        return root_id

    for page_type in ("file_page", "api_contract", "infra_page"):
        for page in by_type.get(page_type, []):
            page.parent_page_id = file_parent(page.target_path)

    # Now that every file has found a module, a module that recorded no
    # members can borrow theirs. Pages written before members were recorded
    # would otherwise report an empty membership and land on the root, which
    # would keep every wiki indexed before this change permanently flat.
    claimed: dict[str, list[str]] = {}
    for page in by_type.get("file_page", []):
        if page.parent_page_id and page.parent_page_id.startswith("module_page:"):
            claimed.setdefault(page.parent_page_id, []).append(page.target_path)

    for page in module_pages:
        members = page.metadata.get("file_paths") or page.metadata.get("files") or []
        members = [m for m in members if isinstance(m, str)]
        if not members:
            members = claimed.get(page.page_id, [])
        page.parent_page_id = layer_parent(_dominant_layer(members, layer_of_file))

    # A spotlight belongs to the file it documents: "path/to/file.py::Symbol".
    # The file's own page may not exist: selection can spotlight a symbol in a
    # file that did not earn a page of its own. Fall back to where that file
    # would have sat rather than pointing at a row that is not there.
    file_page_ids = {p.page_id for p in by_type.get("file_page", [])}
    for page in by_type.get("symbol_spotlight", []):
        owner = page.target_path.split("::", 1)[0]
        owner_id = f"file_page:{owner}" if owner else ""
        if owner_id in file_page_ids:
            page.parent_page_id = owner_id
        else:
            page.parent_page_id = file_parent(owner) if owner else root_id

    # A cycle spans files, often across modules. Hang it off the layer its
    # members mostly live in rather than picking one member's module.
    for page in by_type.get("scc_page", []):
        members = page.metadata.get("files") or page.metadata.get("file_paths") or []
        members = [m for m in members if isinstance(m, str)]
        page.parent_page_id = layer_parent(_dominant_layer(members, layer_of_file))

    for page in by_type.get("layer_page", []):
        page.parent_page_id = root_id
    for page_type in ("onboarding", "architecture_diagram"):
        for page in by_type.get(page_type, []):
            page.parent_page_id = root_id

    if root is not None:
        root.parent_page_id = None

    # Last line of defence: a parent that is not in the set breaks every walk
    # of the tree, and the rules above each resolve against a different
    # source. Rather than trust them all to agree, drop any edge that does not
    # land on a real page. Falling back to the root reads as "somewhere in
    # this wiki", which is true; a dangling id reads as a page that exists.
    known = {p.page_id for p in pages}
    for page in pages:
        if page.parent_page_id is not None and page.parent_page_id not in known:
            page.parent_page_id = root_id if page.page_id != root_id else None

    _number(pages, root_id, layer_rank)


def _number(
    pages: list[GeneratedPage] | list[TreeNode],
    root_id: str | None,
    layer_rank: dict[str, int],
) -> None:
    """Assign sibling order and dotted section numbers by walking the tree."""
    children: dict[str | None, list[GeneratedPage]] = {}
    for page in pages:
        children.setdefault(page.parent_page_id, []).append(page)
    for siblings in children.values():
        siblings.sort(key=lambda p: _sort_key(p, layer_rank))

    numbered: set[str] = set()

    def walk(parent_id: str | None, prefix: str) -> None:
        for index, page in enumerate(children.get(parent_id, []), start=1):
            page.display_order = index
            page.section_number = f"{prefix}{index}" if prefix else str(index)
            numbered.add(page.page_id)
            walk(page.page_id, f"{page.section_number}.")

    if root_id is not None:
        root = next(p for p in pages if p.page_id == root_id)
        root.display_order = 0
        root.section_number = None
        numbered.add(root_id)
        walk(root_id, "")

    # Anything the walk did not reach: an incremental run holding pages whose
    # parent was not regenerated, or a page the tree has no place for. Number
    # them within their own group so they still carry an order rather than
    # silently sharing zero, but leave section_number unset, because a dotted
    # number that is not anchored to the root would be a lie.
    for _parent_id, siblings in sorted(children.items(), key=lambda kv: kv[0] or ""):
        index = 0
        for page in siblings:
            if page.page_id in numbered:
                continue
            index += 1
            page.display_order = index
