"""The phase checklist: done rows, running rows, what is next, and the one factory."""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.progress import TextColumn

from repowise.cli.ui import checklist
from repowise.cli.ui.glyphs import glyph
from repowise.cli.ui.progress import RichProgressCallback, print_stage, upcoming_stages
from repowise.cli.ui.progress_display import ChecklistProgress, make_progress


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _console(*, terminal: bool) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=terminal, color_system=None)
    return console, buf


def _render(progress: ChecklistProgress, width: int = 160) -> str:
    console = Console(file=io.StringIO(), width=width, color_system=None)
    with console.capture() as cap:
        console.print(progress.get_renderable())
    return cap.get()


# --- renderer ----------------------------------------------------------------


def test_a_substep_label_keeps_its_depth_and_drops_the_ellipsis() -> None:
    assert checklist.split_label("  ↳ Resolving imports") == ("Resolving imports", 1)
    assert checklist.split_label("Parsing files…") == ("Parsing files", 0)


def test_every_count_carries_its_unit() -> None:
    assert checklist.count_text(1204, 2938, "files") == "1,204/2,938 files"
    assert checklist.count_text(50, None, "files") == "50 files"
    assert checklist.count_text(3, 9, "") == ""


@pytest.mark.parametrize(
    ("completed", "total", "elapsed"),
    [
        (5, None, 30.0),  # no total: nothing to measure against
        (0, 100, 30.0),  # nothing finished yet: no rate
        (5, 100, 1.0),  # too early: the rate is mostly startup
        (2, 3, 30.0),  # three coarse items have no rate
        (3, 100, 30.0),  # too few finished
        (100, 100, 30.0),  # finished
    ],
)
def test_no_time_left_without_an_honest_estimate(completed, total, elapsed) -> None:
    assert checklist.time_left(completed, total, elapsed) is None


def test_time_left_follows_the_measured_rate() -> None:
    assert checklist.time_left(25, 100, 10.0) == pytest.approx(30.0)
    assert checklist.format_time_left(30.0) == "about 30s left"
    assert checklist.format_time_left(200.0) == "about 3m left"


def test_running_time_is_whole_seconds() -> None:
    assert checklist.format_running(12.7) == "12s"
    assert checklist.format_running(65.2) == "1m 05s"
    assert checklist.format_running(None) == "0s"


def test_a_done_row_names_the_step_its_count_and_its_time() -> None:
    line = checklist.done_line(
        checklist.FinishedStep("Parsing files…", completed=30, total=30, unit="files", elapsed=0.42)
    )
    plain = Console().render_str(line).plain
    assert plain == "  ✓ Parsing files · 30 files · 0.4s"


def test_a_substep_done_row_is_indented_and_a_unitless_step_shows_only_its_time() -> None:
    line = checklist.done_line(checklist.FinishedStep("  ↳ Detecting communities", elapsed=1.5))
    assert Console().render_str(line).plain == "    ✓ Detecting communities · 1.5s"


def test_pending_and_done_states_carry_a_glyph_not_only_a_colour() -> None:
    assert Console().render_str(checklist.upcoming_line(["Analysis", "Persistence"])).plain == (
        "  ○ Next: Analysis · Persistence"
    )
    assert glyph(checklist.STATE_MARKS[checklist.DONE][0]) == "✓"


def test_no_em_dashes_in_checklist_copy() -> None:
    texts = [
        checklist.long_step_line("repowise init --resume"),
        checklist.upcoming_line(["Analysis"]),
        checklist.done_line(checklist.FinishedStep("Parsing files…", 1, 1, "files", 1.0)),
    ]
    assert not any("—" in t for t in texts)


# --- factory -----------------------------------------------------------------


def test_the_factory_adds_the_cost_column_only_when_asked_and_keeps_its_placeholder() -> None:
    """Lesson #1571: the format placeholder must reach Rich literally."""
    console, _ = _console(terminal=False)
    plain = make_progress(console)
    priced = make_progress(console, cost=True)
    formats = [c.text_format for c in priced.columns if isinstance(c, TextColumn)]
    assert any("${task.fields[cost]:.3f}" in f for f in formats)
    assert len(priced.columns) == len(plain.columns) + 1


def test_a_running_row_shows_count_unit_time_left_and_the_current_item() -> None:
    console, _ = _console(terminal=False)
    progress = make_progress(console)
    clock = _Clock()
    progress.get_time = clock
    task = progress.add_task("Parsing files…", total=100, unit="files", detail="", cost=0.0)
    progress.start_task(task)
    clock.now += 10.0
    progress.update(task, completed=25, detail="src/app/routing.py")

    out = _render(progress)
    assert "25/100 files" in out
    assert "10s" in out
    assert "about 30s left" in out
    assert "src/app/routing.py" in out


def test_the_long_step_line_needs_a_resume_command_and_ten_seconds() -> None:
    console, _ = _console(terminal=False)
    for resume, wait, shown in (
        ("repowise init --resume", 11.0, True),
        ("repowise init --resume", 5.0, False),
        (None, 60.0, False),
    ):
        progress = make_progress(console, resume_command=resume)
        clock = _Clock()
        progress.get_time = clock
        progress.add_task("Parsing files…", total=None, unit="files", detail="", cost=0.0)
        clock.now += wait
        assert ("Safe to Ctrl-C" in _render(progress)) is shown


def test_the_final_frame_is_empty() -> None:
    """A step cut short by Ctrl-C must not stay on screen with a time left."""
    console, _ = _console(terminal=False)
    progress = make_progress(console, resume_command="repowise init --resume")
    progress.upcoming = ["Analysis"]
    progress.add_task("Scoring code health…", total=100, unit="checks", detail="", cost=0.0)
    assert "Next: Analysis" in _render(progress)
    progress.start()
    progress.stop()
    assert _render(progress).strip() == ""


# --- callback ----------------------------------------------------------------


def test_a_finished_step_leaves_one_done_row_and_its_live_row_goes() -> None:
    console, buf = _console(terminal=False)
    with make_progress(console) as bar:
        callback = RichProgressCallback(bar, console)
        callback.on_phase_start("parse", 2)
        callback.on_item_done("parse")
        callback.on_item_done("parse")
        callback.on_phase_done("parse")
        callback.on_phase_done("parse")
        assert not [t for t in bar.tasks if t.visible]

    out = buf.getvalue()
    assert out.count("✓ Parsing files · 2 files") == 1


def test_a_piped_run_stays_line_based() -> None:
    """No live frames, no pending hints: only the lines a log can keep."""
    console, buf = _console(terminal=False)
    with make_progress(console, resume_command="repowise init --resume") as bar:
        callback = RichProgressCallback(bar, console, total_phases=4, stage="ingestion")
        callback.on_phase_start("traverse", None)
        callback.on_item_label("traverse", "a.py")
        callback.on_phase_done("traverse")
        callback.on_phase_start("git", 5)

    out = buf.getvalue()
    assert "✓ Scanning & filtering files" in out
    assert "Next:" not in out
    assert "Safe to Ctrl-C" not in out
    assert "\x1b[" not in out


def test_a_step_that_raises_gets_no_done_row() -> None:
    console, buf = _console(terminal=False)
    with make_progress(console) as bar:
        callback = RichProgressCallback(bar, console)
        with pytest.raises(RuntimeError), callback.step("state"):
            raise RuntimeError("disk full")
        with callback.step("views"):
            pass

    out = buf.getvalue()
    assert "Writing index state" not in out
    assert "✓ Refreshing agent views" in out


def test_the_current_item_lands_on_the_running_row() -> None:
    console, _ = _console(terminal=False)
    bar = make_progress(console)
    callback = RichProgressCallback(bar, console)
    callback.on_phase_start("parse", 3)
    callback.on_item_label("parse", "pkg/mod.py")
    callback.on_item_label("unknown", "ignored.py")
    assert bar.tasks[0].fields["detail"] == "pkg/mod.py"


def test_each_stage_shows_the_stages_after_it() -> None:
    assert upcoming_stages("ingestion", 4) == ["Analysis", "Generation", "Persistence"]
    assert upcoming_stages("analysis", 3) == ["Persistence"]
    assert upcoming_stages("persistence", 4) == []

    console, _ = _console(terminal=False)
    bar = make_progress(console)
    RichProgressCallback(bar, console, total_phases=4, stage="generation")
    assert bar.upcoming == ["Persistence"]


def test_fast_mode_numbers_persistence_as_phase_three() -> None:
    console, buf = _console(terminal=False)
    print_stage(console, "persistence", 3)
    assert "Phase 3 of 3" in buf.getvalue()
    assert "Persistence" in buf.getvalue()


def test_a_failed_step_is_hidden_without_a_done_row() -> None:
    from repowise.core.pipeline.progress import emit_phase_failed

    console, buf = _console(terminal=False)
    with make_progress(console) as bar:
        callback = RichProgressCallback(bar, console)
        callback.on_phase_start("checkpoint.index", None)
        emit_phase_failed(callback, "checkpoint.index")
        assert not [t for t in bar.tasks if t.visible]
    assert "Saving a resume checkpoint" not in buf.getvalue()
    emit_phase_failed(None, "checkpoint.index")  # headless: a no-op


def test_concurrent_graph_steps_fold_into_one_row_and_name_what_still_runs() -> None:
    """Graph metrics run several kernels at once: no per-kernel lines, one done row."""
    import asyncio
    import threading

    from repowise.core.pipeline.phases.ingestion import _run_concurrent_steps

    console, buf = _console(terminal=False)
    release = threading.Event()
    seen: list[str] = []
    with make_progress(console) as bar:
        callback = RichProgressCallback(bar, console)

        def fast() -> None:
            pass

        def slow() -> None:
            release.wait(5)

        async def run() -> None:
            job = asyncio.ensure_future(
                _run_concurrent_steps(
                    "graph.metrics", [("PageRank", fast), ("symbol betweenness", slow)], callback
                )
            )
            for _ in range(200):
                await asyncio.sleep(0.01)
                if bar.tasks and bar.tasks[0].completed == 1:
                    break
            seen.append(str(bar.tasks[0].fields["detail"]))
            release.set()
            await job

        asyncio.run(run())

    assert seen == ["symbol betweenness"]
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("    ✓ Computing graph metrics (PageRank, betweenness) · 2 algorithms · ")


def test_generation_done_row_counts_pages_made_including_its_paid_tier() -> None:
    """Announced totals are upper bounds (a page may gate-skip): count what was made."""
    console, buf = _console(terminal=False)
    with make_progress(console) as bar:
        callback = RichProgressCallback(bar, console)
        callback.on_phase_start("generation", 130)
        callback.on_phase_start("generation.llm", 7)
        callback.on_phase_start("onboarding", 6)
        for phase, n in (("generation", 126), ("generation.llm", 6), ("onboarding", 4)):
            for _ in range(n):
                callback.on_item_done(phase)
        for phase in ("onboarding", "generation.llm", "generation"):
            callback.on_phase_done(phase)

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert lines[0].startswith("    ✓ Curating onboarding docs · 4 pages")
    assert lines[1].startswith("    ✓ Writing overview and subsystem pages · 6 pages")
    assert lines[2].startswith("  ✓ Generating pages · 132 pages")
