"""Per-page-type token heuristics.

These default averages were recalibrated after observing a real run on
the repowise repo where the prior estimate (4000/2500 per ``file_page``)
was ~5-8× under-estimating actual input tokens. Real pages include the
full Jinja2-rendered context: file path + symbol signatures + imports +
exports + source snippet + dep summaries + RAG context + git metadata.

The estimator prefers SQLite telemetry when available
(see :mod:`calibration`); these heuristics are the cold-start fallback.
"""

from __future__ import annotations


# (input_tokens, output_tokens) per page. Heuristics that bias slightly
# high — better to over-estimate cost than surprise the user with a
# bill that's an order of magnitude over the quote.
_TOKEN_HEURISTICS: dict[str, tuple[int, int]] = {
    "api_contract": (8_000, 3_000),
    "symbol_spotlight": (8_000, 2_500),
    "file_page": (25_000, 5_000),
    "scc_page": (12_000, 4_000),
    "module_page": (45_000, 6_000),
    "repo_overview": (30_000, 5_000),
    "architecture_diagram": (25_000, 5_000),
    "infra_page": (4_000, 2_000),
    "onboarding": (15_000, 3_500),
}


# Variance bracket used to derive the low/high range. Plus/minus 25%
# captures roughly one standard deviation of observed variance in mixed
# real-world generation runs.
HEURISTIC_VARIANCE = 0.25


def heuristic_tokens(page_type: str) -> tuple[int, int]:
    """Return ``(input, output)`` tokens for *page_type*; safe default."""
    return _TOKEN_HEURISTICS.get(page_type, (10_000, 3_000))
