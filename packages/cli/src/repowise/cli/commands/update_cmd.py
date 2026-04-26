"""``repowise update`` — incremental wiki regeneration for changed files."""

from __future__ import annotations

import time

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    find_workspace_root,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_repo_path,
    run_async,
    save_state,
)


# ---------------------------------------------------------------------------
# Workspace update flow
# ---------------------------------------------------------------------------


def _workspace_update(
    start_path: "Path",
    *,
    repo_alias: str | None = None,
    dry_run: bool = False,
) -> None:
    """Update stale repos in a workspace."""
    from pathlib import Path

    from repowise.core.workspace import WorkspaceConfig, check_repo_staleness, update_workspace

    ws_root = find_workspace_root(start_path)
    if ws_root is None:
        raise click.ClickException(
            "No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' first."
        )

    ws_config = WorkspaceConfig.load(ws_root)

    # Show staleness summary first
    console.print(f"[bold]repowise update[/bold] — workspace: {ws_root.name}")
    console.print()

    stale_count = 0
    for entry in ws_config.repos:
        if repo_alias and entry.alias != repo_alias:
            continue
        abs_path = (ws_root / entry.path).resolve()
        stored = entry.last_commit_at_index
        is_stale, head, behind = check_repo_staleness(abs_path, stored)
        status = f"[yellow]{behind} new commit(s)[/yellow]" if is_stale else "[green]up to date[/green]"
        if not (abs_path / ".repowise").is_dir():
            status = "[dim]not indexed[/dim]"
        console.print(f"  {entry.alias:<20} {status}")
        if is_stale:
            stale_count += 1

    console.print()

    if stale_count == 0:
        console.print("[green]All repos are up to date.[/green]")
        return

    if dry_run:
        console.print(f"[yellow]Dry run — {stale_count} repo(s) would be updated.[/yellow]")
        return

    # Run the updates
    def _on_start(alias: str) -> None:
        console.print(f"  Updating [bold]{alias}[/bold]...")

    def _on_done(result: "RepoUpdateResult") -> None:
        if result.error:
            console.print(f"    [red]\u2717 {result.alias}: {result.error}[/red]")
        elif result.updated:
            console.print(
                f"    [green]\u2713[/green] {result.alias}: "
                f"{result.file_count} files, {result.symbol_count:,} symbols"
            )

    from repowise.core.workspace import RepoUpdateResult

    results = run_async(
        update_workspace(
            ws_root,
            ws_config,
            repo_filter=repo_alias,
            dry_run=False,
            on_repo_start=_on_start,
            on_repo_done=_on_done,
        )
    )

    # Summary
    updated = sum(1 for r in results if r.updated)
    errors = sum(1 for r in results if r.error)
    skipped = sum(1 for r in results if r.skipped_reason)
    console.print()
    console.print(
        f"[bold]Done:[/bold] {updated} updated, {skipped} skipped"
        + (f", {errors} errors" if errors else "")
    )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("update")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--since", default=None, help="Base git ref to diff from (overrides state).")
@click.option(
    "--cascade-budget",
    type=int,
    default=None,
    help="Max pages to regenerate (auto-scaled if unset).",
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show affected pages without regenerating."
)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Update all stale repos in the workspace.",
)
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Update only this repo alias within the workspace.",
)
def update_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    since: str | None,
    cascade_budget: int | None,
    dry_run: bool,
    workspace: bool,
    repo_alias: str | None,
) -> None:
    """Incrementally update wiki pages for files changed since last sync."""
    start = time.monotonic()
    repo_path = resolve_repo_path(path)

    # --- Workspace mode ---
    if workspace or repo_alias:
        _workspace_update(repo_path, repo_alias=repo_alias, dry_run=dry_run)
        return

    ensure_repowise_dir(repo_path)

    # Load saved API keys from .repowise/.env (won't overwrite existing env vars)
    from repowise.cli.ui import load_dotenv

    load_dotenv(repo_path)

    state = load_state(repo_path)
    base_ref = since or state.get("last_sync_commit")
    head = get_head_commit(repo_path)

    if base_ref is None:
        raise click.ClickException(
            "No previous sync found. Run 'repowise init' first or pass --since."
        )

    if head and head == base_ref:
        console.print("[green]Already up to date.[/green]")
        return

    console.print(f"[bold]repowise update[/bold] — {repo_path}")
    console.print(f"Diffing [cyan]{base_ref[:8]}..{(head or 'HEAD')[:8]}[/cyan]")

    from repowise.core.ingestion import ChangeDetector

    detector = ChangeDetector(repo_path)
    file_diffs = detector.get_changed_files(base_ref, head or "HEAD")

    if not file_diffs:
        console.print("[green]No changed files detected.[/green]")
        save_state(repo_path, {**state, "last_sync_commit": head})
        return

    console.print(f"Changed files: [yellow]{len(file_diffs)}[/yellow]")

    # Show changed files
    for fd in file_diffs:
        status_color = {"added": "green", "deleted": "red", "modified": "yellow", "renamed": "blue"}
        color = status_color.get(fd.status, "white")
        console.print(f"  [{color}]{fd.status:>10}[/{color}]  {fd.path}")

    # Re-parse changed files and rebuild graph for affected pages
    from pathlib import Path as PathlibPath

    from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    config = GenerationConfig()

    # Read exclude patterns from config (set during init or via web UI)
    repo_config = load_config(repo_path)
    exclude_patterns: list[str] = list(repo_config.get("exclude_patterns") or [])

    # Full re-ingest for graph (needed for cascade analysis)
    traverser = FileTraverser(repo_path, extra_exclude_patterns=exclude_patterns or None)
    file_infos = list(traverser.traverse())
    repo_structure = traverser.get_repo_structure()

    parser = ASTParser()
    parsed_files = []
    source_map: dict[str, bytes] = {}
    graph_builder = GraphBuilder()

    for fi in file_infos:
        try:
            source = PathlibPath(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            parsed_files.append(parsed)
            source_map[fi.path] = source
            graph_builder.add_file(parsed)
        except Exception:
            pass
    graph_builder.build()

    # Add framework-aware synthetic edges (conftest, Django, FastAPI, Flask)
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
        fw_count = graph_builder.add_framework_edges([item.name for item in tech_items])
        if fw_count:
            console.print(f"Framework edges added: [cyan]{fw_count}[/cyan]")
    except Exception:
        pass  # framework edge detection is best-effort

    # Re-index git metadata for changed files
    git_meta_map: dict[str, dict] = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        _commit_limit = repo_config.get("commit_limit")
        _follow_renames = repo_config.get("follow_renames", False)
        git_indexer = GitIndexer(
            repo_path,
            commit_limit=_commit_limit,
            follow_renames=_follow_renames,
        )
        changed_paths = [fd.path for fd in file_diffs]
        updated_meta = run_async(git_indexer.index_changed_files(changed_paths))
        git_meta_map = {m["file_path"]: m for m in updated_meta}
        graph_builder.update_co_change_edges(git_meta_map)
    except Exception as exc:
        console.print(f"[yellow]Git re-index skipped: {exc}[/yellow]")

    # Determine affected pages (auto-scale budget if not explicitly set)
    if cascade_budget is None:
        from repowise.core.ingestion.change_detector import compute_adaptive_budget

        cascade_budget = compute_adaptive_budget(file_diffs, len(file_infos))
        console.print(f"Adaptive cascade budget: [cyan]{cascade_budget}[/cyan]")
    affected = detector.get_affected_pages(file_diffs, graph_builder.graph(), cascade_budget)

    console.print(f"Pages to regenerate: [cyan]{len(affected.regenerate)}[/cyan]")
    if affected.decay_only:
        console.print(f"Pages to decay: [yellow]{len(affected.decay_only)}[/yellow]")

    if dry_run:
        console.print("[yellow]Dry run — no pages regenerated.[/yellow]")
        return

    provider = resolve_provider(provider_name, model, repo_path=repo_path)

    # Run partial dead code analysis on affected files
    dead_code_report = None
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        analyzer = DeadCodeAnalyzer(graph_builder.graph(), git_meta_map)
        changed_paths = [fd.path for fd in file_diffs]
        dead_code_report = analyzer.analyze_partial(changed_paths)
        if dead_code_report.total_findings:
            console.print(
                f"Dead code findings (partial): [yellow]{dead_code_report.total_findings}[/yellow]"
            )
    except Exception as exc:
        console.print(f"[yellow]Dead code analysis skipped: {exc}[/yellow]")

    # Re-scan changed files for inline decision markers
    new_decision_markers: list = []
    try:
        from repowise.core.analysis.decision_extractor import DecisionExtractor

        changed_paths = [fd.path for fd in file_diffs if fd.status in ("added", "modified")]
        if changed_paths:
            extractor = DecisionExtractor(
                repo_path=repo_path,
                provider=provider,
                graph=graph_builder.graph(),
                git_meta_map=git_meta_map,
            )
            new_decision_markers = run_async(
                extractor.scan_inline_markers(restrict_to_files=changed_paths)
            )
            if new_decision_markers:
                console.print(
                    f"New decision markers found: [green]{len(new_decision_markers)}[/green]"
                )
    except Exception as exc:
        console.print(f"[yellow]Decision re-scan skipped: {exc}[/yellow]")

    # Filter to only affected files
    regen_set = set(affected.regenerate)
    affected_parsed = [pf for pf in parsed_files if pf.file_info.path in regen_set]
    affected_source = {p: s for p, s in source_map.items() if p in regen_set}

    # Generate affected pages
    assembler = ContextAssembler(config)
    generator = PageGenerator(provider, assembler, config)
    repo_name = repo_path.name

    generated_pages = run_async(
        generator.generate_all(
            affected_parsed,
            affected_source,
            graph_builder,
            repo_structure,
            repo_name,
            git_meta_map=git_meta_map,
        )
    )

    # Persist
    async def _persist() -> None:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            FullTextSearch,
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_page_from_generated,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_name, local_path=str(repo_path))
            repo_id = repo.id
            for page in generated_pages:
                await upsert_page_from_generated(session, page, repo_id)

        # Persist updated git metadata + recompute percentiles
        if git_meta_map:
            try:
                from repowise.core.persistence.crud import (
                    recompute_git_percentiles,
                    upsert_git_metadata_bulk,
                )

                async with get_session(sf) as session:
                    await upsert_git_metadata_bulk(
                        session,
                        repo_id,
                        list(git_meta_map.values()),
                    )
                    await recompute_git_percentiles(session, repo_id)
            except Exception:
                pass  # git persistence is best-effort

        # Decision records: persist new markers + recompute staleness
        try:
            if new_decision_markers:
                import dataclasses as _dc

                from repowise.core.persistence.crud import bulk_upsert_decisions

                async with get_session(sf) as session:
                    await bulk_upsert_decisions(
                        session,
                        repo_id,
                        [_dc.asdict(d) for d in new_decision_markers],
                    )

            if git_meta_map:
                from repowise.core.persistence.crud import recompute_decision_staleness

                async with get_session(sf) as session:
                    await recompute_decision_staleness(session, repo_id, git_meta_map)
        except Exception:
            pass  # never fail update due to decision processing

        # Persist dead code findings (partial)
        if dead_code_report and dead_code_report.findings:
            try:
                import dataclasses as _dc_dead

                from repowise.core.persistence.crud import save_dead_code_findings

                async with get_session(sf) as session:
                    await save_dead_code_findings(
                        session,
                        repo_id,
                        [_dc_dead.asdict(f) for f in dead_code_report.findings],
                    )
            except Exception:
                pass  # dead code persistence is best-effort

        # Record a GenerationJob so the web UI "last synced" timestamp updates
        try:
            from datetime import datetime, UTC as _UTC
            from repowise.core.persistence.crud import upsert_generation_job

            async with get_session(sf) as session:
                now = datetime.now(_UTC)
                page_count = len(generated_pages)
                job = await upsert_generation_job(
                    session,
                    repository_id=repo_id,
                    status="completed",
                    total_pages=page_count,
                    config={"mode": "incremental", "source": "cli_update"},
                )
                job.completed_pages = page_count
                job.started_at = now
                job.finished_at = now
        except Exception:
            pass  # job recording is best-effort

        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page in generated_pages:
            await fts.index(page.page_id, page.title, page.content)

        await engine.dispose()

    run_async(_persist())

    # ---- CLAUDE.md (best-effort) ----
    cfg = load_config(repo_path)
    if cfg.get("editor_files", {}).get("claude_md", True):
        try:
            from repowise.cli.helpers import get_db_url_for_repo
            from repowise.core.generation.editor_files import (
                ClaudeMdGenerator,
                EditorFileDataFetcher,
            )
            from repowise.core.persistence import (
                create_engine,
                create_session_factory,
                get_session,
                init_db,
            )
            from repowise.core.persistence.crud import get_repository_by_path

            async def _update_claude_md() -> None:
                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                await init_db(engine)
                sf = create_session_factory(engine)
                try:
                    async with get_session(sf) as session:
                        repo_rec = await get_repository_by_path(session, str(repo_path))
                        if repo_rec is None:
                            return
                        fetcher = EditorFileDataFetcher(session, repo_rec.id, repo_path)
                        data = await fetcher.fetch()
                finally:
                    await engine.dispose()
                ClaudeMdGenerator().write(repo_path, data)

            run_async(_update_claude_md())
        except Exception:
            pass  # CLAUDE.md update must never fail the update command

    # Update state
    state["last_sync_commit"] = head
    state["total_pages"] = state.get("total_pages", 0) + len(generated_pages)
    save_state(repo_path, state)

    # Trigger cross-repo hooks if this repo is part of a workspace
    try:
        ws_root = find_workspace_root(repo_path)
        if ws_root is not None:
            from repowise.core.workspace import WorkspaceConfig
            from repowise.core.workspace.update import run_cross_repo_hooks

            ws_config = WorkspaceConfig.load(ws_root)
            # Find this repo's alias in the workspace config
            from pathlib import Path as _P
            repo_abs = repo_path.resolve()
            alias = None
            for entry in ws_config.repos:
                if (ws_root / entry.path).resolve() == repo_abs:
                    alias = entry.alias
                    break
            if alias and len(ws_config.repos) >= 2:
                console.print("Running cross-repo analysis...")
                run_async(run_cross_repo_hooks(ws_config, ws_root, [alias]))
                console.print("[green]Cross-repo analysis updated.[/green]")
    except Exception:
        pass  # cross-repo hooks must never fail the update

    elapsed = time.monotonic() - start

    # Print generation report
    try:
        from repowise.core.generation.report import GenerationReport, render_report

        report = GenerationReport.from_pages(
            generated_pages,
            stale_count=len(affected.decay_only),
            decisions_count=len(new_decision_markers),
            elapsed=elapsed,
        )
        render_report(report, console)
    except Exception:
        # Fallback to simple message if report fails
        console.print(
            f"[bold green]Updated {len(generated_pages)} pages in {elapsed:.1f}s[/bold green]"
        )
