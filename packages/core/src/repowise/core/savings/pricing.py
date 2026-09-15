"""Per-event pricing projections; history is never repriced."""

from __future__ import annotations

import math


def price_tokens(tokens: int, usd_per_million: float | None) -> float | None:
    """Price one token dimension from its captured event-time rate."""
    if usd_per_million is None:
        return None
    if not math.isfinite(usd_per_million) or usd_per_million < 0:
        raise ValueError("pricing rate must be finite and nonnegative")
    return max(tokens, 0) * usd_per_million / 1_000_000
