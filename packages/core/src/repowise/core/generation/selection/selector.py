"""End-to-end page selection.

The single ``select_pages`` entry point returns an allow-set that both
``PageGenerator.generate_all`` and ``cost_estimator.build_generation_plan``
honor verbatim. No bypass paths — if a candidate isn't here, it isn't
emitted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

from .budget import BucketAllocation, allocate_budget, compute_budget
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
    ``community-<id>`` when grouping by community, the top-level
    directory when falling back to ``top_dir``.
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
    symbol_spotlights: list[tuple[str, str]] = field(default_factory=list)  # (file_path, symbol_name)
    module_groups: list[ModuleGroup] = field(default_factory=list)
    api_contract_paths: list[str] = field(default_factory=list)
    infra_paths: list[str] = field(default_factory=list)
    scc_groups: list[tuple[str, list[str]]] = field(default_factory=list)  # (scc_id, files)
    emit_repo_overview: bool = True
    emit_arch_diagram: bool = True
    allocation: BucketAllocation | None = None

    def counts(self) -> dict[str, int]:
        """Per-page-type counts (for cost estimation and the init UI)."""
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
    return (
        not fi.is_api_contract
        and not _is_infra_file(parsed)
        and fi.language in _CODE_LANGUAGES
    )


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
        )
        if s > 0.0:
            scored.append((s, path))
    scored.sort(key=lambda x: x[0], reverse=True)
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
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _build_module_groups(inputs: SelectionInputs) -> list[tuple[float, ModuleGroup]]:
    """Return scored module groups — either community-based or top-dir."""
    cfg = inputs.config
    min_size = max(1, getattr(cfg, "min_module_size", 3))
    use_communities = getattr(cfg, "module_grouping", "community") == "community"

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
                    file_paths=tuple(p.file_info.path for p in files),
                    label=group_label.get(key),
                    cohesion=group_cohesion.get(key),
                ),
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _build_api_candidates(inputs: SelectionInputs) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for p in inputs.parsed_files:
        if not p.file_info.is_api_contract:
            continue
        scored.append((score_api_contract(p), p.file_info.path))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _build_infra_candidates(inputs: SelectionInputs) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for p in inputs.parsed_files:
        if not _is_infra_file(p):
            continue
        scored.append((score_infra(p), p.file_info.path))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _build_scc_candidates(
    inputs: SelectionInputs,
) -> list[tuple[float, tuple[str, list[str]]]]:
    scored: list[tuple[float, tuple[str, list[str]]]] = []
    for i, scc in enumerate(inputs.sccs):
        files = list(scc)
        s = score_scc(cycle_size=len(files))
        if s <= 0.0:
            continue
        scored.append((s, (f"scc-{i}", sorted(files))))
    scored.sort(key=lambda x: x[0], reverse=True)
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

    allocation = allocate_budget(
        budget=budget,
        candidates_per_bucket=available,
        shares=_shares_from_config(cfg),
        n_files=len(inputs.parsed_files),
    )

    sel = Selection(
        file_page_paths=[p for _, p in files[: allocation.file_page]],
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


def language_summary(parsed_files: list[Any]) -> dict[str, int]:
    """Return ``{language: file_count}`` — used by the init UI to
    describe the repo shape next to the coverage table.
    """
    return dict(Counter(p.file_info.language for p in parsed_files))
