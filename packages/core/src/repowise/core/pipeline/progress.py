"""Progress reporting protocol for the repowise pipeline.

Decouples pipeline execution from UI concerns. The CLI implements this
with Rich progress bars; Modal uses structured logging; tests pass None.

Phase names (stable strings used across all implementations):
    traverse, parse, graph, git, co_change, dead_code, decisions, generation
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# Top-level stages of a pipeline run, in order. These are the ones a user
# counts ("Phase 2 of 4"); the phase names above are the sub-steps inside them.
STAGE_INGESTION = "ingestion"
STAGE_ANALYSIS = "analysis"


def emit_stage(progress: Any, stage: str) -> None:
    """Announce a top-level stage, for callbacks that render stage headers.

    Optional, like ``on_phase_done``: the pipeline runs headlessly as well as
    under a CLI, so the header itself belongs to whoever is drawing the screen
    (which is also the only party that knows how many stages follow this one).
    """
    if progress is None:
        return
    fn = getattr(progress, "on_stage", None)
    if fn is not None:
        fn(stage)


@runtime_checkable
class ProgressCallback(Protocol):
    """Protocol for pipeline progress reporting."""

    def on_phase_start(self, phase: str, total: int | None) -> None:
        """Called when a pipeline phase begins. *total* may be None for indeterminate phases."""
        ...

    def on_item_done(self, phase: str) -> None:
        """Called after one unit of work completes within a phase."""
        ...

    def on_phase_done(self, phase: str) -> None:
        """Called when a pipeline phase finishes — implementations should
        hide the still-rendering progress task so phase-summary lines that
        follow aren't interleaved with stale spinners. Optional; default
        implementations should be a no-op so existing callers keep working.
        """
        ...

    def on_message(self, level: str, text: str) -> None:
        """Emit a free-form message. *level* is 'info', 'warning', or 'error'."""
        ...

    def on_stage(self, stage: str) -> None:
        """Called when a top-level stage begins. Optional — reach it via
        :func:`emit_stage`, which no-ops for callbacks that do not render
        stage headers.
        """
        ...


class LoggingProgressCallback:
    """Emits progress as structured log messages. Suitable for headless workers (Modal)."""

    def on_phase_start(self, phase: str, total: int | None) -> None:
        logger.info("phase_start", phase=phase, total=total)

    def on_item_done(self, phase: str) -> None:
        logger.debug("item_done", phase=phase)

    def on_phase_done(self, phase: str) -> None:
        logger.info("phase_done", phase=phase)

    def on_message(self, level: str, text: str) -> None:
        getattr(logger, level, logger.info)(text)

    def on_stage(self, stage: str) -> None:
        logger.info("stage_start", stage=stage)
