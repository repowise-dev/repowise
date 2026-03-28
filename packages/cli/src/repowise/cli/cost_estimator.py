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
_CODE_LANGUAGES = frozenset(
    {
        "python",
        "typescript",
        "javascript",
        "go",
        "rust",
        "java",
        "cpp",
        "c",
        "csharp",
        "ruby",
        "kotlin",
        "scala",
        "swift",
        "php",
    }
)


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
    "cross_package": (3000, 2000),
    "repo_overview": (5000, 3000),
    "architecture_diagram": (4000, 2500),
    "infra_page": (2000, 1500),
}

# Cost per 1K tokens (input, output).
# Exact model names are checked first; prefix fallbacks catch unknown variants.
_COST_TABLE_EXACT: dict[str, tuple[float, float]] = {
    # OpenAI GPT-5.4 family (prices per MTok → divide by 1000 for per-1K)
    "gpt-5.4": (0.0025, 0.015),  # $2.50/$15 per MTok
    "gpt-5.4-mini": (0.00075, 0.0045),  # $0.75/$4.50 per MTok
    "gpt-5.4-nano": (0.0002, 0.00125),  # $0.20/$1.25 per MTok
    # Gemini family
    "gemini-3.1-pro-preview": (0.002, 0.012),  # $2/$12 per MTok
    "gemini-3-flash-preview": (0.0005, 0.003),  # $0.50/$3 per MTok
    "gemini-3.1-flash-lite-preview": (0.00025, 0.0015),  # $0.25/$1.50 per MTok
    # Anthropic Claude 4.x family
    "claude-opus-4-6": (0.005, 0.025),  # $5/$25 per MTok
    "claude-sonnet-4-6": (0.003, 0.015),  # $3/$15 per MTok
    "claude-haiku-4-5": (0.001, 0.005),  # $1/$5 per MTok
}

# Prefix fallbacks for unknown model variants
_COST_TABLE_PREFIX: dict[str, tuple[float, float]] = {
    "gpt-5.4-nano": (0.0002, 0.00125),
    "gpt-5.4-mini": (0.00075, 0.0045),
    "gpt-5.4": (0.0025, 0.015),
    "claude-opus": (0.005, 0.025),
    "claude-sonnet": (0.003, 0.015),
    "claude-haiku": (0.001, 0.005),
    "claude": (0.003, 0.015),
    "gemini": (0.00025, 0.0015),
    "llama": (0.0, 0.0),
    "mock": (0.0, 0.0),
}


def _lookup_cost(model_name: str) -> tuple[float, float]:
    """Return (input_rate, output_rate) per 1K tokens for a model."""
    lower = model_name.lower()
    # Exact match first
    if lower in _COST_TABLE_EXACT:
        return _COST_TABLE_EXACT[lower]
    # Longest matching prefix wins
    best_prefix = ""
    best_rates = (0.0, 0.0)
    for prefix, rates in _COST_TABLE_PREFIX.items():
        if lower.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_rates = rates
    return best_rates


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

    code_files = [
        p
        for p in files
        if not p.file_info.is_api_contract
        and not _is_infra_file(p)
        and p.file_info.language in _CODE_LANGUAGES
    ]

    # Budget calculation — mirrors page_generator.generate_all() lines 544-564
    budget = max(50, int(len(files) * config.max_pages_pct))

    # Fixed overhead pages (always generated)
    api_count = sum(1 for p in files if p.file_info.is_api_contract)
    scc_count = sum(1 for scc in sccs if len(scc) > 1)
    modules: set[str] = set()
    for p in code_files:
        parts = Path(p.file_info.path).parts
        modules.add(parts[0] if len(parts) > 1 else "root")
    module_count = len(modules)
    fixed_overhead = api_count + scc_count + module_count + 2  # +2 = repo_overview + arch_diagram

    remaining = max(0, budget - fixed_overhead)

    # File page budget (priority over symbol_spotlight)
    code_pr_scores = sorted(
        [pagerank.get(p.file_info.path, 0.0) for p in code_files],
        reverse=True,
    )
    n_file_uncapped = (
        max(1, int(len(code_pr_scores) * config.file_page_top_percentile)) if code_pr_scores else 0
    )
    n_file_cap = min(n_file_uncapped, remaining)
    pr_threshold = code_pr_scores[n_file_cap - 1] if code_pr_scores and n_file_cap > 0 else 0.0

    # Actual file_page count: ALL files passing _is_significant_file
    # (betweenness > 0 and entry_point are independent of the PageRank threshold)
    file_page_count = sum(
        1
        for p in code_files
        if _is_significant_file(p, pagerank, betweenness, config, pr_threshold)
    )

    # Symbol spotlight budget
    sym_budget = max(0, remaining - n_file_cap)
    all_public_symbols = [
        (sym, p) for p in files for sym in p.symbols if sym.visibility == "public"
    ]
    n_sym_uncapped = (
        max(1, int(len(all_public_symbols) * config.top_symbol_percentile))
        if all_public_symbols
        else 0
    )
    n_sym_cap = min(n_sym_uncapped, sym_budget)

    # Infra page count
    infra_count = 0
    if not skip_infra:
        infra_count = sum(1 for p in files if _is_infra_file(p))

    # Cross-package count (monorepo only — estimate from inter-module edges)
    cross_package_count = 0
    try:
        # Check if monorepo structure exists
        if len(modules) > 1:
            # Count distinct cross-module import pairs
            seen_pairs: set[tuple[str, str]] = set()
            for u, v in graph.edges():
                u_parts = Path(u).parts
                v_parts = Path(v).parts
                u_mod = u_parts[0] if len(u_parts) > 1 else "root"
                v_mod = v_parts[0] if len(v_parts) > 1 else "root"
                if u_mod != v_mod:
                    pair = (u_mod, v_mod)
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
            cross_package_count = len(seen_pairs)
    except Exception:
        pass

    # Build plan list
    if api_count:
        plans.append(PageTypePlan("api_contract", api_count, 0))
    if n_sym_cap:
        plans.append(PageTypePlan("symbol_spotlight", n_sym_cap, 1))
    if file_page_count:
        plans.append(PageTypePlan("file_page", file_page_count, 2))
    if scc_count:
        plans.append(PageTypePlan("scc_page", scc_count, 3))
    if module_count:
        plans.append(PageTypePlan("module_page", module_count, 4))
    if cross_package_count:
        plans.append(PageTypePlan("cross_package", cross_package_count, 5))
    plans.append(PageTypePlan("repo_overview", 1, 6))
    plans.append(PageTypePlan("architecture_diagram", 1, 6))
    if infra_count:
        plans.append(PageTypePlan("infra_page", infra_count, 7))

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

    input_rate, output_rate = _lookup_cost(model_name)

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
