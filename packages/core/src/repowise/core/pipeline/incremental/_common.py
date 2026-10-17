"""Shared logging hooks for the incremental refresh steps."""

from __future__ import annotations

from collections.abc import Callable

import structlog

from repowise.core.pipeline.step_runner import _noop_log

# One logger for the whole package, under the package's name.
logger = structlog.get_logger("repowise.core.pipeline.incremental")

LogFn = Callable[[str], None]

__all__ = ["LogFn", "_noop_log", "logger"]
