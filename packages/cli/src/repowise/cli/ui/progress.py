"""Rich progress column + the core ProgressCallback adapter."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.progress import ProgressColumn, Task
from rich.text import Text

from repowise.cli.ui.brand import ERR, OK, WARN


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

_PHASE_LABELS: dict[str, str] = {
    "traverse": "Scanning & filtering files...",
    "parse": "Parsing files...",
    "tsconfig": "Indexing tsconfig path aliases...",
    "graph": "Building dependency graph...",
    "graph.imports": "  ↳ Resolving imports",
    "graph.heritage": "  ↳ Resolving inheritance",
    "graph.calls": "  ↳ Resolving call edges",
    "dynamic_hints": "  ↳ Wiring dynamic hints",
    "graph.metrics": "  ↳ Computing graph metrics (PageRank, betweenness)",
    "graph.communities": "  ↳ Detecting communities",
    "graph.flows": "  ↳ Tracing execution flows",
    "external_systems": "Parsing external dependency manifests...",
    "git": "Indexing file history...",
    "co_change": "Analyzing co-changes...",
    "dead_code": "Detecting dead code...",
    "decisions": "Extracting decisions...",
    "generation": "Generating pages...",
    "onboarding": "Curating onboarding docs...",
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

    def __init__(self, progress: Any, console: Console) -> None:
        self._progress = progress
        self._console = console
        self._tasks: dict[str, Any] = {}

    def on_phase_start(self, phase: str, total: int | None) -> None:
        label = _PHASE_LABELS.get(phase, f"{phase}...")
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
        style_map = {"info": OK, "warning": WARN, "error": ERR}
        style = style_map.get(level, "")
        # Insight lines (indented with →) get special formatting
        if text.lstrip().startswith("→"):
            line = f"  [dim]{text}[/dim]"
        elif style:
            line = f"  [{style}]{text}[/{style}]"
        else:
            line = f"  {text}"

        # Print under the Live lock so the line lands cleanly above the
        # progress region instead of interleaving with still-rendering
        # spinners (issue: phase summary lines interleaved with bars).
        live = getattr(self._progress, "live", None)
        if live is not None:
            try:
                with live._lock:
                    self._progress.console.print(line)
                self._progress.refresh()
                return
            except Exception:
                pass
        self._progress.console.print(line)

    def set_cost(self, total_cost: float) -> None:
        """Update the live cost display on all active progress tasks."""
        for task_id in self._tasks.values():
            try:
                self._progress.update(task_id, cost=total_cost)
            except Exception:
                pass
