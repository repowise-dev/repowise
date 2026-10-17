"""Ingestion and analysis stage of ``repowise init`` (phases 1 and 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli.helpers import console, load_state, run_async
from repowise.cli.ui import (
    BRAND_STYLE,
    OK,
    OWL_SPINNER,
    WARN,
    MaybeCountColumn,
    RichProgressCallback,
)

from ._options import InitRun
from .persistence import build_resume_controller


def _index_vector_store(run: InitRun) -> Any:
    """The store generation would build anyway, hoisted for the analysis checkpoint.

    The checkpoint inside the pipeline dedups decisions against it, and
    ``run_repo_generation`` reuses this object. Never more than that store, so
    the exclusions are the runs that build none: a dry run, and index-only fast
    mode, which also pins no embedder for a table to be read back with.
    Keyless is dropped rather than handed to a matcher that refuses it.
    """
    opts = run.opts
    if opts.dry_run or (opts.index_only and opts.run_mode == "fast"):
        return None
    from repowise.cli.providers import build_embedder, build_vector_store, template_run_embedder
    from repowise.core.providers.embedding import store_has_semantic_vectors

    store_embedder = (
        template_run_embedder(run.embedder_name_resolved, run.embedder_was_requested)
        if opts.index_only or run.no_provider
        else run.embedder_name_resolved
    )
    candidate = build_vector_store(run.repo_path, build_embedder(store_embedder, run.repo_path))
    return candidate if store_has_semantic_vectors(candidate) else None


def _index_columns(with_cost: bool) -> list[Any]:
    columns: list[Any] = [
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
    ]
    # Only when a model can actually be called. On a --no-prose run the column
    # would sit at $0.000 for the whole multi-minute index, which reads as an
    # unanswered question rather than an answer.
    if with_cost:
        columns.append(TextColumn("[" + OK + "]${task.fields[cost]:.3f}[/]"))
    return columns


async def _index_with_resume(
    run: InitRun,
    *,
    run_pipeline: Any,
    llm_client: Any,
    vector_store: Any,
    mode: Any,
    callback: Any,
    existing_kg_fingerprint: str | None,
) -> Any:
    """Run the pipeline with a resume controller.

    The engine, session factory and repository row are created *before* the
    pipeline (all in this one event loop) so the resume controller has a
    stable Repository.id to checkpoint against, and so an interrupt mid-run
    leaves a resumable, persisted index behind. Skipped on a dry run, which
    must not touch the database at all.
    """
    opts = run.opts
    controller = None
    engine = None
    if not opts.dry_run:
        controller, engine = await build_resume_controller(run.repo_path, resume=opts.resume)
    try:
        return await run_pipeline(
            run.repo_path,
            commit_depth=run.resolved_commit_limit,
            follow_renames=run.resolved_follow_renames,
            skip_tests=opts.skip_tests,
            skip_infra=opts.skip_infra,
            exclude_patterns=run.exclude_patterns if run.exclude_patterns else None,
            include_submodules=opts.include_submodules,
            generate_docs=False,
            llm_client=llm_client,
            vector_store=vector_store,
            concurrency=opts.concurrency,
            test_run=opts.test_run,
            mode=mode,
            progress=callback,
            existing_kg_fingerprint=existing_kg_fingerprint,
            resume_controller=controller,
            coverage_report_paths=(
                [Path(p) for p in opts.coverage_report] if opts.coverage_report else None
            ),
            # Local `init` is the one place a fact about this machine's
            # environment is a fact about the user's environment.
            derive_environment_facts=True,
        )
    finally:
        if engine is not None:
            await engine.dispose()


def index_repository(run: InitRun) -> bool:
    """Phases 1 and 2: ingestion and analysis. False when the user interrupted.

    Always run with ``generate_docs=False``; generation happens separately,
    after the cost confirmation.
    """
    opts = run.opts
    # Index-only generates too, it just renders templates instead of prompting
    # a model, so it is four phases like a full run. Fast mode is the one that
    # still stops at three.
    run.total_phases = 3 if opts.run_mode == "fast" else 4
    # None on a dry run: decision extraction and KG enrichment both call this
    # client before the generation phase's dry-run return is reached.
    llm_client = None
    if not opts.dry_run:
        llm_client = run.provider if not opts.index_only else run.decision_provider

    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    orchestrator_mode = (
        OrchestratorMode.FAST if opts.run_mode == "fast" else OrchestratorMode.STANDARD
    )
    index_vector_store = _index_vector_store(run)

    with Progress(*_index_columns(llm_client is not None), console=console) as progress_bar:
        rich_callback = RichProgressCallback(progress_bar, console, total_phases=run.total_phases)
        # Records per-phase wall-clock durations without changing the pipeline
        # API; the timings are persisted to state.json.
        callback = PhaseTimingRecorder(rich_callback)
        # The denominator. Phases nest (``persist.fts`` inside ``persist``)
        # and some overlap, so the totals can exceed it - without it there is
        # no way to tell an unrecorded stage from an overlapping one.
        callback.table.start("run")

        prev_state = load_state(run.repo_path)
        prev_kg_fp = (
            prev_state.get("knowledge_graph", {}).get("fingerprint") if not opts.force else None
        )

        # Make the long synchronous index/analysis phases interruptible: the
        # first Ctrl-C unwinds them cleanly (the INDEX checkpoint already on
        # disk is reused on the next --resume), a second forces a hard quit.
        from repowise.core.cancellation import PipelineCancelled, cancellation_scope

        try:
            with cancellation_scope():
                result = run_async(
                    _index_with_resume(
                        run,
                        run_pipeline=run_pipeline,
                        llm_client=llm_client,
                        vector_store=index_vector_store,
                        mode=orchestrator_mode,
                        callback=callback,
                        existing_kg_fingerprint=prev_kg_fp,
                    )
                )
        except (PipelineCancelled, KeyboardInterrupt):
            from repowise.cli.ui.mascot import EYES_SLEEPY, mini

            console.print(
                f"\n{mini(EYES_SLEEPY)} [{WARN}]Interrupted.[/] Indexed work so far has been "
                "saved — run [bold]repowise init --resume[/] to continue where it stopped."
            )
            from repowise.cli.hints import maybe_hint

            maybe_hint("interrupt")
            return False

    run.result = result
    run.recorder = callback
    # What the run degraded on, in a place an agent can read after the
    # terminal is gone. Timings are read after persistence instead, so
    # generation and the persist tail are in the table too.
    run.run_warnings = list(rich_callback.warnings)
    return True
