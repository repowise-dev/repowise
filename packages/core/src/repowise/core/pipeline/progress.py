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


def emit_warning(progress: Any, text: str) -> None:
    """Report a degradation on the one channel a default run actually shows.

    ``logger.warning`` is invisible in every CLI run: ``configure_cli_logging``
    pins ``repowise.core`` to ERROR unless ``--verbose``, and the structlog
    filtering bound logger drops the record before it is formatted. So a phase
    could fail completely — three decision sources returning nothing, the parse
    pool dying and falling back to sequential — and the run printed nothing at
    all. ``vector_store/_base.py`` already reached this conclusion and logs its
    truncation report at ``error`` to get around it; that works, but it makes
    the level describe the plumbing rather than the severity.

    ``on_message`` is the channel the CLI renders, and it survives a non-TTY:
    Rich's ``Live`` passes renderables straight through when the console is not
    interactive, so a piped or CI run still gets the line (asserted in
    ``tests/unit/cli/test_progress_non_tty.py`` — that is the load-bearing
    assumption here, not an incidental detail).

    Call this *in addition to* the structured ``logger.warning``, not instead
    of it: the log keeps the machine-readable key and fields, this carries the
    sentence a human or an agent reads. Never raises — a reporting failure must
    not abort a run that was otherwise fine.
    """
    if progress is None:
        return
    fn = getattr(progress, "on_message", None)
    if fn is None:
        return
    try:
        fn("warning", text)
    except Exception:
        logger.debug("progress_warning_emit_failed", text=text, exc_info=True)


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
