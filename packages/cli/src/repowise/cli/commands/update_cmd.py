"""``repowise update`` — incremental wiki regeneration for changed files."""

from __future__ import annotations

import time

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_repo_path,
    run_async,
    save_state,
)


@click.command("update")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--since", default=None, help="Base git ref to diff from (overrides state).")
@click.option("--cascade-budget", type=int, default=30, help="Max pages to regenerate per run.")
@click.option("--dry-run", is_flag=True, default=False, help="Show affected pages without regenerating.")
def update_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    since: str | None,
    cascade_budget: int,
    dry_run: bool,
) -> None:
    """Incrementally update wiki pages for files changed since last sync."""
    start = time.monotonic()
    repo_path = resolve_repo_path(path)
    ensure_repowise_dir(repo_path)

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

    if dry_run:
        console.print("[yellow]Dry run — no pages regenerated.[/yellow]")
        return

    # Re-parse changed files and rebuild graph for affected pages
    from pathlib import Path as PathlibPath

    from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    provider = resolve_provider(provider_name, model, repo_path=repo_path)
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

    # Re-index git metadata for changed files
    git_meta_map: dict[str, dict] = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        git_indexer = GitIndexer(repo_path)
        changed_paths = [fd.path for fd in file_diffs]
        updated_meta = run_async(git_indexer.index_changed_files(changed_paths))
        git_meta_map = {m["file_path"]: m for m in updated_meta}
        graph_builder.update_co_change_edges(git_meta_map)
    except Exception as exc:
        console.print(f"[yellow]Git re-index skipped: {exc}[/yellow]")

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

    # Determine affected pages
    affected = detector.get_affected_pages(file_diffs, graph_builder.graph(), cascade_budget)

    console.print(f"Pages to regenerate: [cyan]{len(affected.regenerate)}[/cyan]")
    if affected.decay_only:
        console.print(f"Pages to decay: [yellow]{len(affected.decay_only)}[/yellow]")

    # Generate affected pages
    assembler = ContextAssembler(config)
    generator = PageGenerator(provider, assembler, config)
    repo_name = repo_path.name

    generated_pages = run_async(
        generator.generate_all(
            parsed_files,
            source_map,
            graph_builder,
            repo_structure,
            repo_name,
        )
    )

    # Persist
    async def _persist() -> None:
        from repowise.core.persistence import (
            FullTextSearch,
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_page_from_generated,
            upsert_repository,
        )

        from repowise.cli.helpers import get_db_url_for_repo

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_name, local_path=str(repo_path))
            repo_id = repo.id
            for page in generated_pages:
                await upsert_page_from_generated(session, page, repo_id)

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

        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page in generated_pages:
            await fts.index(page.page_id, page.title, page.content)

        await engine.dispose()

    run_async(_persist())

    # Update state
    state["last_sync_commit"] = head
    state["total_pages"] = state.get("total_pages", 0) + len(generated_pages)
    save_state(repo_path, state)

    elapsed = time.monotonic() - start
    console.print(
        f"[bold green]Updated {len(generated_pages)} pages in {elapsed:.1f}s[/bold green]"
    )
