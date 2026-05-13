"""Phase-timing recorder for the indexing pipeline.

A ``ProgressCallback`` decorator that observes ``on_phase_start`` /
``on_phase_done`` events and records the wall-clock duration of each
phase. Designed to compose with the real (Rich / Logging) callback so
the timing data is collected without touching pipeline internals.

The CLI writes the resulting ``timings`` dict into ``state.json`` so
before/after perf comparisons across runs become trivial:

.. code-block:: text

    state.json
    {
      "last_sync_commit": "...",
      "phase_timings": {
        "traverse": 4.21,
        "parse": 88.3,
        "graph.imports": 312.5,
        ...
      }
    }
"""

from __future__ import annotations

import time
from typing import Any


class PhaseTimingRecorder:
    """Observes pipeline phase events and records wall-clock durations.

    Wraps another ``ProgressCallback`` (or ``None``) and transparently
    delegates every call. Concurrent / nested phases are supported —
    each phase name is timed independently from its own
    ``on_phase_start`` to its matching ``on_phase_done``.

    Repeated phases (a phase whose ``on_phase_start`` fires more than
    once in a single run) accumulate; the total is the sum of every
    visit. This matches how the user perceives the cost — "how much
    wall-clock time did this phase consume".
    """

    def __init__(self, inner: Any | None = None) -> None:
        self._inner = inner
        self._starts: dict[str, float] = {}
        self._totals: dict[str, float] = {}

    @property
    def timings(self) -> dict[str, float]:
        """Mapping of phase name → accumulated seconds (rounded to 0.01s)."""
        return {name: round(secs, 2) for name, secs in self._totals.items()}

    # ---- ProgressCallback protocol ----------------------------------

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self._starts[phase] = time.monotonic()
        if self._inner is not None:
            self._inner.on_phase_start(phase, total)

    def on_item_done(self, phase: str) -> None:
        if self._inner is not None:
            self._inner.on_item_done(phase)

    def on_phase_done(self, phase: str) -> None:
        started = self._starts.pop(phase, None)
        if started is not None:
            self._totals[phase] = self._totals.get(phase, 0.0) + (time.monotonic() - started)
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
