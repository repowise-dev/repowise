"""Shared vocabulary mapping for the built-in sections."""

from __future__ import annotations

from typing import Any

from repowise.core.ingestion.symbol_identity import local_symbol_id

#: An action's tier as the report spells it.
TIER_SEVERITY = {"act_now": "act_now", "plan": "plan", "improve_signal": "improve"}


def effort(bucket: str | None) -> str:
    """S, M or L; a queue's XL reads as L, an unknown one as M."""
    if bucket in ("S", "M", "L"):
        return bucket
    return "L" if bucket == "XL" else "M"


def confidence(level: Any) -> str:
    return level if level in ("high", "medium", "low") else "medium"


def short_symbol(symbol: str | None) -> str | None:
    """``C::m`` from ``a.py::C::m``; a bare name stays as it is."""
    if not symbol:
        return None
    local = local_symbol_id(symbol) if "::" in symbol else symbol
    return None if not local or local == "__module__" else local
