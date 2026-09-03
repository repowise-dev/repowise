"""Unit tests for ``PhaseTimingRecorder``."""

from __future__ import annotations

import contextlib
import time

from repowise.core.pipeline import PhaseTimingRecorder, PhaseTimings, timed


class _CollectingCallback:
    """Inner callback that records the events it received."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, int | None]] = []
        self.messages: list[tuple[str, str]] = []

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self.events.append(("start", phase, total))

    def on_item_done(self, phase: str) -> None:
        self.events.append(("item", phase, None))

    def on_phase_done(self, phase: str) -> None:
        self.events.append(("done", phase, None))

    def on_message(self, level: str, text: str) -> None:
        self.messages.append((level, text))


def test_records_phase_duration() -> None:
    inner = _CollectingCallback()
    rec = PhaseTimingRecorder(inner)

    rec.on_phase_start("parse", 10)
    time.sleep(0.02)
    rec.on_phase_done("parse")

    assert "parse" in rec.timings
    assert rec.timings["parse"] >= 0.01


def test_repeated_phases_accumulate() -> None:
    rec = PhaseTimingRecorder()

    rec.on_phase_start("graph.metrics", None)
    time.sleep(0.01)
    rec.on_phase_done("graph.metrics")

    rec.on_phase_start("graph.metrics", None)
    time.sleep(0.01)
    rec.on_phase_done("graph.metrics")

    # Both visits sum into a single accumulated bucket.
    assert rec.timings["graph.metrics"] >= 0.02


def test_delegates_to_inner_callback() -> None:
    inner = _CollectingCallback()
    rec = PhaseTimingRecorder(inner)

    rec.on_phase_start("traverse", 5)
    rec.on_item_done("traverse")
    rec.on_phase_done("traverse")
    rec.on_message("info", "hello")

    assert ("start", "traverse", 5) in inner.events
    assert ("item", "traverse", None) in inner.events
    assert ("done", "traverse", None) in inner.events
    assert ("info", "hello") in inner.messages


def test_none_inner_is_safe() -> None:
    """The recorder must work standalone (no inner callback) — e.g. when
    a test or headless worker just wants timing data."""
    rec = PhaseTimingRecorder()
    rec.on_phase_start("p", None)
    rec.on_item_done("p")
    rec.on_message("info", "x")
    rec.on_phase_done("p")
    assert "p" in rec.timings


def test_unstarted_phase_is_ignored() -> None:
    """An ``on_phase_done`` with no matching start should be a no-op."""
    rec = PhaseTimingRecorder()
    rec.on_phase_done("never-started")
    assert rec.timings == {}


def test_rebound_recorder_shares_one_table() -> None:
    """Generation and persistence build their own callbacks mid-run."""
    recorder = PhaseTimingRecorder(_CollectingCallback())
    recorder.on_phase_start("ingest", None)
    recorder.on_phase_done("ingest")

    later = recorder.rebind(_CollectingCallback())
    later.on_phase_start("persist", None)
    later.on_phase_done("persist")

    assert set(recorder.timings) == {"ingest", "persist"}
    assert recorder.timings == later.timings


def test_rebound_recorder_forwards_to_its_own_inner() -> None:
    first, second = _CollectingCallback(), _CollectingCallback()
    recorder = PhaseTimingRecorder(first)
    later = recorder.rebind(second)

    later.on_phase_start("persist", 3)
    later.on_item_done("persist")
    later.on_phase_done("persist")

    assert second.events == [("start", "persist", 3), ("item", "persist", None), ("done", "persist", None)]
    assert first.events == []


def test_reannounced_phase_keeps_the_first_start() -> None:
    """A phase is announced again once its real item total is known."""
    recorder = PhaseTimingRecorder()
    recorder.on_phase_start("persist", None)
    time.sleep(0.05)
    recorder.on_phase_start("persist", 42)
    recorder.on_phase_done("persist")

    assert recorder.timings["persist"] >= 0.05


def test_timed_records_a_block_and_tolerates_no_table() -> None:
    timings = PhaseTimings()
    with timed(timings, "kg"):
        time.sleep(0.02)
    with timed(None, "kg"):
        pass

    assert timings.totals["kg"] >= 0.02


def test_timed_records_even_when_the_block_raises() -> None:
    timings = PhaseTimings()
    with contextlib.suppress(RuntimeError), timed(timings, "kg"):
        raise RuntimeError("boom")

    assert "kg" in timings.totals
