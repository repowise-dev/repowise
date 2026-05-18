"""Per-model token pricing.

Rates are USD per 1K tokens (input, output). Exact model names win
first; longest-prefix fallback catches unknown variants.
"""

from __future__ import annotations


# Exact-match rates. Per-MTok pricing divided by 1000.
_COST_TABLE_EXACT: dict[str, tuple[float, float]] = {
    # OpenAI GPT-5.4 family
    "gpt-5.4": (0.0025, 0.015),  # $2.50 / $15 per MTok
    "gpt-5.4-mini": (0.00075, 0.0045),  # $0.75 / $4.50 per MTok
    "gpt-5.4-nano": (0.0002, 0.00125),  # $0.20 / $1.25 per MTok
    # Gemini family
    "gemini-3.1-pro-preview": (0.002, 0.012),  # $2 / $12 per MTok
    "gemini-3-flash-preview": (0.0005, 0.003),  # $0.50 / $3 per MTok
    "gemini-3.1-flash-lite-preview": (0.00025, 0.0015),  # $0.25 / $1.50 per MTok
    # Anthropic Claude 4.x family
    "claude-opus-4-6": (0.005, 0.025),  # $5 / $25 per MTok
    "claude-sonnet-4-6": (0.003, 0.015),  # $3 / $15 per MTok
    "claude-haiku-4-5": (0.001, 0.005),  # $1 / $5 per MTok
}

# Prefix fallbacks for unknown variants.
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
    """Return ``(input_rate, output_rate)`` per 1K tokens for *model_name*."""
    lower = model_name.lower()
    if lower in _COST_TABLE_EXACT:
        return _COST_TABLE_EXACT[lower]
    best_prefix = ""
    best_rates = (0.0, 0.0)
    for prefix, rates in _COST_TABLE_PREFIX.items():
        if lower.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_rates = rates
    return best_rates
