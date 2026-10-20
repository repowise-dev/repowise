"""Ingestion and analysis stage of ``repowise init`` (phases 1 and 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.helpers import console, load_state, run_async
from repowise.cli.ui import progress_steps

from ._options import InitRun
from .persistence import build_resume_controller


def structural_generation_config(index_only: bool) -> Any:
    """A deterministic config for a structural run, so the pipeline knows no model was chosen.

    The pipeline generates no pages here (the CLI renders them afterwards); the
    config only tells decision reporting why the model-only sources did not run.
    """
    if not index_only:
        return None
    from repowise.core.generation.models import GenerationConfig

    return GenerationConfig(deterministic=True)


def _index_vector_store(run: InitRun) -> Any:
    """Generation's vector store, built early so analysis can dedup decisions against it.

    None for runs that build no store (a dry run, index-only fast mode) and
    for a keyless store, which the matcher refuses.
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


#: What the long-step line tells a user to run after Ctrl-C.
RESUME_COMMAND = "repowise init --resume"


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

    The controller's engine and repository row are created first, in this
    event loop, so an interrupt leaves a resumable index. A dry run touches no
    database.
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
            # Only local init describes the user's own environment.
            derive_environment_facts=True,
            generation_config=structural_generation_config(opts.index_only),
        )
    finally:
        if engine is not None:
            await engine.dispose()


def decision_llm_client(run: InitRun) -> Any:
    """The model decision extraction calls this run, or None (always None on a dry run).

    A structural run makes no model calls, decisions included.
    """
    return None if run.opts.dry_run or run.opts.index_only else run.provider


def index_repository(run: InitRun) -> None:
    """Phases 1 and 2, without docs (generation follows the cost gate). Exits 130 if interrupted."""
    opts = run.opts
    # Fast mode skips generation, so it has three phases.
    run.total_phases = 3 if opts.run_mode == "fast" else 4
    # None on a dry run: analysis would call it before generation's dry-run exit.
    llm_client = decision_llm_client(run)

    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    orchestrator_mode = (
        OrchestratorMode.FAST if opts.run_mode == "fast" else OrchestratorMode.STANDARD
    )
    index_vector_store = _index_vector_store(run)

    # Cost column only when a model can be called.
    with progress_steps(
        console,
        cost=llm_client is not None,
        resume_command=RESUME_COMMAND,
        total_phases=run.total_phases,
    ) as steps:
        callback = PhaseTimingRecorder(steps)
        # Whole-run total: phases nest and overlap, so their sum can exceed it.
        callback.table.start("run")

        prev_state = load_state(run.repo_path)
        prev_kg_fp = (
            prev_state.get("knowledge_graph", {}).get("fingerprint") if not opts.force else None
        )

        # First Ctrl-C unwinds cleanly for a later --resume; a second hard-quits.
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
            from ._exit import exit_interrupted

            exit_interrupted()

    run.result = result
    run.recorder = callback
    run.run_warnings = list(steps.warnings)
