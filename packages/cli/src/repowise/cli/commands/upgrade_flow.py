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

import dataclasses
import time
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import (
    console,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_reasoning,
    run_async,
    save_state,
)


def _reparse(repo_path: Path, exclude_patterns: list[str]) -> tuple[list[Any], dict[str, bytes], Any]:
    """Parse files for ASTs + source bytes WITHOUT building/resolving the graph.

    The graph is rehydrated from SQL separately; here we only need the parsed
    files and raw source the generator consumes. Skipping ``GraphBuilder.build``
    is the whole point — that resolution pass is what the fast index already did.
    """
    from repowise.core.ingestion import ASTParser, FileTraverser

    # Honor the persisted submodule semantics of the original index — a
    # fast index built with --include-submodules must not drop submodule
    # files from the docs re-parse (missing key → False, legacy behavior).
    state = load_state(repo_path)
    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
    )
    file_infos = list(traverser.traverse())
    repo_structure = traverser.get_repo_structure()

    parser = ASTParser()
    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    for fi in file_infos:
        try:
            source = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            parsed_files.append(parsed)
            source_map[fi.path] = source
        except Exception:
            pass  # unreadable / unparseable files are skipped, as in init
    return parsed_files, source_map, repo_structure


async def _backfill_git(
    sf: Any,
    repo_id: str,
    repo_path: Path,
    *,
    commit_limit: int | None,
    follow_renames: bool,
) -> dict[str, dict]:
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
        summary, git_results = await backfill_full_tier(
            indexer, repo_id, job_store=job_store
        )
        if git_results:
            await upsert_git_metadata_bulk(session, repo_id, git_results)
            await recompute_git_percentiles(session, repo_id)
        # Persist the per-commit rows captured during the FULL-tier walk so the
        # commits/change-risk surface lands on an ESSENTIAL→FULL promotion too
        # (Foundation 1 only wrote them on the full orchestrator index).
        if summary.commit_rows:
            await upsert_git_commits_bulk(session, repo_id, summary.commit_rows)

    console.print(
        f"Git tier upgraded to FULL: [cyan]{summary.files_indexed}[/cyan] files "
        "(per-file blame + co-change)."
    )
    return {m["file_path"]: m for m in git_results if m.get("file_path")}


async def _run_upgrade(
    repo_path: Path,
    provider: Any,
    config: Any,
    *,
    exclude_patterns: list[str],
    commit_limit: int | None,
    follow_renames: bool,
) -> list[Any]:
    """Drive the full upgrade and return the generated pages."""
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page_from_generated,
        upsert_repository,
    )
    from repowise.core.pipeline import rehydrate_graph_builder, run_generation

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    # 1. Repo row FIRST — this is the fix for the deferred CLI job_store
    # wiring: pipeline_jobs.repository_id is an FK to repositories.id, so the
    # row must exist before the backfill creates its checkpoint job.
    async with get_session(sf) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        repo_id = repo.id

    # 2. Backfill the git tier ESSENTIAL -> FULL (resumable via JobStore).
    git_meta_map = await _backfill_git(
        sf,
        repo_id,
        repo_path,
        commit_limit=commit_limit,
        follow_renames=follow_renames,
    )

    # 3. Rehydrate the graph from SQL — no parse, no resolution, no recompute.
    async with get_session(sf) as session:
        graph_builder = await rehydrate_graph_builder(session, repo_id, repo_path)

    # 4. Re-parse for ASTs + source (the only unavoidable re-work). The graph
    # is NOT rebuilt — generation traverses the rehydrated SQL graph.
    parsed_files, source_map, repo_structure = _reparse(repo_path, exclude_patterns)
    console.print(
        f"Re-parsed [cyan]{len(parsed_files)}[/cyan] files for doc generation "
        "(graph reused from index — not re-resolved)."
    )

    # 5. Generate the docs the fast index skipped. Honor the cost-tracking
    # opt-out (issue #326) so REPOWISE_NO_COST_TRACKING is respected here too;
    # an in-memory tracker still powers the live cost readout.
    from repowise.cli.providers import cost_tracking_disabled

    if cost_tracking_disabled():
        cost_tracker = CostTracker()
    else:
        # buffered=True defers cost INSERTs to a single post-generation flush so
        # they never contend with the generation writer (issue #326).
        cost_tracker = CostTracker(session_factory=sf, repo_id=repo_id, buffered=True)
    provider._cost_tracker = cost_tracker
    generated_pages = await run_generation(
        repo_path=repo_path,
        parsed_files=parsed_files,
        source_map=source_map,
        graph_builder=graph_builder,
        repo_structure=repo_structure,
        git_meta_map=git_meta_map,
        llm_client=provider,
        embedder=None,
        vector_store=None,
        concurrency=config.max_concurrency,
        progress=None,
        cost_tracker=cost_tracker,
        generation_config=config,
    )

    # Flush buffered cost rows now generation is done (best-effort).
    await cost_tracker.flush()

    # 6. Persist pages + a GenerationJob marker, then build the FTS index.
    async with get_session(sf) as session:
        for page in generated_pages:
            await upsert_page_from_generated(session, page, repo_id)
        try:
            from datetime import UTC, datetime

            from repowise.core.persistence.crud import upsert_generation_job

            now = datetime.now(UTC)
            job = await upsert_generation_job(
                session,
                repository_id=repo_id,
                status="completed",
                total_pages=len(generated_pages),
                config={"mode": "upgrade", "source": "cli_update_full"},
            )
            job.completed_pages = len(generated_pages)
            job.started_at = now
            job.finished_at = now
        except Exception:
            pass  # job recording is best-effort

    try:
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page in generated_pages:
            await fts.index(page.page_id, page.title, page.content)
    except Exception:
        pass  # FTS indexing is best-effort

    # 7. Recompute + persist code health against the now-FULL git tier.
    # The fast index persisted ESSENTIAL-tier findings only, so the blame /
    # co-change biomarkers (function_hotspot, code_age_volatility,
    # hidden_coupling) were no-ops. Now that the backfill has promoted the git
    # tier and rehydrated the graph, re-run the full-repo health pass so those
    # biomarkers land — otherwise the upgrade leaves the health tables frozen
    # at the fast index's ESSENTIAL state. Mirrors what `init` / `update` do.
    try:
        from repowise.core.persistence.crud import (
            save_health_findings,
            save_health_metrics,
            save_health_snapshot,
        )
        from repowise.core.pipeline.orchestrator import _run_health_analysis

        health_report = await _run_health_analysis(
            graph_builder,
            git_meta_map,
            parsed_files,
            repo_path=repo_path,
            progress=None,
        )
        if health_report is not None:
            async with get_session(sf) as session:
                await save_health_metrics(session, repo_id, health_report.metrics or [])
                if health_report.findings:
                    await save_health_findings(session, repo_id, health_report.findings)
                kpis = health_report.kpis or {}
                try:
                    await save_health_snapshot(
                        session,
                        repo_id,
                        hotspot_health=float(kpis.get("hotspot_health", 10.0)),
                        average_health=float(kpis.get("average_health", 10.0)),
                        worst_performer_path=kpis.get("worst_performer_path"),
                        worst_performer_score=kpis.get("worst_performer_score"),
                        per_file_scores={
                            m.file_path: round(float(m.score), 2)
                            for m in health_report.metrics or []
                        },
                    )
                except Exception:
                    pass  # snapshot is best-effort
            console.print(
                f"Code health recomputed at FULL tier: "
                f"[cyan]{len(health_report.findings)}[/cyan] findings."
            )
    except Exception as exc:
        console.print(f"[yellow]Health recompute skipped: {exc}[/yellow]")

    await engine.dispose()
    return generated_pages


def upgrade_to_full(
    repo_path: Path,
    *,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    concurrency: int,
) -> None:
    """Upgrade a fast (index-only / ESSENTIAL git) index to a full one.

    Backfills the git tier and generates the LLM docs the fast index skipped,
    reusing the persisted graph instead of rebuilding it.
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

    config = GenerationConfig(
        max_concurrency=concurrency,
        language=cfg.get("language", "en"),
        reasoning=resolve_reasoning(reasoning, cfg),
        enable_onboarding=bool(cfg.get("enable_onboarding", True)),
    )
    tier1_top_n = cfg.get("tier1_top_n")
    if tier1_top_n is not None:
        config = dataclasses.replace(config, tier1_top_n=tier1_top_n)

    exclude_patterns = list(cfg.get("exclude_patterns") or [])
    commit_limit = cfg.get("commit_limit")
    follow_renames = bool(cfg.get("follow_renames", False))

    console.print(f"[bold]repowise update --full[/bold] — upgrading {repo_path}")
    console.print(
        f"Provider: [cyan]{provider.provider_name}[/cyan] / "
        f"[cyan]{provider.model_name}[/cyan]. This generates docs for the whole repo."
    )

    generated_pages = run_async(
        _run_upgrade(
            repo_path,
            provider,
            config,
            exclude_patterns=exclude_patterns,
            commit_limit=commit_limit,
            follow_renames=follow_renames,
        )
    )

    # Flip persisted state to full so subsequent `repowise update` runs the
    # normal incremental LLM path (docs_enabled) rather than offering upgrade.
    state["last_sync_commit"] = head
    state["docs_enabled"] = True
    state["git_tier"] = "full"
    state["total_pages"] = len(generated_pages)
    save_state(repo_path, state)

    elapsed = time.monotonic() - start
    console.print(
        f"[bold green]Upgrade complete[/bold green] in {elapsed:.1f}s — "
        f"{len(generated_pages)} pages generated, git tier now FULL."
    )
