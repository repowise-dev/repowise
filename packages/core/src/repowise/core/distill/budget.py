"""Token estimation for distill — the shared 4-chars-per-token heuristic.

Every repowise subsystem reports savings on this one scale, and no subsystem
carries a tiktoken dependency to do it.

The estimator *lives* here, and ``generation.context.token_budget`` re-exports
it, rather than the other way round. Deliberate: distill is a leaf utility
with no package-level graph, while ``generation.context``'s ``__init__`` pulls
the assembler → the ingestion models → networkx (538ms). Importing four lines
of arithmetic must not cost that, on the hook path or anywhere else.
"""

from __future__ import annotations

__all__ = ["estimate_tokens", "savings_pct"]


def estimate_tokens(text: str) -> int:
    """Estimate token count using the 4-chars-per-token heuristic."""
    return len(text) // 4


def savings_pct(raw_tokens: int, distilled_tokens: int) -> float:
    """Percentage of tokens saved by distillation (0.0 when nothing saved)."""
    if raw_tokens <= 0:
        return 0.0
    return max(0.0, (raw_tokens - distilled_tokens) / raw_tokens * 100.0)
