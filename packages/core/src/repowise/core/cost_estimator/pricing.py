"""Per-model token pricing.

Rates are USD per 1K tokens (input, output). Exact model names win
first; longest-prefix fallback catches unknown variants.

Ceiling: input is priced at one rate, so cached input is billed as if it
were fresh. Providers now discount it heavily (gpt-5.6-luna reads cached
input at $0.02/MTok, a 10x cut), which makes every figure here an
over-estimate on a re-run: safe direction, wrong number.

The token count is not the missing piece: ``cached_tokens`` already flows
provider -> ``GeneratedPage`` -> the ``pages`` table, and the run report
prints it. What is missing is a third rate (here, in
``generation/cost_tracker.py``, and in the TS mirror at
``packages/ui/src/dashboard/quick-actions.tsx``), plus splitting the
cached count out of ``input_tokens`` in the cost arithmetic. Worth doing
when we report cost per re-index, where the discount is the whole story;
not worth it to move a one-off init estimate by ~10%.
"""

from __future__ import annotations

# Exact-match rates. Per-MTok pricing divided by 1000.
_COST_TABLE_EXACT: dict[str, tuple[float, float]] = {
    # OpenAI GPT-5.4 family
    "gpt-5.4": (0.0025, 0.015),  # $2.50 / $15 per MTok
    "gpt-5.4-mini": (0.00075, 0.0045),  # $0.75 / $4.50 per MTok
    "gpt-5.4-nano": (0.0002, 0.00125),  # $0.20 / $1.25 per MTok
    # OpenAI GPT-5.6 family. Same input rate as nano, cheaper output: near
    # enough to alias by eye, which is exactly why it gets a real row.
    "gpt-5.6-luna": (0.0002, 0.0012),  # $0.20 / $1.20 per MTok
    # Gemini family
    "gemini-3.1-pro-preview": (0.002, 0.012),  # $2 / $12 per MTok
    "gemini-3-flash-preview": (0.0005, 0.003),  # $0.50 / $3 per MTok
    "gemini-3.1-flash-lite-preview": (0.00025, 0.0015),  # $0.25 / $1.50 per MTok
    "gemini-3.5-flash-lite": (0.00025, 0.0015),  # $0.25 / $1.50 per MTok
    # Anthropic Claude 4.x family
    "claude-opus-4-6": (0.005, 0.025),  # $5 / $25 per MTok
    "claude-sonnet-4-6": (0.003, 0.015),  # $3 / $15 per MTok
    "claude-haiku-4-5": (0.001, 0.005),  # $1 / $5 per MTok
}

# Prefix fallbacks for unknown variants. No `gpt-5.6` catch-all: the 5.6
# variants are not one price tier, and an unpriced model reads as free here
# (``_lookup_cost`` falls through to (0.0, 0.0)), so a guess would be worse
# than the miss it hides.
_COST_TABLE_PREFIX: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (0.0002, 0.0012),
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
    "codex_cli/": (0.0, 0.0),
    "claude_cli/": (0.0, 0.0),
    "opencode/": (0.0, 0.0),
}


def _lookup_cost(model_name: str) -> tuple[float, float]:
    """Return ``(input_rate, output_rate)`` per 1K tokens for *model_name*."""
    lower = model_name.lower()
    # OpenRouter/LiteLLM slugs carry a routing prefix (`google/gemini-3.5-flash-lite`)
    # that hides the model from every entry below, which priced them at zero.
    # `codex_cli/` and `opencode/` are genuinely free, so they keep their prefixes.
    if "/" in lower and not lower.startswith(("codex_cli/", "claude_cli/", "opencode/")):
        lower = lower.rsplit("/", 1)[-1]
    if lower in _COST_TABLE_EXACT:
        return _COST_TABLE_EXACT[lower]
    best_prefix = ""
    best_rates = (0.0, 0.0)
    for prefix, rates in _COST_TABLE_PREFIX.items():
        if lower.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_rates = rates
    return best_rates
