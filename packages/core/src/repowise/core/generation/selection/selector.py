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
from ..concept_tree.naming import deterministic_title, disambiguate_titles
from ..models import scc_page_slug
from ..tour import DEFAULT_MAX_LANDMARKS, tour_landmark_paths
from .budget import (
    BucketAllocation,
    ModuleDemandRow,
    allocate_budget,
    allocate_module_file_pages,
    compute_budget,
)
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
    emit_arch_diagram: bool = True
    allocation: BucketAllocation | None = None
    # Zero-LLM deterministic pages for the code files the budget did NOT pick
    # (Phase G coverage tail). Kept separate from ``file_page_paths`` so cost
    # estimation stays honest (these are free) and the CLI can report the split.
    deterministic_tail_paths: list[str] = field(default_factory=list)
    # The concept partition behind ``module_groups``, one entry per group, plus
    # the layer names the grouper steered by. Carried so a caller holding a
    # provider can name these groups; selection itself stays free of the model
    # so the cost estimator and scope resolution keep costing nothing.
    concept_groups: list[ConceptGroup] = field(default_factory=list)
    layer_labels: dict[str, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        """Per-page-type counts of BUDGETED (LLM-costed) pages.

        Deliberately excludes ``deterministic_tail_paths`` — those are free
        zero-LLM pages, reported separately (``len(deterministic_tail_paths)``)
        so cost estimation and the budget contract are not inflated by them.
        """
        return {
            "api_contract": len(self.api_contract_paths),
            "symbol_spotlight": len(self.symbol_spotlights),
            "file_page": len(self.file_page_paths),
            "scc_page": len(self.scc_groups),
            "module_page": len(self.module_groups),
            "repo_overview": int(self.emit_repo_overview),
            "architecture_diagram": int(self.emit_arch_diagram),
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
    # Per-file question demand mined from session transcripts
    # (``core.sessions.miners.demand.aggregate_file_demand``): repo-relative
    # path -> question count. Tilts the file_page budget toward high-demand
    # modules. ``None``/empty reproduces the uniform, demand-free selection
    # byte-for-byte (fresh installs with no session history).
    demand: dict[str, int] | None = None
    # Scoped generation: take every candidate in every bucket (no coverage
    # budget), the same full-coverage set deterministic mode uses but keeping
    # symbol spotlights so a caller can regenerate one on demand. The actual
    # rationing is done downstream by ``generate_all(only_page_ids=...)``, which
    # filters this full allow-set to exactly the requested pages. Without it,
    # ``repowise generate --unwritten`` would silently emit only the budgeted
    # ~20% of files instead of every template page the user asked to upgrade.
    select_all: bool = False


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


def _passes_tail_floor(path: str, tail_dirs: tuple[str, ...] | None) -> bool:
    """Importance floor for the deterministic coverage tail (Phase G).

    Two exclusions are ALWAYS applied because they were proven to only dilute
    retrieval (test-file pages pushed real answers below rank 5 in dogfood):
    test files and pure ``__init__.py`` re-export files. When ``tail_dirs`` is
    set, the path must also live under one of those repo-relative prefixes.
    """
    norm = path.replace("\\", "/")
    if norm.startswith("tests/") or "/tests/" in norm:
        return False
    if norm.rsplit("/", 1)[-1] == "__init__.py":
        return False
    if tail_dirs:
        return any(norm == d.rstrip("/") or norm.startswith(d.rstrip("/") + "/") for d in tail_dirs)
    return True


def _select_deterministic_tail(
    files: list[tuple[float, str]],
    selected_files: list[str],
    cfg: Any,
) -> list[str]:
    """Every code file the budget dropped, importance-floored and capped.

    ``files`` is score-descending, so a cap keeps the highest-signal tail.
    Returns [] when the tail is disabled, reproducing the prior behaviour.
    """
    if not getattr(cfg, "tier2_tail_enabled", True):
        return []
    selected = set(selected_files)
    tail_dirs = getattr(cfg, "tier2_tail_dirs", None)
    tail = [p for _, p in files if p not in selected and _passes_tail_floor(p, tail_dirs)]
    cap = getattr(cfg, "tier2_tail_cap", None)
    if cap is not None and cap >= 0:
        tail = tail[:cap]
    return tail


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
    return scored


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
    return deduped


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
    scored: list[tuple[float, ModuleGroup]] = []
    for group, title in zip(groups, titles, strict=True):
        langs = Counter(lang_of.get(m, "") for m in group.members)
        langs.pop("", None)
        score = sum(inputs.pagerank.get(m, 0.0) for m in group.members)
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
                ),
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1].key))
    return ConceptCandidates(scored=scored, groups=groups, layer_labels=layer_labels)


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


def _shares_from_config(cfg: Any) -> dict[str, float]:
    return {
        "file_page": getattr(cfg, "file_page_share", 0.50),
        "symbol_spotlight": getattr(cfg, "symbol_spotlight_share", 0.15),
        # Zero, and not a knob. The concept pages are taken whole, so a share
        # here would reserve budget for a bucket that does not spend it and
        # quietly shrink the file-page coverage the user actually asked for.
        "module_page": 0.0,
        "api_contract": getattr(cfg, "api_contract_share", 0.08),
        "infra_page": getattr(cfg, "infra_page_share", 0.05),
        "scc_page": getattr(cfg, "scc_share", 0.04),
    }


def _coverage_pct(cfg: Any) -> float:
    """Read ``coverage_pct``, falling back to legacy ``max_pages_pct``."""
    return float(getattr(cfg, "coverage_pct", None) or getattr(cfg, "max_pages_pct", 0.20))


def _build_file_module_map(
    module_groups: list[tuple[float, ModuleGroup]],
) -> dict[str, str]:
    """Map each grouped file to its module key (the wiki-page granularity).

    Built from every candidate module group, not just the selected top-K, so
    demand attribution covers all grouped files. Files in no group fall back to
    their top-level directory (:func:`_fallback_module`) at lookup time.
    """
    file_to_module: dict[str, str] = {}
    for _, group in module_groups:
        for path in group.file_paths:
            file_to_module.setdefault(path, group.key)
    return file_to_module


def _fallback_module(path: str) -> str:
    """Module key for a file in no group: its top-level directory.

    Mirrors the ``top_dir`` grouping fallback the pipeline itself uses, so an
    ungrouped file still attributes to a stable, human-legible bucket.
    """
    parts = Path(path).parts
    return parts[0] if len(parts) > 1 else "root"


def _select_file_pages(
    files: list[tuple[float, str]],
    file_page_budget: int,
    module_groups: list[tuple[float, ModuleGroup]],
    demand: dict[str, int] | None,
) -> list[str]:
    """The file_page allow-set, tilted toward high-demand modules.

    Falls straight through to the demand-free top-``file_page_budget`` when
    there is no demand, so behaviour is unchanged on fresh installs.
    """
    ranked = [p for _, p in files]
    if not demand:
        return ranked[:file_page_budget]

    file_to_module = _build_file_module_map(module_groups)

    def module_of(path: str) -> str:
        return file_to_module.get(path) or _fallback_module(path)

    selected, audit = allocate_module_file_pages(ranked, file_page_budget, demand, module_of)
    _log_demand_tilt(audit, file_page_budget)
    return selected


def _log_demand_tilt(audit: list[ModuleDemandRow], file_page_budget: int) -> None:
    """Emit the inspectable per-module reallocation table (dry-run audit)."""
    if not audit:
        return
    moved = [r for r in audit if r.delta]
    log.info(
        "page_selection.demand_tilt",
        file_page_budget=file_page_budget,
        modules_reweighted=len(audit),
        modules_moved=len(moved),
        gained=sum(r.delta for r in moved if r.delta > 0),
        table=[
            {
                "module": r.module,
                "demand": r.demand,
                "baseline": r.baseline_pages,
                "allocated": r.allocated_pages,
                "delta": r.delta,
                "candidates": r.candidates,
            }
            for r in audit[:25]
        ],
    )


def _ensure_landmarks(selected: list[str], landmarks: list[str]) -> list[str]:
    """Guarantee every *landmark* is in *selected*, keeping the count honest.

    For each landmark not already chosen, drop the lowest-scored *non-landmark*
    file from the tail (``selected`` is score-ordered descending) and append the
    landmark — so the total file_page count is unchanged. The only case the
    count can grow is when there is nothing left to displace (e.g. a near-zero
    budget where every selected file is itself a landmark); that overage is
    bounded by ``len(landmarks)`` (at most ``DEFAULT_MAX_LANDMARKS``).
    """
    sel = list(selected)
    landmark_set = set(landmarks)
    for m in landmarks:
        if m in sel:
            continue
        for i in range(len(sel) - 1, -1, -1):
            if sel[i] not in landmark_set:
                sel.pop(i)
                break
        sel.append(m)
    return sel


def _select_everything(
    files: list,
    symbols: list,
    concepts: ConceptCandidates,
    apis: list,
    infras: list,
    sccs: list,
    available: dict[str, int],
    *,
    include_symbols: bool,
) -> Selection:
    """Return a Selection holding every scored candidate, budget bypassed.

    Used by fully deterministic (no-LLM) generation and by scoped
    ``select_all`` generation, where the coverage budget has no job to do —
    the former because every page is free, the latter because
    ``only_page_ids`` rations downstream. Candidates are already score-ordered,
    so the resulting page order still puts the most central files first.

    ``include_symbols`` keeps the symbol-spotlight bucket (scoped generation, so
    a caller can regenerate a specific spotlight); deterministic mode drops it
    (see below).
    """
    sel = Selection(
        file_page_paths=[p for _, p in files],
        deterministic_tail_paths=[],
        # Symbol spotlights are the one bucket deterministic mode drops. A
        # template spotlight carries the signature, docstring and importer
        # list, all of which the file page for its containing file already
        # renders, so taking every candidate would triple the index for no
        # new information and dilute retrieval, the same failure the tier-2
        # tail's importance floor exists to avoid. On the fixture repo that is
        # 171 near-duplicate pages against 33 file pages. Scoped generation
        # keeps them (``include_symbols``) since ``only_page_ids`` filters to the
        # one the user asked for rather than emitting all of them.
        symbol_spotlights=[t for _, t in symbols] if include_symbols else [],
        module_groups=[m for _, m in concepts.scored],
        concept_groups=list(concepts.groups),
        layer_labels=dict(concepts.layer_labels),
        api_contract_paths=[p for _, p in apis],
        infra_paths=[p for _, p in infras],
        scc_groups=[g for _, g in sccs],
        emit_repo_overview=True,
        emit_arch_diagram=True,
        allocation=BucketAllocation(
            file_page=available["file_page"],
            symbol_spotlight=available["symbol_spotlight"] if include_symbols else 0,
            module_page=available["module_page"],
            api_contract=available["api_contract"],
            infra_page=available["infra_page"],
            scc_page=available["scc_page"],
        ),
    )
    log.info(
        "page_selection.complete",
        mode="select_all" if include_symbols else "deterministic",
        counts=sel.counts(),
    )
    return sel


def select_pages(inputs: SelectionInputs) -> Selection:
    """Return the allow-set of pages to generate for one run.

    Deterministic given identical inputs. Safe to call from both the
    generator and the cost estimator.
    """
    cfg = inputs.config
    pct = _coverage_pct(cfg)
    budget = compute_budget(len(inputs.parsed_files), pct)

    # Build scored candidates for every bucket.
    files = _build_file_candidates(inputs)
    symbols = _build_symbol_candidates(inputs)
    concepts = _build_module_groups(inputs)
    modules = concepts.scored
    apis = _build_api_candidates(inputs)
    infras = _build_infra_candidates(inputs)
    sccs = _build_scc_candidates(inputs)

    available = {
        "file_page": len(files),
        "symbol_spotlight": len(symbols),
        "module_page": len(modules),
        "api_contract": len(apis),
        "infra_page": len(infras),
        "scc_page": len(sccs),
    }

    # getattr, not attribute access: select_pages accepts any config-like
    # object (the cost estimator passes its own), so a new field must not
    # become a hard requirement on callers.
    if getattr(cfg, "deterministic", False):
        # Nothing to ration: a template page costs no tokens, so the coverage
        # budget has no job to do. Take every candidate in every bucket. The
        # tail stays empty because the file bucket already holds every file.
        return _select_everything(
            files, symbols, concepts, apis, infras, sccs, available, include_symbols=False
        )

    # Scoped generation (``repowise generate``): full-coverage allow-set so
    # ``only_page_ids`` can name any page. Spotlights kept so one can be
    # regenerated on demand.
    if getattr(inputs, "select_all", False):
        return _select_everything(
            files, symbols, concepts, apis, infras, sccs, available, include_symbols=True
        )

    allocation = allocate_budget(
        budget=budget,
        candidates_per_bucket=available,
        shares=_shares_from_config(cfg),
        n_files=len(inputs.parsed_files),
    )
    # The module bucket is not rationed (see the Selection below), so the
    # allocation has to say so. Leaving the budgeted number here would make
    # the cost estimator quote a figure the run does not honour, which is the
    # one thing the "allow-set honored verbatim" contract exists to prevent.
    allocation = replace(allocation, module_page=len(modules))

    # File pages, tilted toward the modules agents ask about most (demand-free
    # top-K when there is no session data, so fresh installs are unchanged).
    selected_files = _select_file_pages(files, allocation.file_page, modules, inputs.demand)

    # The guided tour wants its highest-value entry points to land on real
    # pages. Force those landmarks into the file_page allow-set, displacing the
    # lowest-scored picks so the budget total stays honest (see _ensure_landmarks).
    if selected_files or allocation.file_page > 0:
        file_candidate_set = {p for _, p in files}
        landmarks = [
            p
            for p in tour_landmark_paths(
                inputs.parsed_files,
                inputs.pagerank,
                max_landmarks=DEFAULT_MAX_LANDMARKS,
            )
            if p in file_candidate_set
        ]
        selected_files = _ensure_landmarks(selected_files, landmarks)

    # Deterministic coverage tail: every code file the budget dropped gets a
    # cheap zero-LLM page so the whole codebase is retrievable (Phase G).
    deterministic_tail = _select_deterministic_tail(files, selected_files, cfg)

    sel = Selection(
        file_page_paths=selected_files,
        deterministic_tail_paths=deterministic_tail,
        symbol_spotlights=[t for _, t in symbols[: allocation.symbol_spotlight]],
        # Never truncated, unlike every other bucket. The concept groups are a
        # total partition of the production files, so rationing them does not
        # produce a smaller wiki — it produces one with files that belong to no
        # page at all, and the tree stops being a map of the repository. The
        # cost of not rationing is bounded by construction rather than by a
        # budget: the grouper's size ladder holds the page count near 50-80 for
        # a repository this size and scales sublinearly above it.
        module_groups=[m for _, m in modules],
        concept_groups=list(concepts.groups),
        layer_labels=dict(concepts.layer_labels),
        api_contract_paths=[p for _, p in apis[: allocation.api_contract]],
        infra_paths=[p for _, p in infras[: allocation.infra_page]],
        scc_groups=[g for _, g in sccs[: allocation.scc_page]],
        emit_repo_overview=True,
        emit_arch_diagram=True,
        allocation=allocation,
    )

    log.info(
        "page_selection.complete",
        coverage_pct=pct,
        budget=budget,
        counts=sel.counts(),
    )
    return sel


def summarize_selection(sel: Selection) -> dict[str, int]:
    """Convenience wrapper returning the counts dict.

    Kept as a separate helper so the init UI can hand a Selection
    directly to its rendering layer without depending on the dataclass
    internals.
    """
    return sel.counts()
