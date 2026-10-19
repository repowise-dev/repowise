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
from typing import Any

import click

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
    stamp_offered_slots,
)
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.docs_mode import docs_mode_state_fields
from repowise.core.index_scope import file_page_scope, stamp_index_scope
from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY
from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT
from repowise.core.update_lock import release_update_lock, try_acquire_update_lock


def _gate_cost(
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    provider: Any,
    repo_path: Path,
    *,
    yes: bool,
) -> None:
    """Print the estimated spend and confirm past the gate. Raises on decline.

    Best-effort on the estimate itself: a failure there prints the reason and
    lets the run proceed rather than blocking an upgrade on a number that is
    advisory anyway.
    """
    try:
        from repowise.cli.commands.init_cmd.generation import (
            COST_GATE_USD,
            cost_gate_declined,
            format_cost,
            structural_page_summary,
        )
        from repowise.core.cost_estimator import build_generation_plan, estimate_cost

        plans = build_generation_plan(parsed_files, graph_builder, config)
        est = estimate_cost(plans, provider.provider_name, provider.model_name, repo_path=repo_path)
    except Exception as exc:
        console.print(f"[yellow]Cost estimate unavailable ({exc}); continuing.[/yellow]")
        return

    pages = sum(p.count for p in plans)
    console.print(f"Estimated: [bold]{pages}[/bold] pages, [bold]{format_cost(est)}[/bold].")
    structural = structural_page_summary(plans)
    if structural:
        console.print(f"  [dim]{structural}[/dim]")

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

    async with get_session(sf) as session:
        graph_builder = await rehydrate_graph_builder(session, repo_id, repo_path)

    # 4. Re-parse for ASTs + source (the only unavoidable re-work). The graph
    # is NOT rebuilt — generation traverses the rehydrated SQL graph.
    parsed_files, source_map, repo_structure = _reparse(repo_path, exclude_patterns)
    # The health pass below reads attributes SQL does not store.
    graph_builder.restore_parse_only_attrs(parsed_files)
    console.print(
        f"Re-parsed [cyan]{len(parsed_files)}[/cyan] files for doc generation "
        "(graph reused from index — not re-resolved)."
    )

    # Show the bill before running it, as init does: the user reaching for
    # `--full` has never been shown a cost for this repo.
    _gate_cost(parsed_files, graph_builder, config, provider, repo_path, yes=yes)

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
    from repowise.core.generation.selection import count_documentable_files

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
                "semantic": "available" if vector_store is not None else "unavailable",
                "next_command": "repowise reindex" if vector_store is None else None,
            },
            "embedding_error": embedding_error,
            "stub_fallbacks": stub_fallbacks,
            "stub_reasons": stub_reasons,
        },
    )


async def _open_repository(repo_path: Path) -> tuple[Any, Any, Any]:
    """Open the repo DB and upsert its repository row; returns engine, factory, id.

    The row comes FIRST: pipeline_jobs.repository_id is an FK to repositories.id,
    so it must exist before the backfill creates its checkpoint job.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
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
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from repowise.cli.commands.init_cmd._generation_persist import (
        run_generation_with_persistence,
    )
    from repowise.cli.ui import (
        BRAND_STYLE,
        OK,
        OWL_SPINNER,
        MaybeCountColumn,
        RichProgressCallback,
    )

    parsed_files, source_map, graph_builder, repo_structure, git_meta_map = inputs
    with Progress(
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        TextColumn(f"[{OK}]${{task.fields[cost]:.3f}}[/]"),
        console=console,
    ) as upgrade_progress:
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


#: The resumable stages of a full upgrade, in run order.
_UPGRADE_STAGES = ["git_backfill", "generation", "search", "health"]


def _completed_stages(state: dict[str, Any]) -> list[str]:
    return list(
        state.get("full_upgrade", {}).get("completed_stages", [])
        if isinstance(state.get("full_upgrade"), dict)
        else []
    )


def _next_stage(stages: list[str]) -> str:
    return next((item for item in _UPGRADE_STAGES if item not in stages), "finalize")


def upgrade_to_full(
    repo_path: Path,
    *,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    concurrency: int,
    yes: bool = False,
) -> None:
    """Write the repo's wiki with a model, reusing the persisted graph.

    Two repos arrive here. A ``--mode fast`` index has no pages and an
    ESSENTIAL git tier, both of which this fills in. An ``--index-only`` repo
    has a full git tier and a wiki rendered from templates, and this replaces
    those pages with written ones.
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

    # Provider is required — the fast index made no LLM calls, so the repo may
    # not have one configured yet. resolve_provider surfaces a clear error.
    provider = resolve_provider(provider_name, model, repo_path=repo_path)
    persisted_provider = state.get("provider") or cfg.get("provider")
    persisted_model = state.get("model") or cfg.get("model")
    provider_reused = (
        provider_name is None
        and model is None
        and persisted_provider == provider.provider_name
        and persisted_model == provider.model_name
    )

    config = GenerationConfig.from_repo_config(
        cfg,
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

    console.print(f"[bold]repowise update --full[/bold] — upgrading {repo_path}")
    console.print(
        f"Provider: [cyan]{provider.provider_name}[/cyan] / "
        f"[cyan]{provider.model_name}[/cyan]. This generates docs for the whole repo."
    )
    embedder_name = _upgrade_embedder_name(cfg)

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
        provider_block = {
            "name": provider.provider_name,
            "model": provider.model_name,
            "embedder": embedder_name,
            "reused": provider_reused,
            "model_cost_possible": True,
        }
        _start_transition(repo_path, state, cfg, provider, embedder_name, provider_block)
        try:
            upgrade_result = run_async(
                _run_upgrade(
                    repo_path,
                    provider,
                    config,
                    exclude_patterns=exclude_patterns,
                    commit_limit=commit_limit,
                    follow_renames=follow_renames,
                    embedder_name=embedder_name,
                    yes=yes,
                    checkpoint=_upgrade_checkpoint(repo_path, cfg, provider, embedder_name),
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
            provider=provider,
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

        _report_upgrade(time.monotonic() - start, generated_pages, outcome)
    finally:
        release_update_lock(repo_path)


def _upgrade_embedder_name(cfg: dict[str, Any]) -> str | None:
    """The embedder to write with: re-resolved when the index recorded the mock.

    Index-only promises no spend, so it records the mock embedder. The user is
    paying a model here, and the mock would leave the written wiki invisible to
    semantic search, so re-resolve; the answer is recorded for later updates.
    """
    embedder_name = cfg.get("embedder")
    if not embedder_name or embedder_name == "mock":
        from repowise.cli.providers import resolve_embedder

        resolved = resolve_embedder(None)
        if resolved != (embedder_name or "mock"):
            console.print(f"Embedder: [cyan]{resolved}[/cyan] (was mock, index-only's default).")
            embedder_name = resolved
    return embedder_name


def _start_transition(
    repo_path: Path,
    state: dict[str, Any],
    cfg: dict[str, Any],
    provider: Any,
    embedder_name: str | None,
    provider_block: dict[str, Any],
) -> None:
    """Record the running transition before any backfill or model work.

    A killed process then leaves an honest resumable transition while the prior
    index_scope keeps describing the usable index.
    """
    completed_stages = _completed_stages(state)
    state["full_upgrade"] = {
        "status": "running",
        "retryable": True,
        "completed_stages": completed_stages,
        "next_stage": _next_stage(completed_stages),
        "provider": provider.provider_name,
        "model": provider.model_name,
        "embedder": embedder_name,
    }
    stamp_index_scope(state, cfg, upgrade=state["full_upgrade"], provider=dict(provider_block))
    save_state(repo_path, state)


def _upgrade_checkpoint(
    repo_path: Path, cfg: dict[str, Any], provider: Any, embedder_name: str | None
) -> Any:
    """A callback that records each completed stage in state.json."""

    def _checkpoint(stage: str, **fields: Any) -> None:
        current = load_state(repo_path)
        stages = _completed_stages(current)
        if stage not in stages:
            stages.append(stage)
        current["full_upgrade"] = {
            "status": "running",
            "retryable": True,
            "completed_stages": stages,
            "next_stage": _next_stage(stages),
            "provider": provider.provider_name,
            "model": provider.model_name,
            "embedder": embedder_name,
        }
        if fields.get("git_history_coverage") is not None:
            current["git_history_coverage"] = fields["git_history_coverage"]
        stamp_index_scope(current, cfg, upgrade=current["full_upgrade"])
        save_state(repo_path, current)

    return _checkpoint


def _mark_transition(repo_path: Path, cfg: dict[str, Any], **updates: Any) -> None:
    """Update the recorded transition after the upgrade stopped early."""
    current = load_state(repo_path)
    transition = current.get("full_upgrade", {})
    transition.update(**updates)
    current["full_upgrade"] = transition
    stamp_index_scope(current, cfg, upgrade=transition)
    save_state(repo_path, current)


def _unpack_upgrade_result(
    upgrade_result: tuple[Any, ...], cfg: dict[str, Any]
) -> tuple[list[Any], int, dict[str, Any]]:
    """Pages, page count and outcome; a 2-tuple from a wrapper gets an unknown outcome."""
    if len(upgrade_result) != 2:
        generated_pages, total_pages, outcome = upgrade_result
        return generated_pages, total_pages, outcome
    generated_pages, total_pages = upgrade_result
    outcome = {
        "git_history_coverage": None,
        "file_pages": {
            "configured_cap": resolve_max_file_pages(config=cfg),
            "effective_cap": None,
            "eligible": None,
            "generated": None,
            "omitted": None,
        },
        "search": {
            "full_text": "unknown",
            "semantic": "unknown",
            "next_command": None,
        },
        "embedding_error": None,
    }
    return generated_pages, total_pages, outcome


def _finish_upgrade_state(
    state: dict[str, Any],
    cfg: dict[str, Any],
    *,
    head: str | None,
    provider: Any,
    config: Any,
    commit_limit: Any,
    total_pages: int,
    outcome: dict[str, Any],
    provider_block: dict[str, Any],
) -> None:
    """Flip the persisted state to full, so later updates take the normal LLM path."""
    state["last_sync_commit"] = head
    state.update(docs_mode_state_fields("llm"))
    state["git_tier"] = "full"
    state["run_mode"] = "standard"
    if outcome["git_history_coverage"] is not None:
        state["git_history_coverage"] = outcome["git_history_coverage"]
    state["total_pages"] = total_pages
    # Record who wrote the pages, or `status` reports them as unknown.
    state["provider"] = provider.provider_name
    state["model"] = provider.model_name
    # Every onboarding slot was evaluated against whole-repo signals, so the
    # missing-slot notice has nothing left to report.
    stamp_offered_slots(state, enabled=config.enable_onboarding)
    # Health and the git backfill ran over the whole repo under the current
    # analyzer and history rules, so the next update need not redo them.
    state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION
    state[GIT_HISTORY_VERSION_KEY] = GIT_HISTORY_VERSION
    state["full_upgrade"] = {
        "status": "complete",
        "retryable": False,
        "completed_stages": [*_UPGRADE_STAGES, "finalize"],
        "next_stage": None,
    }
    stamp_index_scope(
        state,
        cfg,
        run_mode="standard",
        content_provenance="model",
        git_tier="full",
        git_commit_cap=(commit_limit if isinstance(commit_limit, int) else _DEFAULT_COMMIT_LIMIT),
        git_history_coverage=outcome["git_history_coverage"],
        file_pages=outcome["file_pages"],
        analysis={"unavailable": [], "skipped": []},
        upgrade=state["full_upgrade"],
        provider=dict(provider_block),
        search=outcome["search"],
    )


def _report_upgrade(elapsed: float, generated_pages: list[Any], outcome: dict[str, Any]) -> None:
    console.print(
        f"[bold green]Upgrade complete[/bold green] in {elapsed:.1f}s — "
        f"{len(generated_pages)} pages generated, git tier now FULL."
    )
    _report_placeholders(outcome.get("stub_fallbacks", 0), outcome.get("stub_reasons", []))
    if outcome["search"]["semantic"] != "available":
        console.print(
            "[yellow]Semantic search is unavailable.[/yellow] Set an embedder key and run "
            "[bold]repowise reindex[/bold] (embedding calls only; no model calls)."
        )
