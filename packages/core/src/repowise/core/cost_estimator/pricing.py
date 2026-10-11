"""Per-model token pricing.

Rates are USD per 1K tokens (input, output). Exact model names win
first; longest-prefix fallback catches unknown variants.

Ceiling: input is priced at one rate, so cached input is billed as if it
were fresh. Providers now discount it heavily (gpt-5.6-luna reads cached
input at $0.02/MTok, a 10x cut), which makes every figure here an
over-estimate on a re-run: safe direction, wrong number.

The token count is not the missing piece: ``cached_tokens`` already flows
provider -> ``GeneratedPage`` -> the ``pages`` table, and the run report
prints it. What is missing is a third rate (here and in
``generation/cost_tracker.py``), plus splitting the
cached count out of ``input_tokens`` in the cost arithmetic. Worth doing
when we report cost per re-index, where the discount is the whole story;
not worth it to move a one-off init estimate by ~10%.
"""

from __future__ import annotations

from repowise.core.providers.llm.specs import ZERO_COST_MODEL_PREFIXES

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
    "claude-haiku-5-5": (0.0001, 0.0005),  # $0.10 / $0.50 per MTok, prompts <= 100K
    # DeepSeek V4 — https://api-docs.deepseek.com/quick_start/pricing
    "deepseek-v4-flash": (0.00027, 0.00110),  # $0.27 / $1.10 per MTok
    "deepseek-v4-pro": (0.00055, 0.00219),  # $0.55 / $2.19 per MTok
    "deepseek-chat": (0.00027, 0.00110),
    "deepseek-reasoner": (0.00055, 0.00219),
    # Kimi / Moonshot — https://platform.moonshot.cn/docs/pricing/chat
    "kimi-for-coding": (0.0006, 0.0024),  # $0.60 / $2.40 per MTok
    "kimi-k2.5": (0.0006, 0.0024),
    "kimi-k2.6": (0.0006, 0.0024),
}

# Prefix fallbacks for unknown variants. No `gpt-5.6` catch-all: the 5.6
# variants are not one price tier, and an unpriced model reads as free here
# (the estimator prices a ``None`` from ``lookup_cost`` at zero), so a guess would be worse
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
    # DeepSeek V4 family — https://api-docs.deepseek.com/quick_start/pricing
    # deepseek-chat was the V3 alias; V4 Flash/Pro are the current names.
    "deepseek-v4-pro": (0.00055, 0.00219),
    "deepseek-v4-flash": (0.00027, 0.00110),
    "deepseek-v4": (0.00027, 0.00110),
    "deepseek-chat": (0.00027, 0.00110),
    "deepseek-reasoner": (0.00055, 0.00219),
    "deepseek": (0.00027, 0.00110),
    # Kimi / Moonshot — https://platform.moonshot.cn/docs/pricing/chat
    "kimi-k2.6": (0.0006, 0.0024),
    "kimi-k2.5": (0.0006, 0.0024),
    "kimi-k2": (0.0006, 0.0024),
    "kimi-for-coding": (0.0006, 0.0024),
    "kimi": (0.0006, 0.0024),
    "moonshot": (0.0006, 0.0024),
    "llama": (0.0, 0.0),
    "mock": (0.0, 0.0),
    **dict.fromkeys(ZERO_COST_MODEL_PREFIXES, (0.0, 0.0)),
}


def lookup_cost(model_name: str) -> tuple[float, float] | None:
    """Return ``(input_rate, output_rate)`` per 1K tokens for *model_name*.

    ``None`` when no entry matches: the model is unpriced, which is not the
    same as free (the explicit zero rows above).
    """
    lower = model_name.lower()
    # OpenRouter/LiteLLM slugs carry a routing prefix (`google/gemini-3.5-flash-lite`)
    # that hides the model from every entry below, which priced them at zero.
    # The agent-CLI prefixes are genuinely free, so they keep their prefixes.
    if "/" in lower and not lower.startswith(ZERO_COST_MODEL_PREFIXES):
        lower = lower.rsplit("/", 1)[-1]
    if lower in _COST_TABLE_EXACT:
        return _COST_TABLE_EXACT[lower]
    best_prefix = ""
    best_rates: tuple[float, float] | None = None
    for prefix, rates in _COST_TABLE_PREFIX.items():
        if lower.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_rates = rates
    return best_rates
