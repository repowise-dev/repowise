"""Page regeneration for a docs update: the generator run and its progress."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

from repowise.cli.helpers import console, run_async
from repowise.cli.ui import make_progress
from repowise.core.pipeline import timed

from .options import DocsPass, UpdateOptions, UpdateRun


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
    """Live progress for generate_all: a bar in rich mode, JSON events otherwise."""

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
        # The total is an estimate; keep it at least the real count.
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

    regen_set = set(run.affected.regenerate)
    affected_parsed = [pf for pf in run.parsed_files if pf.file_info.path in regen_set]
    affected_source = {p: s for p, s in run.source_map.items() if p in regen_set}

    docs.prior_pages = _load_prior_pages(run)

    # Without the vector store, regenerated pages drop out of semantic search.
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

    progress = _PageProgress(run.emitter, docs.cost_tracker)

    # Each page is persisted as it lands, so a rerun after a crash re-bills nothing.
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

    gen_bar = make_progress(console, cost=True) if run.emitter is None else nullcontext()
    with gen_bar as gen_progress:
        progress.bar = gen_progress
        progress.task = (
            gen_progress.add_task("Generating pages...", total=None, cost=0.0, unit="pages")
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

        # Snap the estimate to the real count so the bar ends at N of N.
        if progress.task is not None:
            gen_progress.update(progress.task, total=progress.completed, completed=progress.completed)

    docs.full_stats = {"embed_failed_pages": generator.embed_failed_pages}
    if checkpointer.failure:
        run.degraded.append(f"Per-page crash checkpointing: {checkpointer.failure}")


def _load_prior_pages(run: UpdateRun) -> dict[str, object]:
    """Prior pages, so an unchanged prompt hash skips the model call."""
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
        run.degraded.append(f"Prior-page reuse: {exc}")
        return {}


def render_stale_structure(opts: UpdateOptions, run: UpdateRun, docs: DocsPass) -> None:
    """Re-render stale structural pages from the full graph (no model calls)."""
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
