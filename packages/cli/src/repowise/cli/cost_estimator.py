"""Pre-generation cost estimation — mirrors page_generator.generate_all() selection logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Infra / significance helpers (mirrored from page_generator.py)
# ---------------------------------------------------------------------------

_INFRA_LANGUAGES = frozenset({"dockerfile", "makefile", "terraform", "shell"})
_INFRA_FILENAMES = frozenset({"Dockerfile", "Makefile", "GNUmakefile"})
_CODE_LANGUAGES = frozenset({
    "python", "typescript", "javascript", "go", "rust",
    "java", "cpp", "c", "csharp", "ruby", "kotlin", "scala",
    "swift", "php",
})


def _is_infra_file(parsed: Any) -> bool:
    lang = parsed.file_info.language
    if lang in _INFRA_LANGUAGES:
        return True
    name = Path(parsed.file_info.path).name
    return name in _INFRA_FILENAMES


def _is_significant_file(
    parsed: Any,
    pagerank: dict[str, float],
    betweenness: dict[str, float],
    config: Any,
    pr_threshold: float,
) -> bool:
    if len(parsed.symbols) < config.file_page_min_symbols:
        return False
    path = parsed.file_info.path
    return (
        parsed.file_info.is_entry_point
        or pagerank.get(path, 0.0) >= pr_threshold
        or betweenness.get(path, 0.0) > 0.0
    )


# ---------------------------------------------------------------------------
# Plan and estimate data types
# ---------------------------------------------------------------------------


@dataclass
class PageTypePlan:
    """Count of pages to generate for a given page type."""

    page_type: str
    count: int
    level: int


@dataclass
class CostEstimate:
    """Estimated cost for a generation run."""

    plans: list[PageTypePlan] = field(default_factory=list)
    total_pages: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    provider_name: str = ""
    model_name: str = ""


# Token heuristics per page type (input, output)
_TOKEN_HEURISTICS: dict[str, tuple[int, int]] = {
    "api_contract": (3000, 2000),
    "symbol_spotlight": (2000, 1500),
    "file_page": (4000, 2500),
    "scc_page": (3000, 2000),
    "module_page": (4000, 2500),
    "repo_overview": (5000, 3000),
    "architecture_diagram": (4000, 2500),
    "infra_page": (2000, 1500),
}

# Cost per 1K tokens (input, output) by model prefix
_COST_TABLE: dict[str, tuple[float, float]] = {
    "claude": (0.003, 0.015),
    "gpt-4o": (0.005, 0.015),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5": (0.0005, 0.0015),
    "llama": (0.0, 0.0),
    "mock": (0.0, 0.0),
}


def build_generation_plan(
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    skip_tests: bool = False,
    skip_infra: bool = False,
) -> list[PageTypePlan]:
    """Replicate the page selection logic from ``generate_all()`` without calling the LLM.

    Returns a list of :class:`PageTypePlan` entries — one per page type.
    """
    graph = graph_builder.graph()
    pagerank = graph_builder.pagerank()
    betweenness = graph_builder.betweenness_centrality()
    sccs = graph_builder.strongly_connected_components()

    plans: list[PageTypePlan] = []

    # Optionally filter
    files = parsed_files
    if skip_tests:
        files = [p for p in files if not p.file_info.is_test]

    # Level 0: api_contract
    api_count = sum(1 for p in files if p.file_info.is_api_contract)
    if api_count:
        plans.append(PageTypePlan("api_contract", api_count, 0))

    # Level 1: symbol_spotlight (top percentile public symbols by PageRank)
    all_public_symbols = []
    for p in files:
        for sym in p.symbols:
            if sym.visibility == "public":
                all_public_symbols.append((sym, p))
    n_top_sym = max(1, int(len(all_public_symbols) * config.top_symbol_percentile)) if all_public_symbols else 0
    if n_top_sym:
        plans.append(PageTypePlan("symbol_spotlight", n_top_sym, 1))

    # Level 2: file_page (significant code files)
    code_files = [
        p for p in files
        if not p.file_info.is_api_contract
        and not _is_infra_file(p)
        and p.file_info.language in _CODE_LANGUAGES
    ]
    code_pr_scores = sorted(
        [pagerank.get(p.file_info.path, 0.0) for p in code_files],
        reverse=True,
    )
    n_top_files = max(1, int(len(code_pr_scores) * config.file_page_top_percentile)) if code_pr_scores else 0
    pr_threshold = code_pr_scores[n_top_files - 1] if code_pr_scores else 0.0

    file_page_count = sum(
        1 for p in code_files
        if _is_significant_file(p, pagerank, betweenness, config, pr_threshold)
    )
    if file_page_count:
        plans.append(PageTypePlan("file_page", file_page_count, 2))

    # Level 3: scc_page (cycles with len > 1)
    scc_count = sum(1 for scc in sccs if len(scc) > 1)
    if scc_count:
        plans.append(PageTypePlan("scc_page", scc_count, 3))

    # Level 4: module_page (top-level directory grouping)
    modules: set[str] = set()
    for p in code_files:
        parts = Path(p.file_info.path).parts
        modules.add(parts[0] if len(parts) > 1 else "root")
    if modules:
        plans.append(PageTypePlan("module_page", len(modules), 4))

    # Level 6: repo_overview + architecture_diagram
    plans.append(PageTypePlan("repo_overview", 1, 6))
    plans.append(PageTypePlan("architecture_diagram", 1, 6))

    # Level 7: infra_page
    if not skip_infra:
        infra_count = sum(1 for p in files if _is_infra_file(p))
        if infra_count:
            plans.append(PageTypePlan("infra_page", infra_count, 7))

    # Global budget cap: total pages ≤ max(50, N_files * max_pages_pct).
    # Fixed overhead pages (repo_overview, arch, module, scc, api_contract) are
    # always kept. file_page has priority over symbol_spotlight, which has
    # priority over infra_page.
    budget = max(50, int(len(files) * config.max_pages_pct))
    _FIXED = {"repo_overview", "architecture_diagram", "module_page", "scc_page", "api_contract"}
    _PRIORITY = ["file_page", "symbol_spotlight", "infra_page"]

    fixed_plans = [p for p in plans if p.page_type in _FIXED]
    adjustable = {p.page_type: p for p in plans if p.page_type not in _FIXED}
    fixed_total = sum(p.count for p in fixed_plans)
    remaining = max(0, budget - fixed_total)

    if sum(p.count for p in adjustable.values()) > remaining:
        new_adjustable: list[PageTypePlan] = []
        left = remaining
        for pt in _PRIORITY:
            p = adjustable.get(pt)
            if p:
                take = min(p.count, left)
                if take > 0:
                    new_adjustable.append(PageTypePlan(pt, take, p.level))
                left = max(0, left - take)
        plans = sorted(fixed_plans + new_adjustable, key=lambda p: p.level)

    return plans


def estimate_cost(
    plans: list[PageTypePlan],
    provider_name: str,
    model_name: str,
) -> CostEstimate:
    """Estimate token counts and USD cost from a generation plan."""
    total_pages = sum(p.count for p in plans)
    total_input = 0
    total_output = 0

    for plan in plans:
        inp, out = _TOKEN_HEURISTICS.get(plan.page_type, (3000, 2000))
        total_input += inp * plan.count
        total_output += out * plan.count

    # Find cost rates
    input_rate, output_rate = 0.0, 0.0
    model_lower = model_name.lower()
    for prefix, rates in _COST_TABLE.items():
        if prefix in model_lower:
            input_rate, output_rate = rates
            break

    cost = (total_input / 1000) * input_rate + (total_output / 1000) * output_rate

    return CostEstimate(
        plans=plans,
        total_pages=total_pages,
        estimated_input_tokens=total_input,
        estimated_output_tokens=total_output,
        estimated_cost_usd=cost,
        provider_name=provider_name,
        model_name=model_name,
    )
