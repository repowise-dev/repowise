"""Phase-timing recorder for the indexing pipeline.

A ``ProgressCallback`` decorator that observes ``on_phase_start`` /
``on_phase_done`` events and records the wall-clock duration of each
phase. Designed to compose with the real (Rich / Logging) callback so
the timing data is collected without touching pipeline internals.

Stages that build their own progress callback (generation, persistence)
share one :class:`PhaseTimings` table via :meth:`PhaseTimingRecorder.rebind`,
so a run reports one set of totals rather than one per progress bar.

The CLI writes the resulting ``timings`` dict into ``state.json`` so
before/after perf comparisons across runs become trivial:

.. code-block:: text

    state.json
    {
      "last_sync_commit": "...",
      "phase_timings": {
        "run": 170.54,
        "traverse": 4.21,
        "parse": 88.3,
        "graph.imports": 312.5,
        ...
      }
    }

Phases nest (``persist.fts`` inside ``persist``) and some overlap, so the
entries can sum to more than ``run``. Compare each against ``run``, never
against the sum.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class PhaseTimings:
    """Accumulated per-phase wall-clock totals for one run.

    Repeated phases accumulate; the total is the sum of every visit. This
    matches how the user perceives the cost - "how much wall-clock time did
    this phase consume". Phases may overlap, so the totals can sum to more
    than the run's wall time.
    """

    def __init__(self) -> None:
        self._starts: dict[str, float] = {}
        self._totals: dict[str, float] = {}

    def start(self, phase: str) -> None:
        """Open ``phase``. Re-announcing an open phase keeps the first start.

        Callers announce a phase before the work begins and again once a real
        item total is known; resetting on the second call would drop whatever
        ran in between.
        """
        self._starts.setdefault(phase, time.monotonic())

    def stop(self, phase: str) -> None:
        """Close ``phase``. A phase that never started is ignored."""
        started = self._starts.pop(phase, None)
        if started is not None:
            self._totals[phase] = self._totals.get(phase, 0.0) + (time.monotonic() - started)

    @property
    def totals(self) -> dict[str, float]:
        """Mapping of phase name -> accumulated seconds (rounded to 0.01s)."""
        return {name: round(secs, 2) for name, secs in self._totals.items()}


class PhaseTimingRecorder:
    """Observes pipeline phase events and records wall-clock durations.

    Wraps another ``ProgressCallback`` (or ``None``) and transparently
    delegates every call. Each phase name is timed independently from its
    own ``on_phase_start`` to its matching ``on_phase_done``.
    """

    def __init__(self, inner: Any | None = None, timings: PhaseTimings | None = None) -> None:
        self._inner = inner
        self._timings = timings if timings is not None else PhaseTimings()

    @property
    def timings(self) -> dict[str, float]:
        """Mapping of phase name -> accumulated seconds (rounded to 0.01s)."""
        return self._timings.totals

    @property
    def table(self) -> PhaseTimings:
        """The shared totals table, for handing to a later stage."""
        return self._timings

    def rebind(self, inner: Any | None) -> PhaseTimingRecorder:
        """A recorder forwarding to ``inner`` that writes the same totals.

        Generation and persistence each own a progress bar built after the
        pipeline callback is gone; rebinding keeps their phases in one table.
        """
        return PhaseTimingRecorder(inner, self._timings)

    # ---- ProgressCallback protocol ----------------------------------

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self._timings.start(phase)
        if self._inner is not None:
            self._inner.on_phase_start(phase, total)

    def on_item_done(self, phase: str) -> None:
        if self._inner is not None:
            self._inner.on_item_done(phase)

    def on_phase_done(self, phase: str) -> None:
        self._timings.stop(phase)
        if self._inner is not None:
            fn = getattr(self._inner, "on_phase_done", None)
            if callable(fn):
                fn(phase)

    def on_message(self, level: str, text: str) -> None:
        if self._inner is not None:
            self._inner.on_message(level, text)

    # Forward any other attribute the inner callback exposes (e.g.
    # ``set_cost`` on the Rich callback). Keeps the recorder a true
    # transparent wrapper without enumerating optional surface area.
    def __getattr__(self, name: str) -> Any:
        if self._inner is None:
            raise AttributeError(name)
        return getattr(self._inner, name)


@contextmanager
def timed(timings: PhaseTimings | None, name: str) -> Iterator[None]:
    """Time ``name`` into ``timings``, or do nothing when there is no table.

    For stages that have no progress callback for a recorder to observe.
    """
    if timings is None:
        yield
        return
    timings.start(name)
    try:
        yield
    finally:
        timings.stop(name)
