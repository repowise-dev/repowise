"""End-to-end page selection.

The single ``select_pages`` entry point returns an allow-set that both
``PageGenerator.generate_all`` and ``cost_estimator.build_generation_plan``
honor verbatim. No bypass paths — if a candidate isn't here, it isn't
emitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

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
    score_module,
    score_scc,
    score_symbol,
)

log = structlog.get_logger(__name__)

_INFRA_LANGUAGES = _LANG_REGISTRY.infra_languages()
_INFRA_FILENAMES = frozenset({"Dockerfile", "Makefile", "GNUmakefile"})
_CODE_LANGUAGES = _LANG_REGISTRY.code_languages()


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleGroup:
    """One ``module_page`` worth of files.

    ``key`` is the stable identifier persisted as ``target_path``:
    the module's real directory path when grouping by curated KG modules
    (so path-prefix child lookups work), ``community-<id>`` when grouping
    by community, the top-level directory when falling back to ``top_dir``.
    """

    key: str
    display: str
    language: str
    file_paths: tuple[str, ...]
    label: str | None = None
    cohesion: float | None = None


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
    # passed through — never re-derived here. Only read when
    # ``config.module_grouping == "curated"``; ``None``/empty falls back to
    # community grouping (the fallback-matrix "degraded" row).
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


def _build_curated_module_groups(
    inputs: SelectionInputs, min_size: int
) -> list[tuple[float, ModuleGroup]] | None:
    """Scored groups from curated KG modules, or ``None`` when unavailable.

    ``key`` = the module's real directory path (its ``target_path``, so the
    MCP child lookup ``target_path LIKE 'dir/%'`` works), ``display``/``label``
    = the curated human name, ``cohesion`` = None (the community block in the
    template is conditional and has no meaning for directory groups). Ranked
    by Σ PageRank of members — better than ``score_module``'s cohesion term,
    which is meaningless here. Returns ``None`` only when no curated modules
    were passed in (→ community fallback); an artifact whose modules all fall
    below the floor yields an empty list, not a vocabulary mix.
    """
    if not inputs.kg_modules:
        return None

    code_by_path = {p.file_info.path: p for p in inputs.parsed_files if _is_code_file(p)}
    scored: list[tuple[float, ModuleGroup]] = []
    seen_keys: set[str] = set()
    for module in inputs.kg_modules:
        if module.get("wholeLayer"):
            # 1:1 with a layer page (single-module layers, flat libs) — a
            # module doc would re-document the layer. Skip the page; the
            # module stays in the KG artifact for canvas/coverage.
            continue
        member_paths = sorted(
            path
            for nid in module.get("nodeIds", [])
            if isinstance(nid, str) and (path := nid.removeprefix("file:")) in code_by_path
        )
        if len(member_paths) < min_size:
            continue
        key = module.get("path") or (module.get("id") or "").removeprefix("module:")
        if not key or key in seen_keys:
            # Rare cross-layer dir collision: the slug id is still unique.
            key = (module.get("id") or "").removeprefix("module:")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        name = module.get("name") or key
        language = module.get("language") or code_by_path[member_paths[0]].file_info.language
        score = sum(inputs.pagerank.get(p, 0.0) for p in member_paths)
        scored.append(
            (
                score,
                ModuleGroup(
                    key=key,
                    display=name,
                    language=language,
                    file_paths=tuple(member_paths),
                    label=name,
                    cohesion=None,
                ),
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1].key))
    return scored


def _build_module_groups(inputs: SelectionInputs) -> list[tuple[float, ModuleGroup]]:
    """Return scored module groups — curated, community-based, or top-dir."""
    cfg = inputs.config
    min_size = max(1, getattr(cfg, "min_module_size", 3))
    grouping = getattr(cfg, "module_grouping", "community")

    if grouping == "curated":
        curated = _build_curated_module_groups(inputs, min_size)
        if curated is not None:
            return curated
        grouping = "community"  # no curated artifact → today's path, unchanged

    use_communities = grouping == "community"

    # Bucket files into groups.
    groups: dict[str, list[Any]] = {}
    group_lang: dict[str, str] = {}
    group_label: dict[str, str | None] = {}
    group_cohesion: dict[str, float | None] = {}
    group_display: dict[str, str] = {}

    if use_communities and inputs.community_info:
        for p in inputs.parsed_files:
            if not _is_code_file(p):
                continue
            cid = inputs.community.get(p.file_info.path)
            if cid is None:
                continue
            key = f"community-{cid}"
            groups.setdefault(key, []).append(p)
            group_lang.setdefault(key, p.file_info.language)
            if key not in group_display:
                ci = inputs.community_info.get(cid)
                label = getattr(ci, "label", "") or f"cluster_{cid}"
                group_display[key] = label
                group_label[key] = label
                group_cohesion[key] = float(getattr(ci, "cohesion", 0.0) or 0.0)
    else:
        for p in inputs.parsed_files:
            if not _is_code_file(p):
                continue
            parts = Path(p.file_info.path).parts
            key = parts[0] if len(parts) > 1 else "root"
            groups.setdefault(key, []).append(p)
            group_lang.setdefault(key, p.file_info.language)
            group_display.setdefault(key, key)
            group_label.setdefault(key, None)
            group_cohesion.setdefault(key, None)

    scored: list[tuple[float, ModuleGroup]] = []
    for key, files in groups.items():
        s = score_module(
            size=len(files),
            cohesion=group_cohesion.get(key) or 0.0,
            min_module_size=min_size,
        )
        if s <= 0.0:
            continue
        scored.append(
            (
                s,
                ModuleGroup(
                    key=key,
                    display=group_display.get(key, key),
                    language=group_lang.get(key, "unknown"),
                    file_paths=tuple(sorted(p.file_info.path for p in files)),
                    label=group_label.get(key),
                    cohesion=group_cohesion.get(key),
                ),
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1].key))
    return scored


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
        "module_page": getattr(cfg, "module_page_share", 0.10),
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
    modules: list,
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
        module_groups=[m for _, m in modules],
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
    modules = _build_module_groups(inputs)
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
            files, symbols, modules, apis, infras, sccs, available, include_symbols=False
        )

    # Scoped generation (``repowise generate``): full-coverage allow-set so
    # ``only_page_ids`` can name any page. Spotlights kept so one can be
    # regenerated on demand.
    if getattr(inputs, "select_all", False):
        return _select_everything(
            files, symbols, modules, apis, infras, sccs, available, include_symbols=True
        )

    allocation = allocate_budget(
        budget=budget,
        candidates_per_bucket=available,
        shares=_shares_from_config(cfg),
        n_files=len(inputs.parsed_files),
    )

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
        module_groups=[m for _, m in modules[: allocation.module_page]],
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
