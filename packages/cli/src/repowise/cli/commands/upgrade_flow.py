"""``repowise update --full`` — incremental fast→full index upgrade.

A repo indexed with ``--mode fast`` has the full structural graph + metrics
persisted, but only ESSENTIAL git signals and no LLM docs. Re-running
``repowise init`` would upgrade it, but it re-parses *and* rebuilds the graph
from scratch — redoing the expensive resolution/centrality work the fast index
already did.

This module upgrades incrementally instead:

1. Create the repository row **first** (so the JobStore-backed backfill's
   ``pipeline_jobs`` FK is satisfiable from the CLI).
2. Backfill the git tier ESSENTIAL → FULL via the resumable
   ``backfill_full_tier`` worker (per-file blame + repo-wide co-change).
3. Rehydrate the dependency graph from SQL (``rehydrate_graph_builder``) —
   no re-resolution, no centrality recompute.
4. Re-parse files for ASTs + source bytes (the only unavoidable re-work) and
   generate the docs the fast index skipped, against the rehydrated graph.
5. Persist pages + git metadata and flip the persisted state to full.

The normal incremental ``repowise update`` path is untouched; this runs only
when ``--full`` is passed.
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click

from repowise.cli.commands.decision_lanes import (
    print_lane_estimate,
    run_and_report_lanes,
    still_pending_after,
    with_lane_plan,
)
from repowise.cli.commands.upgrade_state import (
    _finish_upgrade_state,
    _mark_transition,
    _start_transition,
    _unpack_upgrade_result,
    _upgrade_checkpoint,
)
from repowise.cli.helpers import (
    console,
    get_head_commit,
    load_config,
    load_state,
    resolve_max_file_pages,
    resolve_provider,
    resolve_reasoning,
    run_async,
    save_state,
)
from repowise.cli.providers import is_free_embedder
from repowise.core.decision_lanes import plan_decision_lanes
from repowise.core.index_scope import FAST_TO_FULL_COMMAND, file_page_scope
from repowise.core.update_lock import release_update_lock, try_acquire_update_lock


def _gate_cost(
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    provider: Any,
    repo_path: Path,
    *,
    yes: bool,
    embedder: str = "mock",
    decisions: Any = None,
) -> None:
    """Print the estimated spend, embedding included, and confirm past the gate.

    Raises on decline.

    Best-effort on the estimate itself: a failure there prints the reason and
    lets the run proceed rather than blocking an upgrade on a number that is
    advisory anyway.
    """
    try:
        from repowise.cli.commands.init_cmd.generation import (
            COST_GATE_USD,
            cost_gate_declined,
            embedding_note,
            format_cost,
            structural_page_summary,
        )
        from repowise.cli.cost_gate import spend_phrase
        from repowise.cli.providers.embedders import resolve_embedding_model
        from repowise.core.cost_estimator import build_generation_plan, estimate_cost

        plans = build_generation_plan(parsed_files, graph_builder, config)
        est = estimate_cost(
            with_lane_plan(plans, decisions),
            provider.provider_name,
            provider.model_name,
            repo_path=repo_path,
            embedder=embedder,
            embedding_model=resolve_embedding_model(embedder),
        )
    except Exception as exc:
        console.print(f"[yellow]Cost estimate unavailable ({exc}); continuing.[/yellow]")
        return

    pages = sum(p.count for p in plans)
    console.print(f"[bold]{pages}[/bold] pages. {spend_phrase(est, config.max_concurrency)}.")
    structural = structural_page_summary(plans)
    if structural:
        console.print(f"  [dim]{structural}[/dim]")
    embedding = embedding_note(est)
    if embedding:
        console.print(f"  [dim]{embedding}[/dim]")
    print_lane_estimate(decisions, provider)

    # Nothing to ask on a run that cannot be asked. Without this the confirm
    # reads EOF, raises, and the caller reports a successful run that generated
    # nothing, which is the worst of the three possible answers.
    if est.estimated_cost_usd > COST_GATE_USD and not yes and not sys.stdin.isatty():
        raise click.ClickException(
            f"This would spend about {format_cost(est)} and there is no terminal to "
            "confirm on. Re-run with --yes to accept the cost."
        )
    if cost_gate_declined(est, yes=yes, message="  Generate the wiki at this cost?"):
        raise click.Abort()


def _reparse(
    repo_path: Path, exclude_patterns: list[str]
) -> tuple[list[Any], dict[str, bytes], Any]:
    """Parse files for ASTs + source bytes WITHOUT building/resolving the graph.

    Thin CLI wrapper over :func:`repowise.core.pipeline.reparse_repo`: reads the
    persisted submodule/nested-repo semantics from ``state.json`` so a fast index
    built with ``--include-submodules`` re-parses the same file set, then defers
    to the shared core parser.
    """
    from repowise.core.pipeline import reparse_repo

    # Honor the persisted submodule semantics of the original index — a
    # fast index built with --include-submodules must not drop submodule
    # files from the docs re-parse (missing key → False, legacy behavior).
    state = load_state(repo_path)
    return reparse_repo(
        repo_path,
        exclude_patterns,
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
    )


async def _backfill_git(
    sf: Any,
    repo_id: str,
    repo_path: Path,
    *,
    commit_limit: int | None,
    follow_renames: bool,
) -> tuple[dict[str, dict], dict[str, Any] | None]:
    """Promote the git tier to FULL via the resumable backfill worker.

    Returns the ``file_path → git-metadata`` map for the freshly-indexed FULL
    signals, which is also persisted here so co-change/blame land in the DB.
    """
    from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
    from repowise.core.ingestion.git_indexer.backfill import (
        BACKFILL_PHASE,
        backfill_full_tier,
    )
    from repowise.core.persistence import get_session
    from repowise.core.persistence.crud import (
        clear_attribution_if_disabled,
        recompute_git_percentiles,
        upsert_git_commits_bulk,
        upsert_git_metadata_bulk,
    )
    from repowise.core.persistence.stores.sql_job_store import SqlJobStore

    indexer = GitIndexer(
        repo_path,
        commit_limit=commit_limit,
        follow_renames=follow_renames,
        tier=GitIndexTier.FULL,
    )

    async with get_session(sf) as session:
        job_store = SqlJobStore(session)
        resumable = await job_store.find_resumable(repository_id=repo_id)
        if any(j.phase == BACKFILL_PHASE for j in resumable):
            console.print(
                "[dim]Found an interrupted git backfill — resuming (re-running FULL tier).[/dim]"
            )
        summary, git_results = await backfill_full_tier(indexer, repo_id, job_store=job_store)
        if git_results:
            await upsert_git_metadata_bulk(session, repo_id, git_results)
            await recompute_git_percentiles(session, repo_id)
        # Persist the per-commit rows captured during the FULL-tier walk so the
        # commits/change-risk surface lands on an ESSENTIAL→FULL promotion too
        # (Foundation 1 only wrote them on the full orchestrator index).
        if summary.commit_rows:
            await upsert_git_commits_bulk(session, repo_id, summary.commit_rows)
        await clear_attribution_if_disabled(session, repo_id)

    console.print(
        f"Git tier upgraded to FULL: [cyan]{summary.files_indexed}[/cyan] files "
        "(per-file blame + co-change)."
    )
    coverage = getattr(summary, "history_coverage", None)
    return (
        {m["file_path"]: m for m in git_results if m.get("file_path")},
        coverage.to_dict() if coverage is not None else None,
    )


async def _scan_commit_health(sf: Any, repo_id: str, repo_path: Path) -> None:
    """Per-commit health at the full index's budget: an upgrade is expected to take long."""
    from repowise.core.pipeline.commit_health import (
        budget_seconds,
        refresh_commit_health_after_update,
    )

    budget = budget_seconds()
    console.print(f"Scanning commit health (up to {budget:.0f} s)…")
    await refresh_commit_health_after_update(sf, repo_id, str(repo_path), budget=budget)


async def _persist_authoritative_pages(
    *,
    session: Any,
    repository_id: str,
    generated_pages: list[Any],
    vector_store: Any,
) -> list[str]:
    """Persist a full selection and retire every page outside it."""
    from repowise.core.persistence import upsert_pages_from_generated
    from repowise.core.pipeline.persist import tombstone_pages_outside_generation

    await upsert_pages_from_generated(session, generated_pages, repository_id)
    retired_page_ids = await tombstone_pages_outside_generation(
        session,
        repository_id,
        {page.page_id for page in generated_pages},
    )
    if retired_page_ids and vector_store is not None:
        await vector_store.delete_many(retired_page_ids)
    return retired_page_ids


async def _sync_authoritative_fts(
    *,
    engine: Any,
    repo_path: Path,
    generated_pages: list[Any],
    retired_page_ids: list[str],
) -> None:
    """Replace full-run FTS content and durably retry retired-id cleanup."""
    from repowise.core.persistence.search import FullTextSearch
    from repowise.core.pipeline.cleanup_debt import (
        clear_cleanup_debt,
        load_cleanup_debt,
        record_cleanup_debt,
    )

    cleanup_ids = set(retired_page_ids) | load_cleanup_debt(repo_path)["fts"]
    try:
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        if cleanup_ids:
            await fts.delete_many(sorted(cleanup_ids))
        await fts.index_pages(generated_pages)
    except BaseException:
        record_cleanup_debt(repo_path, "fts", cleanup_ids)
        raise
    else:
        clear_cleanup_debt(repo_path, "fts", cleanup_ids)


async def _run_upgrade(
    repo_path: Path,
    provider: Any,
    config: Any,
    *,
    exclude_patterns: list[str],
    commit_limit: int | None,
    follow_renames: bool,
    embedder_name: str | None,
    yes: bool,
    checkpoint: Any | None = None,
) -> tuple[list[Any], int, dict[str, Any]]:
    """Drive the full upgrade and return the generated pages."""
    from repowise.core.pipeline import rehydrate_graph_builder

    engine, sf, repo_id = await _open_repository(repo_path)

    # 2. Backfill the git tier ESSENTIAL -> FULL (resumable via JobStore).
    git_meta_map, history_coverage = await _backfill_git(
        sf,
        repo_id,
        repo_path,
        commit_limit=commit_limit,
        follow_renames=follow_renames,
    )
    if checkpoint:
        checkpoint("git_backfill", git_history_coverage=history_coverage)

    # 3. Rehydrate the graph from SQL — no parse, no resolution, no recompute.
    from repowise.core.persistence import get_session

    try:
        async with get_session(sf) as session:
            graph_builder = await rehydrate_graph_builder(session, repo_id, repo_path)
    except ValueError as exc:
        # Only the empty-graph case; any other ValueError is a real fault.
        if "No persisted graph nodes" not in str(exc):
            raise
        await engine.dispose()
        raise click.ClickException(
            f"The index at {repo_path} has no code graph. Run `repowise init` to rebuild it."
        ) from None

    # 4. Re-parse for ASTs + source (the only unavoidable re-work). The graph
    # is NOT rebuilt — generation traverses the rehydrated SQL graph.
    parsed_files, source_map, repo_structure = _reparse(repo_path, exclude_patterns)
    # The health pass below reads attributes SQL does not store.
    graph_builder.restore_parse_only_attrs(parsed_files)
    console.print(
        f"Re-parsed [cyan]{len(parsed_files)}[/cyan] files for doc generation "
        "(graph reused from index — not re-resolved)."
    )

    # The decision lanes a keyless index skipped run with this model as well.
    lane_inputs = SimpleNamespace(
        graph_builder=graph_builder,
        git_meta_map=git_meta_map,
        parsed_files=parsed_files,
        source_map=source_map,
    )
    # A keyless upgrade has no model for them, so they stay pending.
    lane_plan = (
        None
        if getattr(config, "deterministic", False)
        else plan_decision_lanes(load_state(repo_path), repo_path, lane_inputs)
    )

    # Show the bill before running it, as init does: the user reaching for
    # `--full` has never been shown a cost for this repo. Documentation built
    # from code with a free embedder has no bill to show.
    free_run = getattr(config, "deterministic", False) and is_free_embedder(embedder_name or "mock")
    if not free_run:
        _gate_cost(
            parsed_files,
            graph_builder,
            config,
            provider,
            repo_path,
            yes=yes,
            embedder=embedder_name or "mock",
            decisions=lane_plan,
        )

    # 5. Generate the docs the fast index skipped, embedding as we go.
    cost_tracker = _upgrade_cost_tracker(sf, repo_id)
    provider._cost_tracker = cost_tracker
    embedder, vector_store, embedding_error = _upgrade_embedding(repo_path, embedder_name)
    generation_scope: dict[str, int | None] = {}
    generated_pages = await _generate_upgrade_pages(
        repo_path,
        provider,
        config,
        inputs=(parsed_files, source_map, graph_builder, repo_structure, git_meta_map),
        embedder=embedder,
        vector_store=vector_store,
        cost_tracker=cost_tracker,
        generation_scope=generation_scope,
    )
    stub_fallbacks, stub_reasons = _stub_fallback_summary(generated_pages)
    if checkpoint:
        checkpoint("generation")

    # Flush buffered cost rows now generation is done (best-effort).
    await cost_tracker.flush()

    lanes = await run_and_report_lanes(
        sf,
        repo_id,
        repo_path,
        lane_plan,
        provider=provider,
        inputs=lane_inputs,
        vector_store=vector_store,
        cost_tracker=cost_tracker,
    )

    # 6. Persist pages + a GenerationJob marker, then build the FTS index.
    retired_page_ids = await _persist_upgrade_pages(
        sf, repo_id, generated_pages, vector_store, stub_fallbacks
    )
    try:
        await _sync_authoritative_fts(
            engine=engine,
            repo_path=repo_path,
            generated_pages=generated_pages,
            retired_page_ids=retired_page_ids,
        )
    except BaseException:
        await engine.dispose()
        raise
    if checkpoint:
        checkpoint("search")

    # 7. Recompute + persist code health against the now-FULL git tier.
    await _recompute_full_tier_health(
        engine, sf, repo_id, repo_path, graph_builder, git_meta_map, parsed_files
    )
    await _scan_commit_health(sf, repo_id, repo_path)
    if checkpoint:
        checkpoint("health")

    total_pages = await _repository_page_count(sf, repo_id, fallback=len(generated_pages))

    await engine.dispose()
    from repowise.cli.providers import semantic_search_command
    from repowise.core.generation.selection import count_documentable_files
    from repowise.core.providers.embedding.base import MockEmbedder

    # Mock vectors are hashes: a store built on them is no semantic search.
    semantic_on = vector_store is not None and not isinstance(embedder, MockEmbedder)
    return (
        generated_pages,
        total_pages,
        {
            "git_history_coverage": history_coverage,
            "file_pages": {
                "configured_cap": getattr(config, "max_file_pages", None),
                **(
                    generation_scope
                    or file_page_scope(
                        configured_cap=getattr(config, "max_file_pages", None),
                        eligible=count_documentable_files(parsed_files),
                        generated_pages=generated_pages,
                    )
                ),
            },
            "search": {
                "full_text": "available",
                "semantic": "available" if semantic_on else "unavailable",
                "next_command": (
                    None if semantic_on else semantic_search_command(repo_path, embedder_name)
                ),
            },
            "embedding_error": embedding_error,
            "stub_fallbacks": stub_fallbacks,
            "stub_reasons": stub_reasons,
            "decision_lanes_pending": still_pending_after(lanes),
        },
    )


async def _open_repository(repo_path: Path) -> tuple[Any, Any, Any]:
    """Open the repo DB and upsert its repository row; returns engine, factory, id.

    The row comes FIRST: pipeline_jobs.repository_id is an FK to repositories.id,
    so it must exist before the backfill creates its checkpoint job.

    A repo-local store copied from another path keeps the old ``local_path``.
    Its single row is rebound here, as ``repo_index_session`` reads it, so the
    upsert reuses the indexed row instead of adding an empty one.
    """
    from sqlalchemy import select

    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_configured_db_url,
        get_repository_by_path,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import Repository

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        if (
            get_configured_db_url() is None
            and await get_repository_by_path(session, str(repo_path)) is None
        ):
            repos = (await session.scalars(select(Repository).limit(2))).all()
            if len(repos) == 1:
                repos[0].local_path = str(repo_path)
                await session.flush()
        repo = await upsert_repository(
            session, name=repo_path.name, local_path=str(repo_path), stamp_head=True
        )
        repo_id = repo.id
    return engine, sf, repo_id


def _upgrade_cost_tracker(sf: Any, repo_id: Any) -> Any:
    """A cost tracker honoring the cost-tracking opt-out.

    An in-memory tracker still powers the live cost readout. ``buffered=True``
    defers cost INSERTs to one post-generation flush so they never contend with
    the generation writer.
    """
    from repowise.cli.providers import cost_tracking_disabled
    from repowise.core.generation.cost_tracker import CostTracker

    if cost_tracking_disabled():
        return CostTracker()
    return CostTracker(session_factory=sf, repo_id=repo_id, buffered=True)


def _upgrade_embedding(repo_path: Path, embedder_name: str | None) -> tuple[Any, Any, str | None]:
    """The embedder and vector store pages are embedded into as they are generated.

    The store is the same LanceDB directory `init` and `update` write to; without
    it the written wiki would be invisible to semantic search.
    """
    from repowise.cli.providers import build_embedder, build_vector_store, resolve_embedder

    embedder = None
    vector_store = None
    embedding_error: str | None = None
    try:
        embedder = build_embedder(resolve_embedder(embedder_name), repo_path)
        vector_store = build_vector_store(repo_path, embedder)
    except Exception as exc:
        embedding_error = str(exc)
        console.print(f"[yellow]Embedding skipped: {exc}[/yellow]")
    return embedder, vector_store, embedding_error


async def _generate_upgrade_pages(
    repo_path: Path,
    provider: Any,
    config: Any,
    *,
    inputs: tuple[Any, Any, Any, Any, Any],
    embedder: Any,
    vector_store: Any,
    cost_tracker: Any,
    generation_scope: dict[str, int | None],
) -> list[Any]:
    """Generate through init's persistence wrapper under a live progress bar.

    Pages are written the instant each completes (a Ctrl-C loses at most the
    in-flight page), prior pages are reused from the store, and the bar advances.
    ``inputs`` is (parsed_files, source_map, graph_builder, repo_structure,
    git_meta_map).
    """
    from repowise.cli.commands.init_cmd._generation_persist import (
        run_generation_with_persistence,
    )
    from repowise.cli.ui import RichProgressCallback, make_progress

    parsed_files, source_map, graph_builder, repo_structure, git_meta_map = inputs
    with make_progress(console, cost=True) as upgrade_progress:
        gen_callback = RichProgressCallback(upgrade_progress, console)
        return await run_generation_with_persistence(
            repo_path=repo_path,
            repo_name=repo_path.name,
            parsed_files=parsed_files,
            source_map=source_map,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            git_meta_map=git_meta_map,
            llm_client=provider,
            embedder=embedder,
            vector_store=vector_store,
            concurrency=config.max_concurrency,
            progress=gen_callback,
            cost_tracker=cost_tracker,
            generation_config=config,
            selection_out=generation_scope,
        )


def _stub_fallback_summary(generated_pages: list[Any]) -> tuple[int, list[str]]:
    """Placeholders the model failed to fill, and why.

    `repowise generate` refills them; failing the run would discard every page
    that succeeded.
    """
    from repowise.core.generation.models import (
        STUB_FALLBACK_ERROR,
        count_stub_fallbacks,
        is_stub_fallback,
    )

    stub_fallbacks = count_stub_fallbacks(generated_pages)
    stub_reasons = sorted(
        {str(p.metadata.get(STUB_FALLBACK_ERROR, "")).strip() for p in generated_pages if is_stub_fallback(p)}
        - {""}
    )
    return stub_fallbacks, stub_reasons


async def _persist_upgrade_pages(
    sf: Any, repo_id: Any, generated_pages: list[Any], vector_store: Any, stub_fallbacks: int
) -> list[str]:
    """Persist pages, place them in the tree and record the job; returns retired ids.

    A successful full generation is authoritative for every generated page
    type, so rows absent from its selection are retired even when their file
    still exists; otherwise doctor keeps reporting them stale.
    """
    from repowise.core.persistence import get_session

    async with get_session(sf) as session:
        retired_page_ids = await _persist_authoritative_pages(
            session=session,
            repository_id=repo_id,
            generated_pages=generated_pages,
            vector_store=vector_store,
        )
        try:
            from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

            await rebuild_page_tree(session, repo_id)
        except Exception:
            # Placement is navigation, not content: never fail the upgrade on it.
            pass
        try:
            from datetime import UTC, datetime

            from repowise.core.persistence.crud import upsert_generation_job

            now = datetime.now(UTC)
            # A stub has a row but no prose, so it is not a completed page.
            job = await upsert_generation_job(
                session,
                repository_id=repo_id,
                status="completed",
                total_pages=len(generated_pages),
                config={"mode": "upgrade", "source": "cli_update_full"},
            )
            job.completed_pages = len(generated_pages) - stub_fallbacks
            job.failed_pages = stub_fallbacks
            job.started_at = now
            job.finished_at = now
        except Exception:
            pass  # job recording is best-effort
    return retired_page_ids


async def _recompute_full_tier_health(
    engine: Any,
    sf: Any,
    repo_id: Any,
    repo_path: Path,
    graph_builder: Any,
    git_meta_map: Any,
    parsed_files: list[Any],
) -> None:
    """Re-run health at the FULL git tier, or dispose the engine and raise.

    The fast index persisted ESSENTIAL-tier findings only, so the blame and
    co-change biomarkers were no-ops at that tier. Mirrors `init` / `update`.
    """
    from repowise.core.persistence import get_session

    try:
        from repowise.core.analysis.health.trends import snapshot_fields
        from repowise.core.persistence.crud import (
            save_health_findings,
            save_health_metrics,
            save_health_snapshot,
        )
        from repowise.core.pipeline.orchestrator import _run_health_analysis
        from repowise.core.workspace.update import get_head_commit

        health_report = await _run_health_analysis(
            graph_builder,
            git_meta_map,
            parsed_files,
            repo_path=repo_path,
            progress=None,
        )
        if health_report is not None:
            async with get_session(sf) as session:
                await save_health_metrics(
                    session,
                    repo_id,
                    health_report.metrics or [],
                    analyzed_commit=get_head_commit(repo_path),
                )
                if health_report.findings:
                    await save_health_findings(session, repo_id, health_report.findings)
                kpis = health_report.kpis or {}
                with contextlib.suppress(Exception):  # snapshot is best-effort
                    fields = snapshot_fields(
                        kpis, health_report.metrics or [], health_report.findings or []
                    )
                    if fields is not None:
                        await save_health_snapshot(session, repo_id, **fields)
            console.print(
                f"Code health recomputed at FULL tier: "
                f"[cyan]{len(health_report.findings)}[/cyan] findings."
            )
    except Exception as exc:
        await engine.dispose()
        raise RuntimeError(f"FULL-tier health recompute failed: {exc}") from exc
    if health_report is None:
        await engine.dispose()
        raise RuntimeError("FULL-tier health recompute returned no report")


async def _repository_page_count(sf: Any, repo_id: Any, *, fallback: int) -> int:
    """The repo's real page count, not this run's.

    An upgrade regenerates rather than appends and need not cover every page the
    repo had, so this run's count would under-report what `status` shows.
    """
    from repowise.core.persistence import get_session

    try:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from repowise.core.persistence.models import Page

        async with get_session(sf) as session:
            return int(
                (
                    await session.execute(
                        sa_select(sa_func.count())
                        .select_from(Page)
                        .where(Page.repository_id == repo_id)
                    )
                ).scalar_one()
            )
    except Exception:
        return fallback


def _report_placeholders(count: int, reasons: list[str]) -> None:
    """Name the pages the model failed and the command that refills them."""
    if not count:
        return
    lines = "".join(f"  [dim]· {r[:160]}[/dim]\n" for r in reasons[:3])
    console.print(
        f"[yellow]{count} page(s) could not be written by the model[/yellow] "
        f"and kept a placeholder rendered from structure.\n{lines}"
        "  Run [bold]repowise generate[/bold] to write just those pages."
    )


def upgrade_to_full(
    repo_path: Path,
    *,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    concurrency: int,
    yes: bool = False,
) -> None:
    """Take the repo to a full index, reusing the persisted graph.

    A ``--mode fast`` index has no pages and an ESSENTIAL git tier, both of
    which this fills in. An index-only repo has a full git tier and
    documentation built from code, which a model run replaces with written
    pages. With no model configured the documentation is built from code: no
    model calls and no spend.
    """
    from repowise.cli.ui import load_dotenv
    from repowise.core.generation import GenerationConfig

    load_dotenv(repo_path)
    state = load_state(repo_path)
    if not state:
        raise click.ClickException(
            f"No existing index found at {repo_path}. Run `repowise init` first."
        )

    cfg = load_config(repo_path)
    head = get_head_commit(repo_path)
    start = time.monotonic()

    provider = _resolve_upgrade_provider(provider_name, model, repo_path, state)
    keyless = provider is None

    config = GenerationConfig.from_repo_config(
        cfg,
        deterministic=keyless,
        max_concurrency=concurrency,
        language=cfg.get("language", "en"),
        reasoning=resolve_reasoning(reasoning, cfg),
        enable_onboarding=bool(cfg.get("enable_onboarding", True)),
        # A whole-repo selection, so the file-page cap chosen at init applies
        # here; without it this run would grow the file layer back.
        max_file_pages=resolve_max_file_pages(config=cfg),
    )

    exclude_patterns = list(cfg.get("exclude_patterns") or [])
    commit_limit = cfg.get("commit_limit")
    follow_renames = bool(cfg.get("follow_renames", False))

    _announce_upgrade(repo_path, provider)
    embedder_name = _keyless_embedder_name(cfg) if keyless else _upgrade_embedder_name(cfg)

    # The same single-flight lock as an incremental update: a concurrent
    # `repowise update` must not race this run's save_state. If another update
    # holds it, defer the same way; that update rolls forward to HEAD.
    existing_lock = try_acquire_update_lock(repo_path, head)
    if existing_lock is not None:
        from repowise.cli.helpers import write_update_pending

        write_update_pending(repo_path, head)
        raise click.ClickException(
            "Another `repowise update` is already running on this repo. "
            "The full upgrade is deferred; it will run on the next pass."
        )

    # Released when this function returns (success, cost-gate Abort or failure),
    # not at process exit.
    try:
        provider_block = _provider_block(
            provider,
            embedder_name,
            reused=provider_name is None and model is None and _persisted(provider, state, cfg),
        )
        _start_transition(repo_path, state, cfg, provider_block)
        try:
            upgrade_result = run_async(
                _run_upgrade(
                    repo_path,
                    _template_provider() if keyless else provider,
                    config,
                    exclude_patterns=exclude_patterns,
                    commit_limit=commit_limit,
                    follow_renames=follow_renames,
                    embedder_name=embedder_name,
                    yes=yes,
                    checkpoint=_upgrade_checkpoint(repo_path, cfg, provider_block),
                )
            )
            generated_pages, total_pages, outcome = _unpack_upgrade_result(upgrade_result, cfg)
        except click.Abort:
            # Declined at the cost gate. The git backfill is kept (free to keep,
            # costly to redo) and the docs mode is left alone, so the repo keeps
            # whatever wiki it already had.
            _mark_transition(repo_path, cfg, status="resumable", retryable=True)
            console.print("[yellow]Nothing generated.[/yellow] The prior index remains usable.")
            return
        except BaseException as exc:
            _mark_transition(
                repo_path,
                cfg,
                status="resumable"
                if isinstance(exc, (KeyboardInterrupt, SystemExit))
                else "failed",
                retryable=True,
                error=type(exc).__name__,
            )
            raise

        _finish_upgrade_state(
            state,
            cfg,
            head=head,
            config=config,
            commit_limit=commit_limit,
            total_pages=total_pages,
            outcome=outcome,
            provider_block=provider_block,
        )
        save_state(repo_path, state, full_index=True)
        if embedder_name and embedder_name != cfg.get("embedder"):
            from repowise.cli.helpers import save_config_partial

            # `embedding_model=None` clears a model pinned for the old embedder;
            # passing `embedder` alone does not.
            save_config_partial(repo_path, embedder=embedder_name, embedding_model=None)

        _report_upgrade(time.monotonic() - start, generated_pages, outcome, keyless=keyless)
    finally:
        release_update_lock(repo_path)


def _resolve_upgrade_provider(
    provider_name: str | None, model: str | None, repo_path: Path, state: dict[str, Any]
) -> Any | None:
    """The model to write with, or None to build the documentation from code.

    None only when nothing names a model and none is configured anywhere. A repo
    whose documentation a model wrote keeps the error instead: building from
    code would replace that writing.
    """
    from repowise.core.docs_mode import resolve_docs_mode

    try:
        return resolve_provider(provider_name, model, repo_path=repo_path)
    except click.ClickException as exc:
        unconfigured = getattr(exc, "reason", None) == "no_provider_configured"
        if provider_name is not None or model is not None or not unconfigured:
            raise
        if resolve_docs_mode(state) == "llm":
            exc.message = (
                "This repo's documentation was written by a model, and no model is "
                f"configured to rewrite it. {exc.message}"
            )
            raise
        return None


def _announce_upgrade(repo_path: Path, provider: Any | None) -> None:
    console.print(f"[bold]{FAST_TO_FULL_COMMAND}[/bold]: upgrading {repo_path}")
    if provider is None:
        console.print(
            "No model configured: documentation is built from code "
            "[dim](no model calls, no spend)[/dim]."
        )
        return
    console.print(
        f"Provider: [cyan]{provider.provider_name}[/cyan] / "
        f"[cyan]{provider.model_name}[/cyan]. This writes documentation for the whole repo."
    )


def _persisted(provider: Any | None, state: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """Whether *provider* is the one this repo already recorded."""
    return provider is not None and (
        (state.get("provider") or cfg.get("provider")) == provider.provider_name
        and (state.get("model") or cfg.get("model")) == provider.model_name
    )


def _provider_block(provider: Any | None, embedder_name: str | None, *, reused: bool) -> dict:
    """The scope's provider record; a keyless run names no provider and no cost."""
    return {
        "name": provider.provider_name if provider is not None else None,
        "model": provider.model_name if provider is not None else None,
        "embedder": embedder_name,
        "reused": reused,
        "model_cost_possible": provider is not None,
    }


def _template_provider() -> Any:
    from repowise.core.providers.llm.template import TemplateProvider

    return TemplateProvider()


def _upgrade_embedder_name(cfg: dict[str, Any]) -> str | None:
    """The embedder to write with: re-resolved when the index recorded the mock.

    Index-only promises no spend, so it records the mock embedder. The user is
    paying a model here, and the mock would leave the written wiki invisible to
    semantic search, so re-resolve; the answer is recorded for later updates.
    """
    from repowise.cli.providers import model_run_embedder

    embedder_name, stated, _replaced = model_run_embedder(cfg.get("embedder"))
    console.print(f"Embedder: [cyan]{stated}[/cyan].")
    return embedder_name


def _keyless_embedder_name(cfg: dict[str, Any]) -> str:
    """The embedder a keyless upgrade uses: the one the user chose, else the free mock."""
    from repowise.cli.providers import keyless_run_embedder

    embedder_name, stated = keyless_run_embedder(cfg.get("embedder"))
    console.print(f"Embedder: [cyan]{stated}[/cyan].")
    return embedder_name


def _report_upgrade(
    elapsed: float, generated_pages: list[Any], outcome: dict[str, Any], *, keyless: bool = False
) -> None:
    how = "built from code" if keyless else "written"
    console.print(
        f"[bold green]Upgrade complete[/bold green] in {elapsed:.1f} s: "
        f"{len(generated_pages)} documentation pages {how}, full git history."
    )
    _report_placeholders(outcome.get("stub_fallbacks", 0), outcome.get("stub_reasons", []))
    if outcome["search"]["semantic"] != "available":
        console.print(
            "[yellow]Semantic search is off.[/yellow] Run "
            f"[bold]{outcome['search']['next_command'] or 'repowise reindex'}[/bold] to turn it on "
            "(embedding calls only, no model calls)."
        )
