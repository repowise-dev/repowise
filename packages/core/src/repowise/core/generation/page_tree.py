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
          file pages            (under the nearest module by path)
          api contracts, infra pages, symbol spotlights
        cycle pages
      anything unplaced

Everything is ordered by a total sort key, never by dict insertion, because a
tree that reshuffles between two runs of the same commit is the same defect as
an id that moves.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import GeneratedPage

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


def assign_page_tree(pages: list[GeneratedPage], layer_order_ids: list[str] | None = None) -> None:
    """Fill in ``parent_page_id``, ``display_order`` and ``section_number``.

    Mutates the pages in place. Safe to run on a partial set: an incremental
    run holds only the pages it regenerated, and a page whose parent is not in
    the set keeps the parent it can resolve or none at all, rather than being
    re-parented to something arbitrary that happens to be present.
    """
    by_type: dict[str, list[GeneratedPage]] = {}
    for page in pages:
        by_type.setdefault(page.page_type, []).append(page)

    root = next(iter(by_type.get("repo_overview", [])), None)
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
    for page in by_type.get("module_page", []):
        members = page.metadata.get("file_paths") or page.metadata.get("files") or []
        members = [m for m in members if isinstance(m, str)]
        page.parent_page_id = layer_parent(_dominant_layer(members, layer_of_file))
        module_paths.append(page.target_path)
    module_paths.sort(key=len, reverse=True)

    module_ids = {p.target_path for p in by_type.get("module_page", [])}

    def file_parent(path: str) -> str | None:
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

    # A spotlight belongs to the file it documents: "path/to/file.py::Symbol".
    for page in by_type.get("symbol_spotlight", []):
        owner = page.target_path.split("::", 1)[0]
        page.parent_page_id = f"file_page:{owner}" if owner else root_id

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

    _number(pages, root_id, layer_rank, module_ids)


def _number(
    pages: list[GeneratedPage],
    root_id: str | None,
    layer_rank: dict[str, int],
    module_ids: set[str],
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
