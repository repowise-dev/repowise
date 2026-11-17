"""The one live progress display every pipeline command draws.

:func:`make_progress` is the factory. The running rows are Rich tasks, one per
step, with a count and unit, the time the step has run, the time left when it
can be measured, and the item it is on. Under them sit the long-step line and
the stages still to come. Finished steps leave the live region: the callback
prints their done row above it (see :mod:`repowise.cli.ui.checklist`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rich.console import Console, RenderableType
from rich.progress import BarColumn, Progress, ProgressColumn, Task, TextColumn
from rich.text import Text

from repowise.cli.ui import checklist, mascot
from repowise.cli.ui.brand import DIM, OK
from repowise.cli.ui.glyphs import glyph

# The item a step is on is a path; keep its tail, which is the part that changes.
_DETAIL_WIDTH = 36


class OwlColumn(ProgressColumn):
    """The blinking mini owl at the head of a running row."""

    def render(self, task: Task) -> Text:
        return mascot.thinking_text(task.get_time())


class MaybeCountColumn(ProgressColumn):
    """``completed/total unit``, or ``completed unit`` when the total is unknown.

    Never ``1214/None``: traversal and dead-code detection start without one.
    A step with no unit shows ``completed/total`` when it has a total, else nothing.
    """

    def render(self, task: Task) -> Text:
        unit = task.fields.get("unit", "")
        total = task.total if task.total else None
        text = checklist.count_text(task.completed, total, unit)
        if not text and total:
            text = f"{int(task.completed):,}/{int(total):,}"
        return Text(text, style="progress.download")


class RunningTimeColumn(ProgressColumn):
    """How long the step has run, in whole seconds."""

    def render(self, task: Task) -> Text:
        return Text(checklist.format_running(task.elapsed), style="progress.elapsed")


class TimeLeftColumn(ProgressColumn):
    """Time left, only when the step has a total and a measured rate."""

    def render(self, task: Task) -> Text:
        left = checklist.time_left(task.completed, task.total, task.elapsed)
        if left is None or task.finished:
            return Text("")
        return Text(checklist.format_time_left(left), style=DIM)


class DetailColumn(ProgressColumn):
    """The item the step is on, e.g. the file being parsed."""

    def render(self, task: Task) -> Text:
        detail = str(task.fields.get("detail") or "")
        if len(detail) > _DETAIL_WIDTH:
            more = glyph("ellipsis")
            detail = more + detail[-(_DETAIL_WIDTH - len(more)) :]
        return Text(detail, style=DIM, overflow="ellipsis", no_wrap=True)


class ChecklistProgress(Progress):
    """A Rich ``Progress`` that also draws the long-step line and what comes next."""

    def __init__(self, *columns: Any, resume_command: str | None = None, **kwargs: Any) -> None:
        # Set before Progress.__init__, which renders once while building its Live.
        #: Stage titles still to run, set by whoever knows the run's stages.
        self.upcoming: list[str] = []
        # Only steps that really resume set this; the line promises it.
        self.resume_command = resume_command
        self._closing = False
        super().__init__(*columns, **kwargs)

    def get_renderables(self) -> Iterable[RenderableType]:
        # The final frame stays on screen and is the only frame a pipe gets.
        # Every finished step already left its done row, so it draws nothing:
        # a step cut short by Ctrl-C must not stay behind with a time left.
        if self._closing:
            return
        running = [t for t in self.tasks if t.visible and not t.finished]
        yield self.make_tasks_table(running)
        if self.resume_command and any(
            (t.elapsed or 0.0) >= checklist.LONG_STEP_SECONDS for t in running
        ):
            yield Text.from_markup(checklist.long_step_line(self.resume_command))
        if self.upcoming:
            yield Text.from_markup(checklist.upcoming_line(self.upcoming))

    def stop(self) -> None:
        self._closing = True
        super().stop()


def make_progress(
    console: Console,
    *,
    cost: bool = False,
    transient: bool = False,
    resume_command: str | None = None,
) -> ChecklistProgress:
    """Build the progress display. ``cost`` adds the running spend column.

    ``resume_command`` is the command that picks up an interrupted run; pass it
    only where Ctrl-C really is safe, since the long-step line says so.
    """
    columns: list[Any] = [
        OwlColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=24),
        MaybeCountColumn(),
        RunningTimeColumn(),
        TimeLeftColumn(),
        DetailColumn(),
    ]
    if cost:
        columns.append(TextColumn("[" + OK + "]${task.fields[cost]:.3f}[/]"))
    return ChecklistProgress(
        *columns,
        console=console,
        transient=transient,
        resume_command=resume_command,
    )
