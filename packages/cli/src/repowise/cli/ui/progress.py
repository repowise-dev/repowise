"""Rich progress column + the core ProgressCallback adapter."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.progress import ProgressColumn, Task
from rich.text import Text

from repowise.cli.ui.brand import ERR, WARN, print_phase_header

# Top-level stage → (phase number, title, subtitle). The pipeline announces the
# stage; how it is numbered and worded is the screen's business, and the screen
# is the only side that knows generation and persistence follow.
#
# The keys are spelled out rather than imported from
# ``repowise.core.pipeline.progress``: that module's package eagerly imports the
# orchestrator, which every other CLI call site is careful to defer into a
# function body, and paying ~170ms of pipeline import on ``repowise --help`` or
# on each post-commit hook run to read two string constants is a bad trade.
# ``test_init_ux`` pins these against the core constants so they cannot drift.
_STAGE_HEADERS: dict[str, tuple[int, str, str]] = {
    "ingestion": (
        1,
        "Ingestion",
        "Walking the tree, parsing files, building the dependency graph",
    ),
    "analysis": (2, "Analysis", "Dead code, code health, architectural decisions"),
}


class MaybeCountColumn(ProgressColumn):
    """Progress column that shows ``completed/total`` when total is known,
    or just ``completed`` when total is ``None`` (indeterminate phase).

    This prevents the ugly ``1214/None`` display that appears for phases
    like file traversal and dead-code detection whose total is not known
    upfront.
    """

    def render(self, task: Task) -> Text:
        if task.total is None or task.total == 0:
            return Text(str(int(task.completed)), style="progress.download")
        return Text(
            f"{int(task.completed)}/{int(task.total)}",
            style="progress.download",
        )


# ---------------------------------------------------------------------------
# Rich progress callback — implements core ProgressCallback protocol
# ---------------------------------------------------------------------------

# Every phase the pipeline emits needs an entry here: the fallback prints the
# raw internal id (``knowledge_graph.skeleton...``) beside proper sentences.
# ``…`` throughout, matching the CLI-authored status lines these interleave with.
_PHASE_LABELS: dict[str, str] = {
    "traverse": "Scanning & filtering files…",
    "parse": "Parsing files…",
    "tsconfig": "Indexing tsconfig path aliases…",
    "graph": "Building dependency graph…",
    "graph.imports": "  ↳ Resolving imports",
    "graph.heritage": "  ↳ Resolving inheritance",
    "graph.calls": "  ↳ Resolving call edges",
    "graph.type_refs": "  ↳ Resolving type references",
    "dynamic_hints": "  ↳ Wiring dynamic hints",
    "graph.metrics": "  ↳ Computing graph metrics (PageRank, betweenness)",
    "graph.communities": "  ↳ Detecting communities",
    "graph.flows": "  ↳ Tracing execution flows",
    "external_systems": "Parsing external dependency manifests…",
    "git": "Indexing file history…",
    "co_change": "Analyzing co-changes…",
    "dead_code": "Detecting dead code…",
    "health": "Scoring code health…",
    "decisions": "Extracting decisions…",
    "knowledge_graph.skeleton": "Building the knowledge graph…",
    "knowledge_graph.enrich": "  ↳ Naming layers and building the tour",
    "generation": "Generating pages…",
    "onboarding": "Curating onboarding docs…",
}


class RichProgressCallback:
    """Adapter that implements ``repowise.core.pipeline.ProgressCallback``
    using a Rich ``Progress`` instance for terminal display.

    Usage::

        from rich.progress import Progress
        with Progress(...) as progress_bar:
            callback = RichProgressCallback(progress_bar, console)
            result = run_async(run_pipeline(..., progress=callback))
    """

    def __init__(self, progress: Any, console: Console, *, total_phases: int | None = None) -> None:
        self._progress = progress
        self._console = console
        self._tasks: dict[str, Any] = {}
        # Set only by the single-repo init flow, which is the one screen that
        # numbers its phases. The workspace flow prints its own per-repo header
        # and would otherwise draw a "Phase 1 of 4" rule for every repo.
        self._total_phases = total_phases

    def _print_above_live(self, emit: Callable[[], None]) -> None:
        """Run *emit* outside the Live region so its output lands cleanly
        above the progress bars instead of interleaving with still-rendering
        spinners (issue: phase summary lines interleaved with bars).
        """
        live = getattr(self._progress, "live", None)
        if live is not None:
            try:
                with live._lock:
                    emit()
                self._progress.refresh()
                return
            except Exception:
                pass
        emit()

    def on_stage(self, stage: str) -> None:
        """Render a top-level stage as the same phase rule the CLI uses.

        Phases 1 and 2 used to arrive as small green ``on_message`` lines while
        3 and 4 got full-width rules, so the first separator a first-time user
        ever saw read "Phase 3 of 4".
        """
        total = self._total_phases
        meta = _STAGE_HEADERS.get(stage)
        if total is None or meta is None:
            return
        num, title, subtitle = meta
        self._print_above_live(
            lambda: print_phase_header(self._progress.console, num, total, title, subtitle)
        )

    def on_phase_start(self, phase: str, total: int | None) -> None:
        label = _PHASE_LABELS.get(phase, f"{phase}…")
        # If phase already has a task, update its total and make visible
        if phase in self._tasks:
            self._progress.update(self._tasks[phase], total=total, visible=True)
        else:
            self._tasks[phase] = self._progress.add_task(label, total=total, visible=True, cost=0.0)

    def on_item_done(self, phase: str) -> None:
        if phase in self._tasks:
            self._progress.advance(self._tasks[phase])

    def on_phase_done(self, phase: str) -> None:
        """Mark a phase task as fully complete and hide it from the live
        display, so the phase-summary lines that follow aren't interleaved
        with stale progress bars (issue: phantom/duplicated progress bars).
        """
        task_id = self._tasks.get(phase)
        if task_id is None:
            return
        try:
            task = next((t for t in self._progress.tasks if t.id == task_id), None)
            if task is not None and task.total is not None:
                self._progress.update(task_id, completed=task.total, visible=False)
            else:
                self._progress.update(task_id, visible=False)
        except Exception:
            pass

    def on_message(self, level: str, text: str) -> None:
        # ``info`` is deliberately unstyled. It carries neutral facts ("Scanned
        # 12,431 files", "Languages: …"), and rendering those in the same green
        # as "✓ Database updated" made green mean "the pipeline said something"
        # rather than "this succeeded".
        style_map = {"warning": WARN, "error": ERR}
        style = style_map.get(level, "")
        # Insight lines (indented with →) get special formatting
        if text.lstrip().startswith("→"):
            line = f"  [dim]{text}[/dim]"
        elif style:
            line = f"  [{style}]{text}[/{style}]"
        else:
            line = f"  {text}"

        self._print_above_live(lambda: self._progress.console.print(line))

    def set_cost(self, total_cost: float) -> None:
        """Update the live cost display on all active progress tasks."""
        for task_id in self._tasks.values():
            with contextlib.suppress(Exception):
                self._progress.update(task_id, cost=total_cost)
