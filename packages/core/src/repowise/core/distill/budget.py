"""Token estimation for distill — thin façade over the shared heuristic.

Reuses the generation-side 4-chars-per-token estimator so every repowise
subsystem reports savings on the same scale. No tiktoken dependency.
"""

from __future__ import annotations

from repowise.core.generation.context.token_budget import estimate_tokens

__all__ = ["estimate_tokens", "savings_pct"]


def savings_pct(raw_tokens: int, distilled_tokens: int) -> float:
    """Percentage of tokens saved by distillation (0.0 when nothing saved)."""
    if raw_tokens <= 0:
        return 0.0
    return max(0.0, (raw_tokens - distilled_tokens) / raw_tokens * 100.0)
