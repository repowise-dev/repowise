"""End-to-end page selection.

The single ``select_pages`` entry point returns an allow-set that both
``PageGenerator.generate_all`` and ``cost_estimator.build_generation_plan``
honor verbatim. No bypass paths — if a candidate isn't here, it isn't
emitted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import structlog

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

from ..concept_tree.grouping import ConceptGroup, group_files
from ..concept_tree.naming import (
    _humanise,
    chapter_title,
    chapter_titles,
    deterministic_scope,
    deterministic_title,
    disambiguate_titles,
)
from ..models import member_structural_key, scc_page_slug
from .scoring import (
    score_api_contract,
    score_file,
    score_infra,
    score_scc,
    score_symbol,
)

log = structlog.get_logger(__name__)

_INFRA_LANGUAGES = _LANG_REGISTRY.infra_languages()
_INFRA_FILENAMES = frozenset({"Dockerfile", "Makefile", "GNUmakefile"})
_CODE_LANGUAGES = _LANG_REGISTRY.code_languages()

# Top-level directories whose contents document or illustrate the repository
# rather than being it. Matched as a whole first path segment only. See
# ``_is_support_file`` for why the anchoring is the point.
_SUPPORT_ROOT_DIRS = frozenset(
    {"docs", "doc", "documentation", "docs_src", "examples", "example", "samples", "sample"}
)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleGroup:
    """One ``module_page`` worth of files.

    ``key`` is the stable identifier persisted as ``target_path``, and it is
    always a real directory: the shallowest directory the group's members
    share. The bench gold set matches pages by exact equality against the
    surfaced ``target_path``, so a group that merged several sibling
    directories still has to name one of them.

    ``structural_key`` is where the abstract identity lives — a hash of the
    sorted member list, carrying the ``concept-`` prefix minted by the
    grouper. It is set by whatever produced the group rather than recomputed
    downstream, because two places agreeing about page identity is the
    arrangement D2 exists to prevent.
    """

    key: str
    display: str
    language: str
    file_paths: tuple[str, ...]
    label: str | None = None
    cohesion: float | None = None
    structural_key: str = ""
    #: The section this page belongs to, and its position in the reading order
    #: the namer chose. Display only: neither takes part in page identity, so a
    #: re-run that re-sections the wiki renumbers it and mints nothing.
    section: str = ""
    order: int = 0
    #: One sentence saying what the page covers and what it does not, from the
    #: outline planner. Threaded to the renderer so the opener can situate the
    #: page against its siblings. Empty on the deterministic path until named.
    scope: str = ""
    #: A rollup page heads the subsystem directory whose detail lives on the
    #: child concept pages below it. It still owns whatever ``file_paths``
    #: holds — a directory that has children *and* loose files of its own is
    #: one page that does both, because both would otherwise be
    #: ``module_page:{that directory}`` and only one page can have an id.
    is_rollup: bool = False
    #: Files the renderer may read to describe the page, beyond the ones it
    #: owns. A rollup sets this to its whole subsystem so the prose can be
    #: about the subsystem while ``file_paths`` stays the disjoint ownership
    #: claim. Empty means "the same files it owns", which is every leaf.
    #:
    #: The split exists because three consumers read ``file_paths`` as an
    #: ownership claim and assume the partition is disjoint — the tree's
    #: ``module_of_file``, the incremental cascade's ``module_page_of``, and
    #: ``related_pages``' sibling map. Widening ``file_paths`` for context
    #: silently broke all three: a rollup out-claimed its own leaves for every
    #: file in the subtree.
    context_paths: tuple[str, ...] = ()


@dataclass
class ConceptCandidates:
    """The concept partition, in both the shapes the run needs.

    ``scored`` is what the budget and the generator consume. ``groups`` is the
    partition itself, carried out so the namer can title the exact groups that
    were computed here instead of asking the grouper for a second partition
    and hoping the two agree.
    """

    scored: list[tuple[float, ModuleGroup]] = field(default_factory=list)
    groups: list[ConceptGroup] = field(default_factory=list)
    layer_labels: dict[str, str] = field(default_factory=dict)


@dataclass
class Selection:
    """Allow-set returned by :func:`select_pages`."""

    file_page_paths: list[str] = field(default_factory=list)
    symbol_spotlights: list[tuple[str, str]] = field(
        default_factory=list
    )  # (file_path, symbol_name)
    module_groups: list[ModuleGroup] = field(default_factory=list)
    api_contract_paths: list[str] = field(default_factory=list)
    infra_paths: list[str] = field(default_factory=list)
    scc_groups: list[tuple[str, list[str]]] = field(default_factory=list)  # (scc_id, files)
    emit_repo_overview: bool = True
    # The concept partition behind ``module_groups``, one entry per group, plus
    # the layer names the grouper steered by. Carried so a caller holding a
    # provider can name these groups; selection itself stays free of the model
    # so the cost estimator and scope resolution keep costing nothing.
    concept_groups: list[ConceptGroup] = field(default_factory=list)
    layer_labels: dict[str, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        """Per-page-type counts of the pages this run will emit.

        Every type is counted, including the ones that cost nothing to render.
        The cost estimator prices each type separately, so a free type
        contributing zero to the bill is its price talking, not its absence
        here, and a caller wanting a page total gets a true one.
        """
        return {
            "api_contract": len(self.api_contract_paths),
            "symbol_spotlight": len(self.symbol_spotlights),
            "file_page": len(self.file_page_paths),
            "scc_page": len(self.scc_groups),
            "module_page": len(self.module_groups),
            "repo_overview": int(self.emit_repo_overview),
            "infra_page": len(self.infra_paths),
        }


# ---------------------------------------------------------------------------
# Input bundle
# ---------------------------------------------------------------------------


@dataclass
class SelectionInputs:
    """All inputs the selector needs.

    Bundling them in one dataclass keeps the public signature small —
    both ``PageGenerator`` and the cost estimator construct one of
    these and hand it to :func:`select_pages`.
    """

    parsed_files: list[Any]
    pagerank: dict[str, float]
    betweenness: dict[str, float]
    community: dict[str, int]
    community_info: dict[int, Any] | None  # cid → CommunityInfo (label, cohesion)
    sccs: list[Any]
    git_meta_map: dict[str, dict] | None
    config: Any  # GenerationConfig — duck-typed to avoid the import cycle
    kg_file_scores: dict[str, float] | None = None
    # Curated wiki modules from the KG artifact (``modules`` top-level key),
    # passed through — never re-derived here. Read for one thing only: the
    # file -> layer map that steers which adjacent directories merge into a
    # concept group. Absent or partial degrades the grouping's taste, never
    # its coverage, because the partition itself needs no KG input.
    kg_modules: list[dict] | None = None


# ---------------------------------------------------------------------------
# Helpers — file classification
# ---------------------------------------------------------------------------


def _is_infra_file(parsed: Any) -> bool:
    fi = parsed.file_info
    if fi.language in _INFRA_LANGUAGES:
        return True
    return Path(fi.path).name in _INFRA_FILENAMES


def _is_code_file(parsed: Any) -> bool:
    fi = parsed.file_info
    return not fi.is_api_contract and not _is_infra_file(parsed) and fi.language in _CODE_LANGUAGES


# ---------------------------------------------------------------------------
# File-page volume policy
#
# Two numbers, each answering a different question, both derived rather than
# picked. The basis is the size distribution of the 2,309 repositories indexed on
# repowise.dev (latest ready snapshot each): median 64 files, p75 235, p90 907,
# p95 2,047, p98 3,290, p99 4,719, largest 14,943.
# ---------------------------------------------------------------------------

# Above this many documentable files, an interactive advanced-mode run offers a
# leaner wiki. It is p95, so 19 repos in 20 are never asked. Nothing is capped
# automatically at this size: a 2,500-file repo would lose 500 pages and gain
# nothing anyone measured, which is not a trade to make on someone's behalf.
FILE_PAGE_ASK_THRESHOLD = 2000

# The ceiling every run holds the file bucket to, asked or not. A file page
# averages 8.8 KB with its metadata (measured over 1,961 pages in a real index),
# so this is about 40 MB of file layer: inside the hosted 48 MiB artifact ceiling
# before compression, with room to spare after it. It sits near p99, so it fires
# on roughly one repo in a hundred, and those are the repos where the tail is the
# difference between a wiki that publishes and one that does not.
#
# A ceiling rather than a fraction, because bounding bytes is the whole point: a
# percentage of the repo grows with the repo and bounds nothing. It is also the
# least aggressive number that solves that problem, which is what makes it
# defensible to apply without asking. Anything tighter is taste, and taste stays
# a question.
FILE_PAGE_AUTO_CEILING = 4500


def auto_file_page_cap(documentable_files: int) -> int | None:
    """The cap a run applies on its own, or ``None`` to leave the bucket alone.

    This is what an unset ``max_file_pages`` resolves to. It only ever engages
    above :data:`FILE_PAGE_AUTO_CEILING`, and it caps to exactly that, so a repo
    just over the line loses almost nothing and a 15,000-file monorepo keeps the
    4,500 file pages its readers are most likely to want.
    """
    return FILE_PAGE_AUTO_CEILING if documentable_files > FILE_PAGE_AUTO_CEILING else None


def recommended_file_page_cap(documentable_files: int) -> int | None:
    """The cap to offer as the recommended answer, or ``None`` if none applies.

    Above the automatic ceiling this is that ceiling, so the question can never
    recommend something the policy would then override. Between the ask
    threshold and the ceiling it is the threshold itself, which keeps the choice
    continuous: a repo just over the line loses almost nothing by taking it.
    """
    if documentable_files > FILE_PAGE_AUTO_CEILING:
        return FILE_PAGE_AUTO_CEILING
    if documentable_files > FILE_PAGE_ASK_THRESHOLD:
        return FILE_PAGE_ASK_THRESHOLD
    return None


def count_documentable_files(parsed_files: list[Any]) -> int:
    """How many files would get a ``file_page``, from parsed output.

    The exact predicate file-page selection uses, minus the score floor (which
    needs graph metrics), so it reads slightly high on files the graph knows
    nothing about. Exists so a caller can report what the volume policy is about
    to do before generation starts, in the same terms the policy uses.
    """
    return sum(
        1 for p in parsed_files if _is_code_file(p) and _passes_importance_floor(p.file_info.path)
    )


def _passes_importance_floor(path: str) -> bool:
    """Whether *path* is worth a file page at all.

    Two exclusions, both measured rather than assumed: test files and pure
    ``__init__.py`` re-export files. Pages for either only dilute retrieval
    (test-file pages pushed real answers below rank 5 in dogfood), and neither
    says anything a reader cannot get from the file it re-exports or tests.

    This used to gate only the coverage tail, back when the budget picked a
    fraction of the repo and the tail backfilled the rest. There is no tail any
    more because every file that clears this floor gets a page, so the floor is
    now simply what file-page selection means. The rule is unchanged; only the
    set it applies to grew from the remainder to the whole.
    """
    norm = path.replace("\\", "/")
    if norm.startswith("tests/") or "/tests/" in norm:
        return False
    return norm.rsplit("/", 1)[-1] != "__init__.py"


# ---------------------------------------------------------------------------
# Helpers — bucket candidate building
# ---------------------------------------------------------------------------


def _build_file_candidates(
    inputs: SelectionInputs,
) -> list[tuple[float, str]]:
    """Return ``[(score, file_path), ...]`` for code files, descending."""
    max_pr = max(inputs.pagerank.values(), default=0.0)
    max_bet = max(inputs.betweenness.values(), default=0.0)
    git = inputs.git_meta_map or {}
    kg_scores = inputs.kg_file_scores or {}

    scored: list[tuple[float, str]] = []
    for p in inputs.parsed_files:
        if not _is_code_file(p):
            continue
        path = p.file_info.path
        if not _passes_importance_floor(path):
            continue
        is_hotspot = bool(git.get(path, {}).get("is_hotspot", False))
        s = score_file(
            p,
            pagerank=inputs.pagerank.get(path, 0.0),
            betweenness=inputs.betweenness.get(path, 0.0),
            max_pagerank=max_pr,
            max_betweenness=max_bet,
            is_hotspot=is_hotspot,
            kg_bonus=kg_scores.get(path, 0.0),
        )
        if s > 0.0:
            scored.append((s, path))
    scored.sort(key=lambda x: (-x[0], x[1]))
    # Three states, because "how many file pages" has three real answers.
    #
    #   unset (None) -> the volume policy decides: untouched below the automatic
    #                   ceiling, held at it above (see auto_file_page_cap)
    #   0            -> explicitly unlimited, one page per eligible file, however
    #                   many that is. This is a refusal, and it is honoured
    #   N > 0        -> cap at N
    #
    # Whatever the cap, it slices the ranking already computed above rather than a
    # second one, and the sort is total (score, then path), so the cut is
    # deterministic.
    cap = getattr(inputs.config, "max_file_pages", None)
    if cap is None:
        cap = auto_file_page_cap(len(scored))
    if not cap:  # None (nothing to do) or 0 (explicitly unlimited)
        return scored
    return scored[: max(1, cap)]


def _build_symbol_candidates(
    inputs: SelectionInputs,
) -> list[tuple[float, tuple[str, str]]]:
    """Return ``[(score, (file_path, symbol_name)), ...]`` descending."""
    max_pr = max(inputs.pagerank.values(), default=0.0)
    scored: list[tuple[float, tuple[str, str]]] = []
    for p in inputs.parsed_files:
        file_pr = inputs.pagerank.get(p.file_info.path, 0.0)
        for sym in p.symbols:
            if sym.visibility != "public":
                continue
            s = score_symbol(sym, file_pr, max_pr)
            if s > 0.0:
                scored.append((s, (p.file_info.path, sym.name)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    # A spotlight page is keyed by ``(file_path, symbol_name)``, but one file
    # can declare that pair twice: two classes in the same Java file each
    # with a ``toString``, two Rust impl blocks each with a ``new``. Left in,
    # the duplicates generate the same page id twice (the second silently
    # overwriting the first, having spent a full LLM call to do it). Keep the
    # highest-scoring occurrence; the list is already sorted, so first wins.
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[float, tuple[str, str]]] = []
    for score, target in scored:
        if target in seen:
            continue
        seen.add(target)
        deduped.append((score, target))
    # A spotlight repeats what its file's page already renders: the signature,
    # the docstring, the importer list. Taking every public symbol would bury
    # the pages that say something new, so the bucket is held to the strongest
    # slice by PageRank. This used to fall out of the coverage budget's 0.15
    # share; with nothing costing tokens there is no budget to fall out of, so
    # the percentile that always existed on the config now does the bounding.
    # Fallback tracks GenerationConfig.top_symbol_percentile; a config object
    # predating the field must not silently select a wider slice than the
    # declared default.
    pct = getattr(inputs.config, "top_symbol_percentile", 0.10) or 0.0
    if pct <= 0:
        return []
    if pct >= 1.0:
        return deduped
    # At least one, so a repo with few public symbols still gets a spotlight.
    keep = max(1, int(len(deduped) * pct))
    return deduped[:keep]


def _layer_map_from_kg(
    inputs: SelectionInputs,
) -> tuple[dict[str, str], dict[str, str]]:
    """File -> curated layer id, and layer id -> display name.

    The layer signal only steers which adjacent runs of directories merge; it
    never forces a split, so an absent or partial map degrades the grouping's
    taste rather than its correctness. That is why this reads whatever the KG
    artifact happens to carry instead of requiring it.
    """
    layer_of_file: dict[str, str] = {}
    for module in inputs.kg_modules or []:
        layer_id = module.get("layerId")
        if not isinstance(layer_id, str) or not layer_id:
            continue
        for nid in module.get("nodeIds", []):
            if isinstance(nid, str) and nid.startswith("file:"):
                layer_of_file[nid[5:]] = layer_id
    labels = {
        lid: lid.removeprefix("layer:").replace("-", " ").replace("_", " ").title()
        for lid in set(layer_of_file.values())
    }
    return layer_of_file, labels


def _is_support_file(path: str) -> bool:
    """Whether *path* is documentation or example source rather than the subject.

    Anchored at the repository root and matched on whole path segments, which
    is the difference between this rule and the substring match the test rule
    originally shipped with. A package that has a ``docs`` directory as a
    *feature* keeps its pages; a ``docs/`` tree at the top of the repository
    does not, because it is the subject's documentation and not the subject.

    Measured rather than assumed, the same way the test exclusion was: on one
    web framework 10 of 15 concept pages documented the example snippets that
    illustrate the library while the library itself got 4. These files match a
    concept query on vocabulary without answering how the system works, and a
    wiki that documents its subject's documentation has said nothing about the
    subject. They keep their deterministic file pages, so nothing becomes
    unreachable.
    """
    head = path.split("/", 1)[0].lower()
    return head in _SUPPORT_ROOT_DIRS


def _build_module_groups(inputs: SelectionInputs) -> ConceptCandidates:
    """Return the concept groups, scored, one per ``module_page``.

    This is the concept tree, not one page per directory. The partition comes
    from :func:`group_files`, which bins the production files into bounded,
    path-local subtrees with no model in the loop, so it is total (every
    non-test production file lands in exactly one group) and deterministic
    (the same file set always produces the same groups, and therefore the same
    page identities).

    Totality is the property that makes this a replacement rather than an
    alternative. The per-directory grouping this supersedes covered a subset
    of the tree under a worse grouping and gave eleven of its pages to test
    directories, which D8 measured as pure dilution. There is no coexistence
    mode: D1 forbids one page type existing in two forms, and a wiki holding
    both populations would be exactly that.

    Scored by summed PageRank so the most central subsystem sorts first. The
    score no longer rations anything — see :func:`select_pages` — but page
    order is still what the reader sees first.
    """
    production = [
        p.file_info.path
        for p in inputs.parsed_files
        if _is_code_file(p) and not getattr(p.file_info, "is_test", False)
    ]
    files = [p for p in production if not _is_support_file(p)]
    if not files:
        # A repository whose production code is entirely under one of those
        # roots. Documenting the docs is a bad wiki; having no wiki at all is
        # a worse one, so the floor yields rather than emptying the tree.
        if production:
            log.info("page_selection.support_floor_skipped", files=len(production))
        files = production
    elif len(files) != len(production):
        log.info(
            "page_selection.support_floor",
            excluded=len(production) - len(files),
            kept=len(files),
        )
    if not files:
        return ConceptCandidates()

    layer_of_file, layer_labels = _layer_map_from_kg(inputs)
    groups = group_files(files, layer_of_file=layer_of_file)

    lang_of = {p.file_info.path: p.file_info.language for p in inputs.parsed_files}
    # Names are decided over the whole set, not per group: two packages that
    # each hold a ``ui`` directory derive the same name from their paths, and
    # two identical rows in the tree are indistinguishable to a reader even
    # though the pages behind them are not.
    titles = disambiguate_titles(
        [
            (deterministic_title(g, layer_labels.get(g.dominant_layer, "")), g.target_path)
            for g in groups
        ]
    )
    # Which directories head a subsystem. Computed before the groups are built
    # so a directory that is both a chapter and a leaf becomes one page rather
    # than colliding with itself on ``module_page:{dir}``.
    chapters = _chapter_members(groups, files)

    scored: list[tuple[float, ModuleGroup]] = []
    for group, title in zip(groups, titles, strict=True):
        langs = Counter(lang_of.get(m, "") for m in group.members)
        langs.pop("", None)
        score = sum(inputs.pagerank.get(m, 0.0) for m in group.members)
        subsystem = chapters.get(group.target_path)
        scored.append(
            (
                score,
                ModuleGroup(
                    # Taken as given. The grouper guarantees it is non-empty
                    # and distinct across groups, and naming the root here
                    # instead would sit outside the uniqueness check that
                    # makes the guarantee.
                    key=group.target_path,
                    display=title,
                    language=langs.most_common(1)[0][0] if langs else "unknown",
                    file_paths=tuple(group.members),
                    label=None,
                    cohesion=None,
                    structural_key=group.structural_key,
                    # Filled here so the deterministic path carries a scope
                    # sentence; the naming call overwrites it with a written one
                    # when a provider is present.
                    scope=(
                        _chapter_scope(group.target_path, owns=True)
                        if subsystem
                        else deterministic_scope(group)
                    ),
                    is_rollup=subsystem is not None,
                    context_paths=tuple(subsystem or ()),
                ),
            )
        )
    scored.extend(_build_rollup_groups(chapters, groups, files, lang_of, inputs.pagerank))
    # Last, because a chapter is named from the leaves beneath it and they all
    # have to exist first.
    retitled = retitle_chapters([mg for _, mg in scored])
    scored = [(score, mg) for (score, _), mg in zip(scored, retitled, strict=True)]
    scored.sort(key=lambda x: (-x[0], x[1].key))
    return ConceptCandidates(scored=scored, groups=groups, layer_labels=layer_labels)


# A rollup covering more than this fraction of the whole repository is not a
# subsystem overview, it is the repository overview under another name — that
# page already exists, so the rollup is skipped rather than made to compete with
# it. Applies to the near-root case in a monorepo where everything lives under a
# single top-level directory ("packages/", "src/").
_ROLLUP_MAX_MEMBER_FRACTION = 0.5


def _chapter_scope(parent: str, *, owns: bool) -> str:
    """The scope sentence for a chapter, on the deterministic path."""
    segment = parent.rsplit("/", 1)[-1] if "/" in parent else parent
    lead = (
        f"Heads the {_humanise(segment)} subsystem and links to the concept pages "
        "documenting its parts"
    )
    if owns:
        return (
            f"{lead}; the files directly under this directory are documented here and "
            "the rest of the detail lives on those child pages."
        )
    return f"{lead}; the detail lives on those child pages, not here."


def _chapter_members(groups: list[ConceptGroup], files: list[str]) -> dict[str, list[str]]:
    """Directories that head a subsystem → every production file beneath them.

    The partition splits a large subsystem into path-local leaves, so a
    directory such as ``.../ingestion`` gets no page of its own — only its
    children do. A reader asking "what is the ingestion subsystem" then lands on
    a file, not the subsystem, and a directory-level retrieval query has nothing
    to match against, because directory-level retrieval matches a page to a
    directory by exact ``target_path`` equality. A chapter is a page whose
    ``target_path`` is exactly that parent directory: it names its child pages
    and links down to them from its body.

    The rule is purely structural and repo-agnostic: a parent is eligible when at
    least two leaf concept pages are its *immediate* children. With leaves at
    mixed depths several ancestor levels can each qualify, which is intended —
    chapters nest — but one guard keeps the set sane: a parent covering more than
    ``_ROLLUP_MAX_MEMBER_FRACTION`` of the repository is not a subsystem
    overview, it is the repository overview under another name, and that page
    already exists.

    Eligibility deliberately does **not** exclude a parent that is itself a leaf
    group. Both pages would be ``module_page:{parent}`` and only one page can
    have an id, so excluding it was the only way to avoid a collision — but it
    excluded exactly the directories a reader most wants a chapter for. On this
    repository it suppressed nine of thirteen, including one heading eighteen
    child pages. Such a parent becomes a single page that both owns its own
    loose files and heads its children; see ``ModuleGroup.context_paths`` for how
    ownership stays disjoint while the prose still covers the subsystem.

    All paths here are POSIX-normalised upstream (the grouper lowercases
    separators), so the ``/`` splits below are safe.
    """
    total = len(files)
    child_count: Counter[str] = Counter()
    for g in groups:
        tp = g.target_path
        if "/" in tp:
            child_count[tp.rsplit("/", 1)[0]] += 1

    out: dict[str, list[str]] = {}
    for parent, kids in sorted(child_count.items()):
        if kids < 2:
            continue
        members = sorted(f for f in files if f.startswith(parent + "/"))
        if not members or (total and len(members) > _ROLLUP_MAX_MEMBER_FRACTION * total):
            continue
        out[parent] = members
    return out


def retitle_chapters(groups: list[ModuleGroup]) -> list[ModuleGroup]:
    """Name every chapter after the subsystem it heads, not after its own files.

    Called twice on a keyed run: once here, over the deterministic titles, and
    once after the namer answers, because the leaf titles it is derived from
    have changed by then. The rule is the same both times and lives in one
    place, so the two cannot disagree. See
    :func:`~..concept_tree.naming.chapter_title` for why a chapter is named
    from its place rather than by the model.

    A leaf is a group that owns files; a chapter that owns none is a pure
    rollup. A directory that is both keeps one page and takes the chapter name,
    because the name a reader sees stands over the whole subtree whatever else
    that page also documents.

    The scope sentence comes back with the title, for the same reason. A model
    shown a chapter's directory writes about the loose files at its root, so a
    chapter that took its scope from that call would open by promising the
    reader a page about those files and then be a page about the subsystem.
    """
    chapters = [mg.key for mg in groups if mg.is_rollup]
    if not chapters:
        return groups
    titles = chapter_titles({mg.key: mg.display for mg in groups if mg.file_paths}, chapters)
    return [
        replace(
            mg,
            display=titles[mg.key],
            scope=_chapter_scope(mg.key, owns=bool(mg.file_paths)),
        )
        if mg.is_rollup and mg.key in titles
        else mg
        for mg in groups
    ]


def _build_rollup_groups(
    chapters: dict[str, list[str]],
    groups: list[ConceptGroup],
    files: list[str],
    lang_of: dict[str, str],
    pagerank: dict[str, float],
) -> list[tuple[float, ModuleGroup]]:
    """Chapter pages for the directories that are *not* already a leaf group.

    A chapter whose directory is also a leaf is built by the caller, in the same
    loop as every other leaf, so it keeps that group's structural key and its
    section, both of which are keyed on the identity the namer already gave it.
    Only the directories with no group of their own need a page synthesised
    here, and those own no files.

    The title set here is provisional: :func:`retitle_chapters` runs over every
    chapter once the leaves exist and settles both kinds together, including
    making them unique.
    """
    pending = sorted(set(chapters) - {g.target_path for g in groups})
    if not pending:
        return []

    rollups: list[tuple[float, ModuleGroup]] = []
    for parent in pending:
        title = chapter_title(parent)
        members = chapters[parent]
        langs = Counter(lang_of.get(m, "") for m in members)
        langs.pop("", None)
        rollups.append(
            (
                sum(pagerank.get(m, 0.0) for m in members),
                ModuleGroup(
                    key=parent,
                    display=title,
                    language=langs.most_common(1)[0][0] if langs else "unknown",
                    # Owns nothing: every file beneath it belongs to one of the
                    # leaves below. The subsystem reaches the renderer through
                    # ``context_paths`` instead.
                    file_paths=(),
                    label=None,
                    cohesion=None,
                    # Hashed over the full subsystem with a distinct prefix so a
                    # chapter can never collide with a leaf whose members are a
                    # subset of these, and so the sweep tracks it as its own row.
                    structural_key=member_structural_key(members, prefix="concept-rollup"),
                    scope=_chapter_scope(parent, owns=False),
                    is_rollup=True,
                    context_paths=tuple(members),
                ),
            )
        )
    return rollups


def _build_api_candidates(inputs: SelectionInputs) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for p in inputs.parsed_files:
        if not p.file_info.is_api_contract:
            continue
        scored.append((score_api_contract(p), p.file_info.path))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def _build_infra_candidates(inputs: SelectionInputs) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for p in inputs.parsed_files:
        if not _is_infra_file(p):
            continue
        scored.append((score_infra(p), p.file_info.path))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def _build_scc_candidates(
    inputs: SelectionInputs,
) -> list[tuple[float, tuple[str, list[str]]]]:
    scored: list[tuple[float, tuple[str, list[str]]]] = []
    for scc in inputs.sccs:
        files = sorted(scc)
        s = score_scc(cycle_size=len(files))
        if s <= 0.0:
            continue
        scored.append((s, (scc_page_slug(files), files)))
    # Tie-break on the slug: score alone leaves equal-sized cycles in list
    # order, and the page-id set must not depend on how they got there.
    scored.sort(key=lambda x: (-x[0], x[1][0]))
    return scored


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_pages(inputs: SelectionInputs) -> Selection:
    """Return the allow-set of pages to generate for one run.

    One path, whatever the caller. Every page type below the concept tree is
    rendered from structure and costs no tokens, and the concept partition is a
    total cover of the production files that would stop being a map of the
    repository if it were rationed. So by default there is nothing left to
    ration: each bucket takes every candidate that clears its floor. The one
    exception is opt-in and owner-chosen -- ``config.max_file_pages`` bounds the
    file bucket for repos large enough that its tail costs more in bytes and
    retrieval noise than it pays back.

    That this does not depend on whether an API key is present is the point.
    Selection used to fork on it, which meant a keyed and a keyless index of the
    same commit disagreed about which files had pages at all. Now they cannot.

    Deterministic given identical inputs. Safe to call from both the generator
    and the cost estimator.
    """
    files = _build_file_candidates(inputs)
    symbols = _build_symbol_candidates(inputs)
    concepts = _build_module_groups(inputs)
    modules = concepts.scored
    apis = _build_api_candidates(inputs)
    infras = _build_infra_candidates(inputs)
    sccs = _build_scc_candidates(inputs)

    sel = Selection(
        file_page_paths=[p for _, p in files],
        symbol_spotlights=[t for _, t in symbols],
        module_groups=[m for _, m in modules],
        concept_groups=list(concepts.groups),
        layer_labels=dict(concepts.layer_labels),
        api_contract_paths=[p for _, p in apis],
        infra_paths=[p for _, p in infras],
        scc_groups=[g for _, g in sccs],
        emit_repo_overview=True,
    )
    log.info("page_selection.complete", counts=sel.counts())
    return sel


def summarize_selection(sel: Selection) -> dict[str, int]:
    """Convenience wrapper returning the counts dict.

    Kept as a separate helper so the init UI can hand a Selection
    directly to its rendering layer without depending on the dataclass
    internals.
    """
    return sel.counts()
