"""``repowise status`` — show sync state and page counts."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import (
    CommandTarget,
    console,
    get_db_url_for_repo,
    get_repowise_dir,
    load_state,
    resolve_command_target,
    run_async,
)

# ---------------------------------------------------------------------------
# Workspace status
# ---------------------------------------------------------------------------


def _query_repo_counts(repo_path: Path) -> tuple[int, int]:
    """Return ``(file_count, symbol_count)`` from a repo's DB."""

    async def _query() -> tuple[int, int]:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.models import GraphNode, Repository

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)

        try:
            async with get_session(sf) as session:
                repo_result = await session.execute(
                    sa_select(Repository.id).where(Repository.local_path == str(repo_path))
                )
                repo_id = repo_result.scalar_one_or_none()
                if repo_id is None:
                    return 0, 0

                # Count file nodes and symbol nodes
                file_count_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "file",
                    )
                )
                symbol_count_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "symbol",
                    )
                )
                return (
                    file_count_result.scalar_one(),
                    symbol_count_result.scalar_one(),
                )
        finally:
            await engine.dispose()

    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists():
        return 0, 0
    try:
        return run_async(_query())
    except Exception:
        return 0, 0


def _query_page_count(repo_path: Path) -> int:
    """Return the number of generated wiki pages for a repo, or 0."""

    async def _query() -> int:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.models import Page, Repository

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        try:
            async with get_session(sf) as session:
                repo_result = await session.execute(
                    sa_select(Repository.id).where(Repository.local_path == str(repo_path))
                )
                repo_id = repo_result.scalar_one_or_none()
                if repo_id is None:
                    return 0
                count_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(Page)
                    .where(Page.repository_id == repo_id)
                )
                return int(count_result.scalar_one() or 0)
        finally:
            await engine.dispose()

    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists():
        return 0
    try:
        return run_async(_query())
    except Exception:
        return 0


def _query_health_line(repo_path: Path) -> str | None:
    """One-line health summary for ``repowise status``.

    Returns ``None`` when no health data exists yet so the caller can
    skip the line silently. Format matches plan §4 P4.10:

        Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: payments/processor.ts)
    """
    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists():
        return None

    async def _q() -> dict | None:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import (
            get_health_metrics,
            get_health_summary,
            get_repository_by_path,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        try:
            async with get_session(sf) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return None
                summary = await get_health_summary(session, repo.id)
                if summary["file_count"] == 0:
                    return None
                metrics = await get_health_metrics(session, repo.id)
                # Hotspot health: NLOC-weighted avg of top-25% files by NLOC.
                if metrics:
                    by_nloc = sorted(metrics, key=lambda m: m.nloc or 0, reverse=True)
                    top = by_nloc[: max(1, len(by_nloc) // 4)]
                    tot = sum(max(m.nloc, 1) for m in top)
                    hotspot = (
                        sum(m.score * max(m.nloc, 1) for m in top) / tot
                        if tot
                        else summary["average_health"]
                    )
                else:
                    hotspot = summary["average_health"]
                return {**summary, "hotspot_health": round(hotspot, 2)}
        finally:
            await engine.dispose()

    try:
        data = run_async(_q())
    except Exception:
        return None
    if not data:
        return None
    worst_path = data["worst_performer_path"] or "n/a"
    worst_score = data["worst_performer_score"]
    worst_repr = f"{worst_score:.1f}" if worst_score is not None else "—"
    from repowise.core.analysis.health.grading import BAND_LABEL, band_for

    band = band_for(float(data["average_health"]))
    band_color = {"healthy": "green", "warning": "yellow", "alert": "red"}[band]
    # Maintainability and performance are co-surfaced pillars; show each when the
    # split has populated it (None on indexes that predate the relevant work).
    maint = data.get("maintainability_average")
    maint_part = f" · {maint:.1f} (maintainability)" if maint is not None else ""
    perf = data.get("performance_average")
    perf_part = f" · {perf:.1f} (performance)" if perf is not None else ""
    return (
        f"[bold]Health:[/bold] {data['average_health']:.1f} (avg) "
        f"[[{band_color}]{BAND_LABEL[band]}[/{band_color}]] · "
        f"{data['hotspot_health']:.1f} (hotspots) · "
        f"{worst_repr} (worst: {worst_path})"
        f"{maint_part}"
        f"{perf_part}"
    )


def _format_relative_time(iso_timestamp: str | None) -> str:
    """Format an ISO 8601 timestamp as a relative time string."""
    if not iso_timestamp:
        return "-"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except Exception:
        return iso_timestamp[:10] if len(iso_timestamp) >= 10 else iso_timestamp


def _workspace_status(target: CommandTarget) -> None:
    """Show status for all repos in a workspace."""
    from repowise.core.workspace import check_repo_staleness

    ws_root = target.ws_root
    ws_config = target.ws_config
    if ws_root is None or ws_config is None:
        console.print(
            "[yellow]No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' first.[/yellow]"
        )
        return

    table = Table(title=f"Workspace: {ws_root.name}")
    table.add_column("Repo", style="cyan", min_width=16)
    table.add_column("Files", justify="right")
    table.add_column("Symbols", justify="right")
    table.add_column("Docs", justify="right")
    table.add_column("Indexed", style="dim")
    table.add_column("HEAD", style="dim")
    table.add_column("Status")

    total_stale = 0
    no_docs: list[str] = []  # aliases with index but no generated pages

    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        repowise_dir = abs_path / ".repowise"
        label = entry.alias
        if entry.alias == ws_config.default_repo:
            label += " [bold](primary)[/bold]"

        if not repowise_dir.exists():
            table.add_row(label, "-", "-", "-", "-", "-", "[yellow]not indexed[/yellow]")
            continue

        file_count, symbol_count = _query_repo_counts(abs_path)
        indexed_ago = _format_relative_time(entry.indexed_at)
        page_count = _query_page_count(abs_path)
        docs_state = load_state(abs_path)
        docs_enabled = docs_state.get("docs_enabled")

        # Render the Docs column in plain English so the user instantly
        # knows whether the LLM-generated wiki exists for this repo.
        if page_count > 0:
            docs_cell = f"[green]{page_count}[/green]"
        elif docs_enabled is False:
            docs_cell = "[yellow]skipped[/yellow]"
            no_docs.append(entry.alias)
        else:
            docs_cell = "[yellow]0[/yellow]"
            no_docs.append(entry.alias)

        # Check staleness by comparing stored commit to current HEAD
        stored_commit = entry.last_commit_at_index
        is_stale, current_head, behind = check_repo_staleness(abs_path, stored_commit)
        head_short = (current_head or "-")[:7]

        if is_stale and behind > 0:
            status = f"[yellow]{behind} new commit(s)[/yellow]"
            total_stale += 1
        elif is_stale:
            status = "[yellow]stale[/yellow]"
            total_stale += 1
        elif file_count > 0:
            status = "[green]up to date[/green]"
        else:
            status = "[yellow]empty[/yellow]"

        table.add_row(
            label,
            str(file_count),
            f"{symbol_count:,}",
            docs_cell,
            indexed_ago,
            head_short,
            status,
        )

    console.print(table)

    # Summary line
    total_repos = len(ws_config.repos)
    indexed = sum(1 for e in ws_config.repos if (ws_root / e.path / ".repowise").exists())
    summary = f"\n  {indexed}/{total_repos} repos indexed. Default: {ws_config.default_repo}"
    if total_stale:
        summary += f". [yellow]{total_stale} stale[/yellow]"
    console.print(summary)

    # Honest "no docs" tip — print the exact remediation command so the
    # user never has to dig through docs to figure out what to do next.
    if no_docs:
        console.print()
        console.print(
            f"[yellow]Note:[/yellow] {len(no_docs)} repo(s) have no generated docs: "
            f"[cyan]{', '.join(no_docs)}[/cyan]"
        )
        first = no_docs[0]
        console.print(
            f"  Run [bold]repowise update --repo {first} --docs[/bold] "
            "to generate them (requires an LLM provider)."
        )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("status")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (show all repos in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def status_command(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Show wiki sync state and page statistics.

    Auto-detects workspace mode when invoked from a workspace root.
    """
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace,
        no_workspace_flag=no_workspace,
    )
    target.notice(console, command="status")

    if target.is_workspace:
        _workspace_status(target)
        return

    repo_path = target.repo_path
    assert repo_path is not None
    repowise_dir = get_repowise_dir(repo_path)

    if not repowise_dir.exists():
        console.print("[yellow]No .repowise/ directory found. Run 'repowise init' first.[/yellow]")
        return

    state = load_state(repo_path)

    # State table
    state_table = Table(title="Sync State")
    state_table.add_column("Key", style="cyan")
    state_table.add_column("Value")
    state_table.add_row("Last sync commit", state.get("last_sync_commit", "—") or "—")
    state_table.add_row("Total pages", str(state.get("total_pages", 0)))
    state_table.add_row("Provider", state.get("provider", "—") or "—")
    state_table.add_row("Model", state.get("model", "—") or "—")
    state_table.add_row("Total tokens", f"{state.get('total_tokens', 0):,}")
    console.print(state_table)

    # Page counts from DB
    db_path = repowise_dir / "wiki.db"
    if not db_path.exists():
        console.print("[yellow]Database not found.[/yellow]")
        return

    async def _query_pages():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_repository_by_path,
            get_session,
            list_pages,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)

        counts: dict[str, int] = {}
        total_tokens = 0

        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                await engine.dispose()
                return counts, total_tokens
            pages = await list_pages(session, repo.id, limit=10000)
            for p in pages:
                counts[p.page_type] = counts.get(p.page_type, 0) + 1
                total_tokens += (p.input_tokens or 0) + (p.output_tokens or 0)

        await engine.dispose()
        return counts, total_tokens

    counts, total_db_tokens = run_async(_query_pages())

    if counts:
        pages_table = Table(title="Pages by Type")
        pages_table.add_column("Page Type", style="cyan")
        pages_table.add_column("Count", justify="right")
        for ptype, count in sorted(counts.items()):
            pages_table.add_row(ptype, str(count))
        pages_table.add_section()
        pages_table.add_row("[bold]Total[/bold]", f"[bold]{sum(counts.values())}[/bold]")
        pages_table.add_row("Total tokens", f"{total_db_tokens:,}")
        console.print(pages_table)

    health_line = _query_health_line(repo_path)
    if health_line:
        console.print()
        console.print(health_line)
