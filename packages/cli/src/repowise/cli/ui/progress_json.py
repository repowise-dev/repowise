"""The ``--progress json`` twin of :class:`~repowise.cli.ui.progress.RichProgressCallback`.

Same interface, same phase labels and units; it writes ``stage``, ``phase`` and
``warning`` events (see :mod:`repowise.cli.agent_events`) instead of drawing.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator

from repowise.cli.agent_events import EventStream
from repowise.cli.ui import checklist
from repowise.cli.ui.progress import _DEGRADING_LEVELS, _PHASE_LABELS, _PHASE_UNITS, _STAGES
from repowise.core.redaction import redact_secrets


class JsonProgressCallback:
    """Implements ``repowise.core.pipeline.ProgressCallback`` as NDJSON events."""

    def __init__(self, events: EventStream, *, stage: str | None = None) -> None:
        self._events = events
        # phase -> [completed, total, started_at]
        self._steps: dict[str, list[float | None]] = {}
        self._done: set[str] = set()
        #: Every warning emitted, in order, as RichProgressCallback keeps them.
        self.warnings: list[str] = []
        if stage is not None:
            self.on_stage(stage)

    def on_stage(self, stage: str) -> None:
        if stage in _STAGES:
            self._events.emit("stage", stage=stage, title=_STAGES[stage][0])

    def _phase(self, phase: str, state: str, elapsed: float | None) -> None:
        completed, total, _ = self._steps[phase]
        label, _depth = checklist.split_label(_PHASE_LABELS.get(phase, phase))
        self._events.emit(
            "phase",
            phase=phase,
            label=label,
            state=state,
            completed=int(completed or 0),
            total=None if total is None else int(total),
            unit=_PHASE_UNITS.get(phase, ""),
            elapsed_s=None if elapsed is None else round(elapsed, 3),
        )

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self._done.discard(phase)
        step = self._steps.get(phase)
        if step is not None:
            # A phase restarted with a new total keeps its count and clock.
            step[1] = total
            return
        self._steps[phase] = [0, total, time.monotonic()]
        self._phase(phase, "start", None)

    def on_item_done(self, phase: str) -> None:
        step = self._steps.get(phase)
        if step is not None:
            step[0] = (step[0] or 0) + 1

    def on_item_label(self, phase: str, label: str) -> None:
        """Per-item labels are for a live display; a stream would drown in them."""

    def _finish(self, phase: str, state: str) -> None:
        step = self._steps.get(phase)
        if step is None or phase in self._done:
            return
        self._done.add(phase)
        if state == "done" and step[1] is not None:
            step[0] = step[1]
        self._phase(phase, state, time.monotonic() - (step[2] or 0.0))

    def on_phase_done(self, phase: str) -> None:
        self._finish(phase, "done")

    def on_phase_failed(self, phase: str) -> None:
        self._finish(phase, "failed")

    @contextlib.contextmanager
    def step(self, phase: str) -> Iterator[None]:
        self.on_phase_start(phase, None)
        try:
            yield
        except BaseException:
            self.on_phase_failed(phase)
            raise
        self.on_phase_done(phase)

    def on_message(self, level: str, text: str) -> None:
        if level not in _DEGRADING_LEVELS:
            return
        text = redact_secrets(text)
        self.warnings.append(text)
        self._events.emit("warning", level=level, text=text.strip())

    def set_cost(self, total_cost: float) -> None:
        """Spend is reported once, in the summary."""
