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
      module pages              (reading order; stamped with their layer)
        module pages            (under the module heading their directory)
        file pages              (under the nearest module, by member or path)
        api contracts, infra pages (under the module for their directory)
          symbol spotlights     (under the file they document)
      flow pages                (the walkthroughs, after the sections)
      cycle pages               (stamped with the layer they belong to)
      project activity          (closes the outline)
      anything unplaced

Only the outline is numbered, two levels deep (see :func:`_number`).

A rung only appears when the pages for it exist.

Layers are the one grouping that is *not* a rung here. The layer is recorded
on each module and cycle page (``layer_id`` for joining, ``layer_name`` for
display) and a reader-facing tree builds the layer rows from that, the same
way the Onboarding folder is built from a slot stamped on its members.

Everything is ordered by a total sort key, never by dict insertion, because a
tree that reshuffles between two runs of the same commit is the same defect as
an id that moves.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Container, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..ids import is_external
from .layers import import_balance, is_support_layer, reading_bucket
from .models import GeneratedPage
from .nav_titles import contextual_label, nav_label
from .onboarding.slots import NAV_SECTION_ACTIVITY, SLOT_ACTIVE_LANDSCAPE


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
    #: The stored title, for telling a part that only restates its section.
    title: str = ""


# Rank of each page type among siblings sharing a parent. Lower sorts first.
# Onboarding is ranked by its slot rather than this table.
_TYPE_RANK: dict[str, int] = {
    "onboarding": 0,
    "architecture_diagram": 1,
    "module_page": 2,
    # Walkthroughs follow the sections they cross: a reader meets "what happens
    # when you run init" after the parts it walks through have names.
    "flow_page": 3,
    "scc_page": 4,
    "api_contract": 5,
    "infra_page": 6,
    "file_page": 7,
    "symbol_spotlight": 8,
}

# Onboarding slots that describe the project's recent activity rather than how
# it works. They close the outline as "Project activity" instead of opening it.
PROJECT_ACTIVITY_SLOTS: frozenset[str] = frozenset({SLOT_ACTIVE_LANDSCAPE})

# Page types that form the numbered outline. Everything else (files, symbols,
# contracts, infra, cycles) is reference material and carries no number.
_NUMBERED_TYPES: frozenset[str] = frozenset(
    {"onboarding", "architecture_diagram", "module_page", "flow_page"}
)
# Numbers go two levels deep ("4", "4.2"). Deeper rows are reachable in the
# tree but unnumbered: "14.2.1.5.1" is noise, not a reference a page can cite.
_MAX_NUMBER_DEPTH = 2

_UNRANKED = len(_TYPE_RANK)
_UNRANKED_READER = (2,)

# Page types that sit directly under the overview whatever their files say: the
# reading path, and the walkthroughs a reader-facing tree groups after the
# sections.
_START_HERE_TYPES: tuple[str, ...] = ("onboarding", "flow_page", "architecture_diagram")


# Page types a reader-facing tree groups into layer rows. Their layer comes
# from the provenance :func:`assign_page_tree` stamps on them, not from a
# parent page, so nothing fails when it is missing — the page just falls out
# of its group. :func:`measure_layer_grouping` is what makes that visible.
GROUPED_PAGE_TYPES: frozenset[str] = frozenset({"module_page", "scc_page"})


@dataclass(frozen=True)
class LayerGroupingReport:
    """How far layer provenance reached across one run's pages.

    ``grouped`` and ``ungrouped`` are counted over :data:`GROUPED_PAGE_TYPES`
    only. Read ``measured`` before reading ``grouped``: a run that produced no
    groupable page at all has a zero that says nothing, which is the opposite
    fact from "every groupable page was left ungrouped".
    """

    grouped: int = 0
    ungrouped: int = 0

    @property
    def total(self) -> int:
        return self.grouped + self.ungrouped

    @property
    def measured(self) -> bool:
        return self.total > 0

    def summary_line(self) -> str:
        """One line fit for a report table, honest about the not-run case."""
        if not self.measured:
            return "not computed (no groupable pages)"
        if not self.ungrouped:
            return f"all {self.total} pages carry a layer"
        return f"{self.ungrouped} of {self.total} pages carry no layer"


def measure_layer_grouping(
    pages: Iterable[GeneratedPage] | Iterable[TreeNode],
) -> LayerGroupingReport:
    """Count how many groupable pages carry a resolved layer id.

    Call after :func:`assign_page_tree`, which is what stamps them. An empty
    string counts as ungrouped: the stamper writes nothing when the layer could
    not be resolved, so a blank id means something else wrote it.
    """
    grouped = 0
    ungrouped = 0
    for page in pages:
        if page.page_type not in GROUPED_PAGE_TYPES:
            continue
        layer_id = page.metadata.get("layer_id")
        if isinstance(layer_id, str) and layer_id:
            grouped += 1
        else:
            ungrouped += 1
    return LayerGroupingReport(grouped=grouped, ungrouped=ungrouped)


def onboarding_slot(page: TreeNode | GeneratedPage) -> str:
    """The onboarding slot a page fills: stamped, or the tail of its target."""
    slot = str(page.metadata.get("onboarding_slot") or "")
    return slot or page.target_path.rsplit("/", 1)[-1]


# The stamp a page carries when it belongs under "Project activity"
# (``onboarding.slots.slot_metadata``). A page written before the stamp existed
# is recognised by its slot.
NAV_SECTION_KEY = "nav_section"


def is_project_activity(page: TreeNode | GeneratedPage) -> bool:
    """Whether *page* belongs under "Project activity" rather than the reading path."""
    if page.metadata.get(NAV_SECTION_KEY) == NAV_SECTION_ACTIVITY:
        return True
    return page.page_type == "onboarding" and onboarding_slot(page) in PROJECT_ACTIVITY_SLOTS


def _onboarding_rank(page: GeneratedPage) -> int:
    """Position of an onboarding page in display order."""
    from .onboarding.slots import ONBOARDING_SLOTS

    slot = onboarding_slot(page)
    try:
        return ONBOARDING_SLOTS.index(slot)
    except ValueError:
        return len(ONBOARDING_SLOTS)


def _sort_key(page: GeneratedPage, reader_rank: dict[str, tuple[int, ...]]) -> tuple:
    """Total order among siblings.

    Ends in the page id so two pages that tie on every meaningful signal still
    have a defined order, which is what keeps the tree stable across runs.

    *reader_rank* orders the top-level chapters (see ``_reader_ranks``) ahead of
    the namer's order, which is only meaningful between the leaves it named:
    a top-level chapter takes its namer position from its own loose files, so
    by that order the engine read fifteenth, after the editor extension.
    """
    if page.page_type == "onboarding":
        rank = _onboarding_rank(page)
    elif page.page_type == "module_page":
        rank = _concept_rank(page)
    else:
        rank = 0
    return (
        # Project activity closes the outline, after every other rung.
        _UNRANKED + 1 if is_project_activity(page) else _TYPE_RANK.get(page.page_type, _UNRANKED),
        # Unranked pages share one value, after every ranked top-level chapter.
        reader_rank.get(page.page_id, _UNRANKED_READER),
        rank,
        page.target_path,
        page.page_id,
    )


def _concept_rank(page: GeneratedPage) -> int:
    """Where the namer put this concept page in the reading order.

    A concept page's siblings are the other concept pages under the same
    layer, and sorting them by target path puts the reader wherever the
    alphabet lands. The namer already decided an order as a narrative, so use
    it when it exists.

    Pages with no order come from a run that never named them (keyless, or a
    wiki written before naming shipped) and sort after the named ones, where
    the tie-break on ``target_path`` gives them the order they have today.
    Deliberately not zero: that is a real position, and borrowing it would
    interleave unnamed pages through the narrative.
    """
    order = page.metadata.get("concept_order")
    return order if isinstance(order, int) else _UNRANKED_CONCEPT


# Larger than any real position, so unnamed pages sort after named ones for
# any outline the grouper's size ladder can produce.
_UNRANKED_CONCEPT = 1_000_000


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


def _listed_members(page: TreeNode | GeneratedPage) -> list[str]:
    """The files a module or cycle page records as its own."""
    members = page.metadata.get("file_paths") or page.metadata.get("files") or []
    return [m for m in members if isinstance(m, str)]


def _borrowed_members(
    page: TreeNode | GeneratedPage,
    module_pages: list[GeneratedPage] | list[TreeNode],
    owned: dict[str, list[str]],
) -> list[str]:
    """The members of the module pages under a rollup's directory.

    A rollup overview owns no files of its own, so it has nothing to place it
    by and would otherwise land on the root. Borrowing its children's members
    puts it under the same layer as the pages it summarises.
    """
    prefix = page.target_path + "/"
    return [
        member
        for other in module_pages
        if other is not page and other.target_path.startswith(prefix)
        for member in owned[other.page_id]
    ]


def _module_heads(
    module_pages: list[GeneratedPage] | list[TreeNode], owned: dict[str, list[str]]
) -> dict[str, str]:
    """Directory -> id of the module page that heads the modules beneath it.

    A chapter heads its subtree by definition. A leaf heads one too when it
    documents files sitting directly in its directory: a module for
    ``pipeline/resume`` belongs under the page for ``pipeline``, whatever the
    grouper decided to call that page, or the reader cannot predict where a
    directory's documentation lives. A page that only borrows a directory's
    name (a roll-up of sibling packages, a run of sibling folders targeted at
    the first of them) owns nothing directly there and heads nothing.
    """
    return {
        page.target_path: page.page_id
        for page in module_pages
        if is_module_head(
            page.target_path, owned[page.page_id], bool(page.metadata.get("is_chapter"))
        )
    }


def is_module_head(target_path: str, members: Iterable[str], is_chapter: bool) -> bool:
    """Whether the module page at *target_path* heads the modules beneath it.

    The one rule the tree nests by and a chapter's child links follow; see
    :func:`_module_heads` for why. *members* are the files the page owns.
    """
    if not target_path:
        return False
    return is_chapter or any(m.rpartition("/")[0] == target_path for m in members)


def nearest_head(target_path: str, heads: Container[str]) -> str | None:
    """The closest ancestor directory of *target_path* in *heads*.

    Strictly an ancestor, so a page never heads itself and the walk cannot
    cycle. Nearest rather than topmost because heads nest: a module under
    ``core/analysis/health`` belongs to Health, which belongs to Analysis,
    which belongs to Core.
    """
    cursor = target_path
    while "/" in cursor:
        cursor = cursor.rsplit("/", 1)[0]
        if cursor in heads:
            return cursor
    return None


# Where a top-level chapter's import balance is kept between runs. Measured
# where the import graph is already in memory (a full generation) and reused by
# every later rebuild, which then needs no edge read: the top-level chapters
# only change when module pages do, and module pages only change in generation.
_BALANCE_KEY = "chapter_import_balance"


def _chapter_balances(
    top: dict[str, tuple[list[str], str]],
    pages: dict[str, TreeNode | GeneratedPage],
    import_edges: Iterable[tuple[str, str]] | None,
) -> dict[str, int]:
    """Each runtime sibling's import balance among *top*, measured or stored.

    With *import_edges*, measured and stamped on the page. Without, read back
    from the stamp; a page that never had one counts as balanced (zero).
    """
    runtime = {pid: files for pid, (files, layer) in top.items() if not is_support_layer(layer)}
    if len(runtime) < 2:
        # One sibling has no other to import or be imported by.
        return dict.fromkeys(runtime, 0)
    if import_edges is None:
        return {pid: int(pages[pid].metadata.get(_BALANCE_KEY) or 0) for pid in runtime}
    balance = import_balance(
        {f: pid for pid, files in runtime.items() for f in files}, import_edges
    )
    for pid in runtime:
        pages[pid].metadata[_BALANCE_KEY] = balance[pid]
    return {pid: balance[pid] for pid in runtime}


def _reader_ranks(
    top: dict[str, tuple[list[str], str]],
    balance: dict[str, int],
    entry_files: Container[str] = (),
) -> dict[str, tuple[int, ...]]:
    """Sort keys for sibling module pages, in the order a reader wants them.

    *top* maps each sibling to its files and its layer; *balance* holds the
    runtime ones' import balance (see :func:`_chapter_balances`). The path
    order this replaces read a repository's engine fifteenth, after its editor
    extension. A reader wants the opposite of a dependency diagram:

    1. support material (tests, docs, tooling) last;
    2. a sibling holding an entry point (a walkthrough starts in it) first,
       when *entry_files* are given: that is where a reader enters the section;
    3. the layer's reading bucket: engine, then storage, then the surfaces
       people use it through, then shared types and utilities;
    4. what the others are built on before what is built on it (the import
       balance between the siblings' files, the measure the layer spine is
       ordered by), so inputs read before the stages that consume them;
    5. the larger sibling, the one a newcomer meets most.
    """
    return {
        pid: (
            int(pid not in balance),
            int(not any(f in entry_files for f in files)),
            reading_bucket(layer),
            -balance.get(pid, 0),
            -len(files),
        )
        for pid, (files, layer) in top.items()
    }


def _sibling_ranks(
    siblings: dict[str | None, dict[str, tuple[list[str], str]]],
    by_id: dict[str, TreeNode | GeneratedPage],
    import_edges: Iterable[tuple[str, str]] | None,
    flows: list[GeneratedPage] | list[TreeNode],
    root_id: str | None,
) -> dict[str, tuple[int, ...]]:
    """Reading-order keys for every module page, among its siblings."""
    # Measured once per parent, so external edges are dropped once here.
    edges = (
        [(s, d) for s, d in import_edges if not (is_external(s) or is_external(d))]
        if import_edges is not None
        else None
    )
    # A walkthrough's entry file marks where a reader enters a section. Not at
    # the top rung, where it would put the CLI ahead of the engine it drives.
    entry_files = {p.target_path.split("::", 1)[0] for p in flows}
    reader_rank: dict[str, tuple[int, ...]] = {}
    for parent_id, group in siblings.items():
        reader_rank.update(
            _reader_ranks(
                group,
                _chapter_balances(group, by_id, edges),
                entry_files if parent_id != root_id else (),
            )
        )
    for pid in _supporting_sections(siblings.get(root_id, {}), siblings):
        # After every section a reader is meant to scan; see _number.
        reader_rank[pid] = (_SUPPORTING_RANK, *reader_rank[pid])
    return reader_rank


def _lift_through_dominant_chapters(
    module_pages: list[GeneratedPage] | list[TreeNode],
    placed: dict[str, tuple[list[str], str]],
    root_id: str | None,
) -> None:
    """Give a section the parts of a chapter that is most of it.

    A package's chapter (``packages/core``) usually heads one chapter for its
    source tree (``packages/core/src/pkg/core``) that holds nearly everything,
    so every real part of the section sat one rung further down than the
    section's own parts: "Core > Pkg Core > Ingestion". When a chapter holds
    at least four fifths of its parent's files, its module children are
    re-parented onto that parent. The chapter keeps its row, beside them.
    """
    children: dict[str | None, list[GeneratedPage | TreeNode]] = {}
    for page in module_pages:
        children.setdefault(page.parent_page_id, []).append(page)
    by_id = {page.page_id: page for page in module_pages}
    # Shallow first, so a chain of such chapters folds all the way up.
    for page in sorted(module_pages, key=lambda p: (p.target_path.count("/"), p.page_id)):
        parent = by_id.get(page.parent_page_id or "")
        kids = children.get(page.page_id, [])
        if parent is None or parent.page_id == root_id or not kids:
            continue
        if len(placed[page.page_id][0]) * 5 < len(placed[parent.page_id][0]) * 4:
            continue
        for kid in kids:
            kid.parent_page_id = parent.page_id
        children.setdefault(parent.page_id, []).extend(kids)
        children[page.page_id] = []


# More top-level sections than this and the small leaf ones are listed as one
# "Supporting packages" section, so the outline stays a table of contents.
_MAX_SECTIONS = 12
_SUPPORTING_RANK = 3


def _supporting_sections(
    top: dict[str, tuple[list[str], str]],
    siblings: dict[str | None, dict[str, tuple[list[str], str]]],
) -> set[str]:
    """Top-level module pages read as one "Supporting packages" section.

    A repository of many small top-level packages (Go's convention) put forty
    rows on the top rung, most of them a page about one directory. Sections
    with parts of their own always stay; of the rest, the largest stay until
    there are :data:`_MAX_SECTIONS`, and the others are numbered together after
    the sections (``11.1``, ``11.2``), which a reader-facing tree draws as one
    folder.
    """
    if len(top) <= _MAX_SECTIONS:
        return set()
    leaves = [pid for pid in top if not siblings.get(pid)]
    room = max(0, _MAX_SECTIONS - (len(top) - len(leaves)))
    # Runtime packages keep a section before tests, docs and tooling do.
    kept = sorted(
        leaves, key=lambda pid: (is_support_layer(top[pid][1]), -len(top[pid][0]), pid)
    )[:room]
    return set(leaves) - set(kept)


def _subtree_files(target_path: str, members: list[str], file_targets: list[str]) -> list[str]:
    """A head's own members plus every documented file under its directory.

    *file_targets* must be sorted: the files under a directory are then one
    contiguous slice, found by bisection rather than by a scan per head.
    """
    prefix = target_path + "/"
    lo = bisect_left(file_targets, prefix)
    hi = bisect_left(file_targets, prefix + "\U0010ffff")
    return sorted(set(members).union(file_targets[lo:hi]))


def assign_page_tree(
    pages: list[GeneratedPage] | list[TreeNode],
    import_edges: Iterable[tuple[str, str]] | None = None,
) -> None:
    """Fill in ``parent_page_id``, ``display_order`` and ``section_number``.

    Mutates the pages in place. Every parent it assigns is a page in the set:
    each rule resolves against a different source, so the result is checked
    rather than trusted. On a partial set that means a page whose real parent
    was not regenerated gets the nearest ancestor that is present, or none,
    which is why callers working from a subset rebuild from the store instead.

    *import_edges* are ``(src, dst)`` file import edges. They order the
    top-level chapters for a reader (see :func:`_reader_ranks`) and nothing
    else. Pass them where the graph is in memory; without them the balance
    stamped by the last run that had them is reused.
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

    file_targets = sorted(p.target_path for p in by_type.get("file_page", []))

    # A file's layer comes from the provenance stamped on it at generation
    # time. Keyed on layer_id, never the display name, which drifts.
    layer_of_file: dict[str, str] = {}
    # Display name per layer id, borrowed from the files that carry both. The
    # id is a slug and turning it back into a name is lossy, so the curated
    # name is read rather than derived.
    layer_name_by_id: dict[str, str] = {}
    for page in by_type.get("file_page", []):
        lid = page.metadata.get("layer_id")
        if isinstance(lid, str) and lid:
            layer_of_file[page.target_path] = lid
            name = page.metadata.get("layer_name")
            if isinstance(name, str) and name:
                layer_name_by_id.setdefault(lid, name)

    def stamp_layer(page: TreeNode, layer_id: str) -> None:
        """Record which layer a page belongs to, on the page itself.

        A reader-facing tree groups by this rather than by parenting the page
        onto a layer page, so the grouping survives those pages not existing.
        Nothing is stamped when the layer could not be resolved: absent is
        honest, whereas a placeholder id would collect unrelated pages under
        one heading.
        """
        if not layer_id:
            return
        page.metadata["layer_id"] = layer_id
        name = layer_name_by_id.get(layer_id)
        if name:
            page.metadata["layer_name"] = name

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
        module_paths.append(page.target_path)
        for member in _listed_members(page):
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
        return root_id

    for page in by_type.get("file_page", []):
        page.parent_page_id = file_parent(page.target_path)
    # A contract or infra page is found by where its file lives, not by which
    # concept page happened to claim that file: a concept page can gather
    # files from sibling directories, and the reader browsing a directory's
    # chapter is the one looking for its contracts and build files.
    for page_type in ("api_contract", "infra_page"):
        for page in by_type.get(page_type, []):
            mod = _nearest_module(page.target_path, module_paths)
            page.parent_page_id = f"module_page:{mod}" if mod else file_parent(page.target_path)

    # Now that every file has found a module, a module that recorded no
    # members can borrow theirs. Pages written before members were recorded
    # would otherwise report an empty membership and land on the root, which
    # would keep every wiki indexed before this change permanently flat.
    claimed: dict[str, list[str]] = {}
    for page in by_type.get("file_page", []):
        if page.parent_page_id and page.parent_page_id.startswith("module_page:"):
            claimed.setdefault(page.parent_page_id, []).append(page.target_path)

    owned = {
        page.page_id: _listed_members(page) or claimed.get(page.page_id, [])
        for page in module_pages
    }
    head_at = _module_heads(module_pages, owned)
    # Each module page's files and layer, then the same grouped by parent.
    placed: dict[str, tuple[list[str], str]] = {}
    siblings: dict[str | None, dict[str, tuple[list[str], str]]] = {}

    for page in module_pages:
        members = owned[page.page_id] or _borrowed_members(page, module_pages, owned)
        if page.target_path in head_at:
            # A page heading a subtree belongs where the subtree does, not
            # where its own loose files do: a package's config files at its
            # root said nothing about whether the package is a UI.
            members = _subtree_files(page.target_path, members, file_targets)
        module_layer = _dominant_layer(members, layer_of_file)
        # Stamped either way: the reader-facing tree groups by this, and a page
        # that has found a chapter still belongs to a layer.
        stamp_layer(page, module_layer)
        head = nearest_head(page.target_path, head_at)
        page.parent_page_id = head_at[head] if head else root_id
        placed[page.page_id] = (members, module_layer)
    by_id = {page.page_id: page for page in module_pages}
    _lift_through_dominant_chapters(module_pages, placed, root_id)
    for page in module_pages:
        siblings.setdefault(page.parent_page_id, {})[page.page_id] = placed[page.page_id]
    reader_rank = _sibling_ranks(
        siblings, by_id, import_edges, by_type.get("flow_page", []), root_id
    )

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
        members = _listed_members(page)
        scc_layer = _dominant_layer(members, layer_of_file)
        stamp_layer(page, scc_layer)
        page.parent_page_id = root_id
    for page_type in _START_HERE_TYPES:
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

    _number(pages, root_id, reader_rank)


def _grouped_at_top(page: TreeNode | GeneratedPage, reader_rank: dict[str, tuple[int, ...]]) -> bool:
    """Whether a top-level page is numbered inside a group rather than as a section."""
    if page.page_type == "flow_page":
        return True
    rank = reader_rank.get(page.page_id, ())
    return page.page_type == "module_page" and bool(rank) and rank[0] == _SUPPORTING_RANK


def _restates(page: TreeNode | GeneratedPage, parent: TreeNode | GeneratedPage | None, product: str) -> bool:
    """Whether *page* is named as its parent section is, once read in place.

    "Repowise Core" under Core, "Web Source" under Web: a reader-facing tree
    folds such a row into its section's own, so it takes no number of its own
    and the parts after it are numbered from 1.
    """
    if parent is None or page.page_type != "module_page" or parent.page_type != "module_page":
        return False
    section = contextual_label(nav_label(parent.title or ""), "", product)
    own = contextual_label(nav_label(page.title or ""), section, product)
    return bool(section) and own.lower() == section.lower()


def _takes_number(
    page: TreeNode | GeneratedPage,
    depth: int,
    prefix: str,
    reader_rank: dict[str, tuple[int, ...]],
) -> bool:
    """Whether *page* is numbered where it sits: an outline page, two levels
    deep at most, under a numbered parent, and not one numbered in a group."""
    if depth > _MAX_NUMBER_DEPTH or page.page_type not in _NUMBERED_TYPES:
        return False
    if is_project_activity(page):
        return False
    if depth == 1:
        return not _grouped_at_top(page, reader_rank)
    return bool(prefix)


def _number_top_groups(
    top: list[GeneratedPage] | list[TreeNode],
    counter: int,
    reader_rank: dict[str, tuple[int, ...]],
) -> None:
    """Number the supporting packages, then the walkthroughs: each a group
    after the sections, its pages numbered inside it ("11.1", "12.1")."""
    for kind in ("module_page", "flow_page"):
        grouped = [p for p in top if p.page_type == kind and _grouped_at_top(p, reader_rank)]
        if grouped:
            counter += 1
        for index, page in enumerate(grouped, start=1):
            page.section_number = f"{counter}.{index}"


def _number(
    pages: list[GeneratedPage] | list[TreeNode],
    root_id: str | None,
    reader_rank: dict[str, tuple[int, ...]],
) -> None:
    """Assign sibling order and dotted section numbers by walking the tree.

    Every placed page gets a ``display_order``. Only the outline is numbered,
    and only two levels deep: the reading path and the sections ("1", "4"),
    the parts of a section ("4.2"), and the walkthroughs as one more section
    after the last ("12.1", "12.2"), which is how a reader-facing tree groups
    them. Reference pages (files, symbols, contracts, infra, cycles), project
    activity and anything deeper carry no number.
    """
    children: dict[str | None, list[GeneratedPage]] = {}
    for page in pages:
        children.setdefault(page.parent_page_id, []).append(page)
    for siblings in children.values():
        siblings.sort(key=lambda p: _sort_key(p, reader_rank))

    numbered: set[str] = set()
    by_id = {p.page_id: p for p in pages}
    product = by_id[root_id].target_path if root_id in by_id else ""

    def walk(parent_id: str | None, prefix: str, depth: int, counter: int = 0) -> int:
        for index, page in enumerate(children.get(parent_id, []), start=1):
            page.display_order = index
            page.section_number = None
            numbered.add(page.page_id)
            if depth == 2 and _restates(page, by_id.get(parent_id or ""), product):
                # Folded into its section's row, so what it holds is numbered
                # as the section's own parts, in its place.
                counter = walk(page.page_id, prefix, depth, counter)
                continue
            if _takes_number(page, depth, prefix, reader_rank):
                counter += 1
                page.section_number = f"{prefix}{counter}"
            walk(
                page.page_id,
                f"{page.section_number}." if page.section_number else "",
                depth + 1,
            )
        if depth == 1:
            _number_top_groups(children.get(parent_id, []), counter, reader_rank)
        return counter

    if root_id is not None:
        root = next(p for p in pages if p.page_id == root_id)
        root.display_order = 0
        root.section_number = None
        numbered.add(root_id)
        walk(root_id, "", 1)

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
