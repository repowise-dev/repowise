"""Shared logging hooks for the incremental refresh steps."""

from __future__ import annotations

from collections.abc import Callable

import structlog

# One logger for the whole package, named as before the split.
logger = structlog.get_logger("repowise.core.pipeline.incremental")

LogFn = Callable[[str], None]


def _noop_log(message: str) -> None:  # pragma: no cover - trivial
    return None
