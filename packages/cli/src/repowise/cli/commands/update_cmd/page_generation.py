"""Page regeneration for a docs update: the generator run and its progress."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

from repowise.cli.helpers import run_async
from repowise.core.pipeline import timed

from .options import DocsPass, UpdateOptions, UpdateRun
from .reporting import make_generation_progress


def _generation_warning_handler(
    degraded: list[str], *, require_embedding_success: bool
) -> Callable[[str], None]:
    """Record generation warnings and make required embedding writes fatal."""

    def _handle(message: str) -> None:
        degraded.append(message)
        if require_embedding_success:
            raise RuntimeError(
                "Configuration-triggered page embedding failed; the previous "
                "fingerprint was retained so the next update retries."
            )

    return _handle


class _PageProgress:
    """Live progress for generate_all: a bar in rich mode, JSON events otherwise.

    Cost is read from the tracker as each page lands so the readout ticks up
    in real time.
    """

    def __init__(self, emitter: Any, cost_tracker: Any) -> None:
        self.emitter = emitter
        self.cost_tracker = cost_tracker
        self.completed = 0
        self.total: int | None = None
        self.bar: Any = None
        self.task: Any = None

    def on_total_known(self, total: int) -> None:
        self.total = total
        if self.emitter is not None:
            self.emitter.total_known(total)
        else:
            self.bar.update(self.task, total=total)

    def on_page_done(self, _page_id: str) -> None:
        self.completed += 1
        # The total is an up-front estimate (see _announce_total); some page
        # categories can't be predicted exactly, so keep it monotonic with the
        # real count, so it never renders "43 of 41" (issue #922).
        if self.total is not None and self.completed > self.total:
            self.total = self.completed
        if self.emitter is not None:
            self.emitter.page_done(
                completed=self.completed,
                total=self.total,
                cost_usd=self.cost_tracker.session_cost,
            )
        else:
            self.bar.update(
                self.task, advance=1, total=self.total, cost=self.cost_tracker.session_cost
            )


def generate_pages(opts: UpdateOptions, run: UpdateRun, docs: DocsPass) -> None:
    """Regenerate the affected pages, checkpointing each one as it lands."""
    from repowise.core.generation import ContextAssembler, PageGenerator

    # Filter to only affected files
    regen_set = set(run.affected.regenerate)
    affected_parsed = [pf for pf in run.parsed_files if pf.file_info.path in regen_set]
    affected_source = {p: s for p, s in run.source_map.items() if p in regen_set}

    docs.prior_pages = _load_prior_pages(run)

    # Generate affected pages. The vector store (shared with the decision
    # pass above) must ride along: without it regenerated pages are never
    # re-embedded, so every update silently evicted its pages from semantic
    # search — on long-lived repos the LanceDB corpus ended up holding
    # decisions and structural pages but zero current file pages.
    repo_path = run.repo_path
    config = docs.config
    assembler = ContextAssembler(config, repo_path=repo_path)
    generator = PageGenerator(
        docs.provider,
        assembler,
        config,
        vector_store=docs.decision_vector_store,
        language=config.language,
        prior_pages=docs.prior_pages,
        repo_path=repo_path,
    )
    repo_name = repo_path.name

    if run.emitter is not None:
        run.emitter.stage("generate")

    # Drive a live progress bar (owl spinner + running cost) off generate_all's
    # page callbacks, matching init's generation phase. In json progress mode
    # there is no bar, so the same callbacks emit total_known / page_done
    # events on stdout instead.
    progress = _PageProgress(run.emitter, docs.cost_tracker)

    # Checkpoint each page to the DB as it lands: a crash mid-generation used
    # to lose every finished page (persist ran only at the very end), so the
    # rerun re-billed all of them. With the row persisted, the rerun's
    # prompt-hash skip sees the fresh content and never re-calls the LLM.
    from .persistence import PageCheckpointer

    checkpointer = PageCheckpointer(repo_path, repo_name)

    async def _generate_with_checkpoint() -> list:
        await checkpointer.start()
        try:
            return await generator.generate_all(
                affected_parsed,
                affected_source,
                run.graph_builder,
                run.repo_structure,
                repo_name,
                on_page_done=progress.on_page_done,
                on_total_known=progress.on_total_known,
                git_meta_map=run.git_meta_map,
                repo_path=repo_path,
                on_page_ready=checkpointer.on_page_ready,
                on_warning=_generation_warning_handler(
                    run.degraded,
                    require_embedding_success=(
                        run.config.traversal_changed or run.config.generation_changed
                    ),
                ),
            )
        finally:
            await checkpointer.close()

    with make_generation_progress() if run.emitter is None else nullcontext() as gen_progress:
        progress.bar = gen_progress
        progress.task = (
            gen_progress.add_task("Generating pages...", total=None, cost=0.0)
            if gen_progress is not None
            else None
        )

        try:
            with timed(run.timings, "generate"):
                docs.generated_pages = run_async(_generate_with_checkpoint())
        except Exception as exc:
            if run.emitter is not None:
                run.emitter.error(str(exc))
            raise

        # Reconcile the bar to the pages actually produced. The up-front total
        # is an estimate and onboarding slots may gate-skip, leaving it above
        # the real count; snap total to completed so the bar reads N of N at
        # 100% instead of stalling short (issue #922).
        if progress.task is not None:
            gen_progress.update(progress.task, total=progress.completed, completed=progress.completed)

    docs.full_stats = {"embed_failed_pages": generator.embed_failed_pages}
    if checkpointer.failure:
        run.degraded.append(f"Per-page crash checkpointing: {checkpointer.failure}")


def _load_prior_pages(run: UpdateRun) -> dict[str, object]:
    """Prior pages, so the generator can skip the LLM call for any affected
    file whose freshly rendered prompt still hashes to the persisted value."""
    repo_path = run.repo_path

    async def _load_prior() -> dict[str, object]:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            load_prior_pages,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            return await load_prior_pages(session, repo.id)

    try:
        return run_async(_load_prior())
    except Exception as exc:
        # Without prior pages the prompt-hash skip is off and every affected
        # page re-bills; surface that instead of silently paying it.
        run.degraded.append(f"Prior-page reuse: {exc}")
        return {}


def render_stale_structure(opts: UpdateOptions, run: UpdateRun, docs: DocsPass) -> None:
    """Re-render stale structural (SCC) pages from the complete repository view.

    SCC pages are deterministic but describe the complete graph, so the
    changed-file generator cannot safely refresh them. Render only the stale
    structural ids with the template provider. This performs no model calls
    and reuses the update's vector store so semantic search is refreshed with
    the page.
    """
    if not run.stale_deterministic_ids or run.config.generation_changed:
        return
    from .deterministic import regenerate_deterministic_page_ids

    with timed(run.timings, "render.structure"):
        docs.generated_pages.extend(
            regenerate_deterministic_page_ids(
                repo_path=run.repo_path,
                parsed_files=run.parsed_files,
                source_map=run.source_map,
                graph_builder=run.graph_builder,
                repo_structure=run.repo_structure,
                git_meta_map=run.git_meta_map,
                page_ids=run.stale_deterministic_ids,
                cfg=run.cfg,
                concurrency=opts.concurrency,
                degraded=run.degraded,
                dead_code_report=run.dead_code_report,
                prior_page_ids=docs.prior_pages,
                vector_store=docs.decision_vector_store,
                stats_out=docs.full_stats,
            )
        )
