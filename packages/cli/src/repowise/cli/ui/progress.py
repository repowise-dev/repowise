"""The core ProgressCallback adapter that drives the live progress display."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

from rich.console import Console

from repowise.cli.ui import checklist
from repowise.cli.ui.brand import ERR, WARN, print_phase_header
from repowise.cli.ui.glyphs import GLYPHS, for_terminal
from repowise.core.redaction import redact_secrets

# Message levels that mean "this run degraded". ``notice`` is deliberately not
# here: it is visible (warning-styled) but informational, e.g. a file skipped on
# size or a "this may take a while" tip.
_DEGRADING_LEVELS = frozenset({"warning", "error"})

# Top-level stage -> (title, subtitle), in run order. The pipeline announces
# ingestion and analysis itself; the CLI opens generation and persistence.
# Numbering is the screen's business, and the screen is the only side that
# knows which stages follow.
#
# The keys are spelled out rather than imported from
# ``repowise.core.pipeline.progress``: that module's package eagerly imports the
# orchestrator, which every other CLI call site is careful to defer into a
# function body, and paying ~170ms of pipeline import on ``repowise --help`` or
# on each post-commit hook run to read two string constants is a bad trade.
# ``test_init_ux`` pins these against the core constants so they cannot drift.
_STAGES: dict[str, tuple[str, str]] = {
    "ingestion": (
        "Ingestion",
        "Walking the tree, parsing files, building the dependency graph",
    ),
    "analysis": ("Analysis", "Dead code, code health, architectural decisions"),
    "generation": ("Generation", ""),
    "persistence": ("Persistence", "Saving to database and building search index"),
}


def stage_order(total_phases: int) -> list[str]:
    """The stages a run of ``total_phases`` goes through; fast mode skips generation."""
    stages = list(_STAGES)
    return stages if total_phases >= len(stages) else [s for s in stages if s != "generation"]


def _order_with(stage: str, total_phases: int) -> list[str]:
    # A caller that opens a stage its count leaves out still gets a header.
    order = stage_order(total_phases)
    return order if stage in order else list(_STAGES)


def print_stage(console: Console, stage: str, total_phases: int, subtitle: str = "") -> None:
    """The numbered rule that opens ``stage``, e.g. ``Phase 4 of 4 · Persistence``."""
    title, default_subtitle = _STAGES[stage]
    num = min(_order_with(stage, total_phases).index(stage) + 1, total_phases)
    print_phase_header(console, num, total_phases, title, subtitle or default_subtitle)


def upcoming_stages(stage: str, total_phases: int) -> list[str]:
    """Titles of the stages after ``stage``."""
    order = _order_with(stage, total_phases)
    return [_STAGES[s][0] for s in order[order.index(stage) + 1 :]]


# Every phase the pipeline emits needs an entry here: the fallback prints the
# raw internal id (``knowledge_graph.skeleton...``) beside proper sentences.
# ``…`` throughout, matching the CLI-authored status lines these interleave with.
# A leading ``↳`` marks a sub-step of the phase above it.
_PHASE_LABELS: dict[str, str] = {
    "traverse": "Scanning & filtering files…",
    "parse": "Parsing files…",
    "tsconfig": "Indexing tsconfig path aliases…",
    "graph": "Building dependency graph…",
    "graph.imports": "  ↳ Resolving imports",
    "graph.heritage": "  ↳ Resolving inheritance",
    "graph.calls": "  ↳ Resolving call edges",
    "graph.type_refs": "  ↳ Resolving type references",
    "graph.member_reads": "  ↳ Resolving member reads",
    "graph.same_package": "  ↳ Linking same-package files",
    "graph.same_namespace": "  ↳ Linking same-namespace files",
    "graph.same_namespace_php": "  ↳ Linking same-namespace PHP files",
    "graph.spec_mirrors": "  ↳ Linking specs to their sources",
    "graph.header_pairs": "  ↳ Pairing headers with sources",
    "graph.partials": "  ↳ Linking partial classes",
    "graph.same_module": "  ↳ Linking same-module files",
    "graph.compile_order": "  ↳ Reading compile order",
    "graph.go_interfaces": "  ↳ Resolving Go interfaces",
    "graph.dispatch": "  ↳ Resolving dynamic dispatch",
    "graph.ts_index": "  ↳ Indexing TypeScript and JavaScript modules",
    "graph.jvm_index": "  ↳ Indexing Java and Kotlin packages",
    "graph.dotnet_index": "  ↳ Indexing .NET projects",
    "graph.go_index": "  ↳ Indexing Go packages",
    "graph.rust_targets": "  ↳ Reading Rust crate targets",
    "graph.cpp_index": "  ↳ Indexing C and C++ includes",
    "graph.swift_entry": "  ↳ Finding Swift entry points",
    "graph.composer_files": "  ↳ Reading Composer autoload rules",
    "graph.dart_shells": "  ↳ Indexing Dart packages",
    "graph.godot_project": "  ↳ Reading the Godot project",
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
    "doc_drift": "Checking documentation drift…",
    "checkpoint.index": "Saving a resume checkpoint…",
    "checkpoint.analysis": "Saving a resume checkpoint…",
    "knowledge_graph.skeleton": "Building the knowledge graph…",
    "knowledge_graph.enrich": "  ↳ Naming layers and building the tour",
    "generation": "Generating pages…",
    "generation.llm": "  ↳ Writing overview and subsystem pages",
    "onboarding": "  ↳ Curating onboarding docs",
    "embedding": "Embedding pages for semantic search…",
    "vector_store": "Opening the search index…",
    "persist": "Saving to the database…",
    "savings": "Recovering token savings from agent history…",
    "state": "Writing index state…",
    "views": "Refreshing agent views…",
    "editor_files": "Writing editor files…",
}

# What a phase counts, so every number on screen has a unit. A phase missing
# here shows its count bare while running and no count once done.
_PHASE_UNITS: dict[str, str] = {
    "traverse": "files",
    "parse": "files",
    "graph.imports": "files",
    "graph.heritage": "files",
    "graph.calls": "files",
    "graph.type_refs": "files",
    "graph.metrics": "algorithms",
    "graph.communities": "algorithms",
    "git": "files",
    "co_change": "commits",
    "dead_code": "checks",
    "health": "checks",
    "doc_drift": "checks",
    "decisions": "sources",
    "generation": "pages",
    "generation.llm": "pages",
    "onboarding": "pages",
    "embedding": "pages",
}

# A step whose work is split across live rows: its done row counts its parts
# too, so the step reports one total. Generation draws free and paid pages as
# separate bars (one bar of mostly free pages reads as frozen on the paid ones).
_PHASE_PARTS: dict[str, tuple[str, ...]] = {"generation": ("generation.llm",)}

# Steps whose announced total is an upper bound (a page may gate-skip), so the
# done row counts the items actually finished rather than the total.
_ESTIMATED_TOTALS = frozenset({"generation", "generation.llm", "onboarding"})


class RichProgressCallback:
    """Adapter that implements ``repowise.core.pipeline.ProgressCallback``
    on the live display from :func:`repowise.cli.ui.progress_display.make_progress`.

    A running step is a live row; when it finishes, its done row is printed
    above the display, so the steps read top to bottom as a checklist.

    Usage::

        with make_progress(console) as progress_bar:
            callback = RichProgressCallback(progress_bar, console)
            result = run_async(run_pipeline(..., progress=callback))
    """

    def __init__(
        self,
        progress: Any,
        console: Console,
        *,
        total_phases: int | None = None,
        stage: str | None = None,
    ) -> None:
        self._progress = progress
        self._console = console
        self._tasks: dict[str, Any] = {}
        self._done: set[str] = set()
        # Items each finished step reported in its done row.
        self._done_counts: dict[str, float] = {}
        # Every warning this callback rendered, in order. Printing is enough
        # for a human watching, but agent-driven mode is the primary path and
        # nobody is watching there: a run that degraded and a run that did not
        # both exit 0 and both write a state.json that looks identical.
        # ``init`` persists this list so the degradation is recoverable after
        # the terminal output is gone. ``update`` already reports a ``degraded``
        # list for the same reason; this is the missing half on the init side.
        #
        # Ceiling: this instance covers the index+analysis run. The generation
        # phase builds its own callback, so its warnings are not collected here.
        self.warnings: list[str] = []
        # Set only by the single-repo init flow, which is the one screen that
        # numbers its phases. The workspace flow prints its own per-repo header
        # and would otherwise draw a "Phase 1 of 4" rule for every repo.
        self._total_phases = total_phases
        if stage is not None:
            self._show_upcoming(stage)

    def _show_upcoming(self, stage: str) -> None:
        if self._total_phases is None or stage not in _STAGES:
            return
        if hasattr(self._progress, "upcoming"):
            self._progress.upcoming = upcoming_stages(stage, self._total_phases)

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
        if total is None or stage not in _STAGES:
            return
        self._show_upcoming(stage)
        self._print_above_live(lambda: print_stage(self._progress.console, stage, total))

    def on_phase_start(self, phase: str, total: int | None) -> None:
        label = checklist.running_label(_PHASE_LABELS.get(phase, f"{phase}…"))
        self._done.discard(phase)
        self._done_counts.pop(phase, None)
        # If phase already has a task, update its total and make visible
        if phase in self._tasks:
            self._progress.update(self._tasks[phase], total=total, visible=True)
        else:
            self._tasks[phase] = self._progress.add_task(
                label,
                total=total,
                visible=True,
                cost=0.0,
                unit=_PHASE_UNITS.get(phase, ""),
                detail="",
            )

    def on_item_done(self, phase: str) -> None:
        if phase in self._tasks:
            self._progress.advance(self._tasks[phase])

    def on_item_label(self, phase: str, label: str) -> None:
        """Show the item ``phase`` is on, e.g. the file being parsed."""
        task_id = self._tasks.get(phase)
        if task_id is not None:
            with contextlib.suppress(Exception):
                self._progress.update(task_id, detail=label)

    def on_phase_done(self, phase: str) -> None:
        """Hide the finished step's live row and print its done row above the display.

        Hiding keeps phase-summary lines that follow from interleaving with stale
        progress bars (issue: phantom/duplicated progress bars). A phase reported
        done twice prints once.
        """
        task_id = self._tasks.get(phase)
        if task_id is None or phase in self._done:
            return
        self._done.add(phase)
        try:
            task = next((t for t in self._progress.tasks if t.id == task_id), None)
            finished = task.completed if task is not None else 0
            if task is not None and task.total is not None:
                self._progress.update(task_id, completed=task.total, visible=False)
            else:
                self._progress.update(task_id, visible=False)
        except Exception:
            return
        if task is None:
            return
        count = finished if phase in _ESTIMATED_TOTALS else (task.total or finished)
        count += sum(self._done_counts.get(p, 0) for p in _PHASE_PARTS.get(phase, ()))
        self._done_counts[phase] = count
        line = checklist.done_line(
            checklist.FinishedStep(
                label=str(task.description),
                completed=count,
                total=None,
                unit=str(task.fields.get("unit", "")),
                elapsed=task.elapsed,
            )
        )
        self._print_above_live(lambda: self._progress.console.print(line, highlight=False))

    def on_phase_failed(self, phase: str) -> None:
        """Hide a step that did not finish, with no done row: its warning says why."""
        task_id = self._tasks.get(phase)
        if task_id is not None:
            with contextlib.suppress(Exception):
                self._progress.update(task_id, visible=False)

    @contextlib.contextmanager
    def step(self, phase: str) -> Iterator[None]:
        """Run a block as one labelled step. A step that raises gets no done row."""
        self.on_phase_start(phase, None)
        try:
            yield
        except BaseException:
            self.on_phase_failed(phase)
            raise
        self.on_phase_done(phase)

    def on_message(self, level: str, text: str) -> None:
        # ``info`` is deliberately unstyled. It carries neutral facts ("Scanned
        # 12,431 files", "Languages: …"), and rendering those in the same green
        # as a done row made green mean "the pipeline said something" rather
        # than "this succeeded".
        #
        # ``notice`` is rendered with the warning style so it stays visible, but
        # it is not a degradation: size-skip lines and progress tips are
        # informational. Only ``warning`` and ``error`` are collected into
        # ``self.warnings``, which ``init`` persists as ``state["degraded"]`` and
        # the MCP server then reports on every reply.
        style_map = {"warning": WARN, "error": ERR, "notice": WARN}
        style = style_map.get(level, "")
        if level in _DEGRADING_LEVELS:
            self.warnings.append(redact_secrets(text))
        text = for_terminal(text)
        # Insight lines (indented with →, in either form) get special formatting
        if text.lstrip().startswith(GLYPHS["arrow"]):
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


@contextlib.contextmanager
def progress_steps(
    console: Console,
    *,
    cost: bool = False,
    transient: bool = False,
    resume_command: str | None = None,
    total_phases: int | None = None,
    stage: str | None = None,
) -> Iterator[Any]:
    """The step callback a pipeline command drives: the live display, or the
    ``--progress json`` event stream when one is open. Arguments as for
    :func:`~repowise.cli.ui.progress_display.make_progress` and
    :class:`RichProgressCallback`; the stream ignores the display-only ones.
    """
    from repowise.cli.agent_events import active

    events = active()
    if events is not None:
        from repowise.cli.ui.progress_json import JsonProgressCallback

        yield JsonProgressCallback(events, stage=stage)
        return

    from repowise.cli.ui.progress_display import make_progress

    with make_progress(
        console, cost=cost, transient=transient, resume_command=resume_command
    ) as bar:
        yield RichProgressCallback(bar, console, total_phases=total_phases, stage=stage)
