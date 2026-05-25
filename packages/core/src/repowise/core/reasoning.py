"""Shared reasoning-mode helpers for generation providers."""

from __future__ import annotations

import os
from typing import Literal, cast

ReasoningMode = Literal[
    "auto",
    "off",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]
REASONING_MODES: tuple[ReasoningMode, ...] = (
    "auto",
    "off",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def normalize_reasoning(
    value: object,
    *,
    default: ReasoningMode = "auto",
) -> ReasoningMode:
    """Normalize a user/config supplied reasoning mode.

    ``auto`` preserves provider defaults. The remaining values are portable
    intents; each provider decides whether and how to translate them.
    """
    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized not in REASONING_MODES:
        choices = ", ".join(REASONING_MODES)
        raise ValueError(f"reasoning must be one of: {choices}")
    return cast(ReasoningMode, normalized)


def resolve_reasoning(
    reasoning: str | None = None,
    config: dict[str, object] | None = None,
) -> ReasoningMode:
    """Resolve reasoning from explicit value, env, repo config, then default."""
    raw = reasoning or os.environ.get("REPOWISE_REASONING")
    if raw is None and config:
        raw = config.get("reasoning")
    return normalize_reasoning(raw)
