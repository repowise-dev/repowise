"""``repowise status`` — show sync state and page counts."""

from __future__ import annotations

import contextlib
from datetime import UTC
from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import (
    CommandTarget,
    console,
    db_configured,
    get_db_url_for_repo,
    get_repowise_dir,
    load_state,
    reconcile_schema_best_effort,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.cli.ui.brand import format_bytes
from repowise.core.docs_mode import resolve_docs_mode

# ---------------------------------------------------------------------------
# Workspace status
# ---------------------------------------------------------------------------


def _index_storage_bytes(repowise_dir: Path) -> int:
    """Return total on-disk size of a repo's ``.repowise/`` directory."""
    if not repowise_dir.is_dir():
        return 0
    total = 0
    for path in repowise_dir.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total


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
        # Without this a store one repowise older reports 0 files / 0 pages:
        # the `no such column` is swallowed by the caller's except.
        await reconcile_schema_best_effort(url)
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
    if not db_path.exists() and not db_configured():
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
        await reconcile_schema_best_effort(url)
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
    if not db_path.exists() and not db_configured():
        return 0
    try:
        return run_async(_query())
    except Exception:
        return 0


async def _query_pages(repo_path: Path) -> tuple[dict[str, int], int]:
    """Page counts per type and total page tokens for *repo_path*."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )

    url = get_db_url_for_repo(repo_path)
    await reconcile_schema_best_effort(url)
    engine = create_engine(url)
    sf = create_session_factory(engine)

    counts: dict[str, int] = {}
    total_tokens = 0
    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return counts, total_tokens
            # Aggregated in SQL rather than by counting a fetched list. This
            # read `list_pages(..., limit=10000)`, and `list_pages` is the
            # paginated listing helper whose limit defaults to 100 — so the
            # per-type table stopped at exactly 10000 pages and the token total
            # with it, while the header above printed the true count from a
            # different query. A wiki over the cap disagreed with itself.
            from sqlalchemy import func, select

            from repowise.core.persistence.models import Page

            rows = await session.execute(
                select(
                    Page.page_type,
                    func.count(),
                    func.sum(func.coalesce(Page.input_tokens, 0)),
                    func.sum(func.coalesce(Page.output_tokens, 0)),
                )
                .where(
                    Page.repository_id == repo.id,
                    Page.freshness_status != "tombstone",
                )
                .group_by(Page.page_type)
            )
            for page_type, count, in_tokens, out_tokens in rows.all():
                counts[page_type] = counts.get(page_type, 0) + count
                total_tokens += int(in_tokens or 0) + int(out_tokens or 0)
    finally:
        await engine.dispose()
    return counts, total_tokens


def _query_health(repo_path: Path) -> dict | None:
    """Health figures for ``repowise status``, or ``None`` when there are none.

    Split from :func:`_query_health_line` so the json mode reports the numbers
    rather than a string with rich markup in it.
    """
    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists() and not db_configured():
        return None

    async def _q() -> dict | None:
        from repowise.core.analysis.health.scoring import hotspot_health
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import (
            get_health_metrics,
            get_health_summary,
            get_hotspot_file_paths,
            get_repository_by_path,
        )

        url = get_db_url_for_repo(repo_path)
        await reconcile_schema_best_effort(url)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        try:
            async with get_session(sf) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return None
                # Load the metrics once and hand them to the summary. Without
                # the ``metrics=`` handoff the summary reads the whole table
                # itself, so this function used to read it twice per
                # ``repowise status`` (and recompute the findings aggregate and
                # the worst-first sort twice with it).
                metrics = await get_health_metrics(session, repo.id)
                summary = await get_health_summary(session, repo.id, metrics=metrics)
                if summary["file_count"] == 0:
                    return None
                # Hotspot health from the one owner. This used to average the
                # top 25% of files by NLOC, which ranks size rather than churn;
                # it is the same wrong definition ``get_overview`` carried, so
                # the two agreed with each other and with no other surface.
                hotspot_paths = await get_hotspot_file_paths(session, repo.id)
                return {**summary, "hotspot_health": hotspot_health(metrics, hotspot_paths)}
        finally:
            await engine.dispose()

    try:
        return run_async(_q()) or None
    except Exception:
        return None


def _query_health_line(repo_path: Path) -> str | None:
    """One-line health summary for ``repowise status``.

    Returns ``None`` when no health data exists yet so the caller can
    skip the line silently. Format matches plan §4 P4.10:

        Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: payments/processor.ts)
    """
    data = _query_health(repo_path)
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
    # Performance leads with the finding COUNT (the honest signal); the bounded
    # /10 average trails in parens as a summary, never as a verification claim.
    perf = data.get("performance_average")
    perf_findings = data.get("performance_findings", 0)
    perf_part = (
        f" · {perf_findings} perf finding{'s' if perf_findings != 1 else ''} ({perf:.1f})"
        if perf is not None
        else ""
    )
    # ``None`` when the repo has no hotspot files, so the segment is dropped
    # rather than printing a 10.0 that would read as "your hotspots are
    # perfect" when there are none to score.
    hotspot = data.get("hotspot_health")
    hotspot_part = f"{hotspot:.1f} (hotspots) · " if hotspot is not None else ""
    return (
        f"[bold]Health:[/bold] {data['average_health']:.1f} (avg) "
        f"[[{band_color}]{BAND_LABEL[band]}[/{band_color}]] · "
        f"{hotspot_part}"
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


def _workspace_rows(target: CommandTarget) -> list[dict]:
    """One plain-data row per workspace repo, before any rendering.

    Split out so json mode reports the underlying values (a byte count, a
    boolean, a commit) rather than the table's cells, which carry rich markup
    and pre-formatted strings like ``3d ago``.

    Every row carries every key. An unindexed repo has nothing to report and
    leaves the measured fields ``None``, rather than omitting them and making
    each consumer branch on ``indexed`` before it may read a field.
    """
    from repowise.core.workspace import check_repo_staleness

    ws_root = target.ws_root
    ws_config = target.ws_config
    assert ws_root is not None and ws_config is not None

    rows: list[dict] = []
    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        repowise_dir = abs_path / ".repowise"
        row: dict = {
            "alias": entry.alias,
            "path": str(abs_path),
            "primary": entry.alias == ws_config.default_repo,
            "indexed": repowise_dir.exists(),
            "indexed_at": entry.indexed_at,
            "files": None,
            "symbols": None,
            "pages": None,
            "docs_mode": None,
            "storage_bytes": None,
            "head": None,
            "stale": None,
            "commits_behind": None,
        }
        if not row["indexed"]:
            rows.append(row)
            continue

        file_count, symbol_count = _query_repo_counts(abs_path)
        page_count = _query_page_count(abs_path)
        is_stale, current_head, behind = check_repo_staleness(
            abs_path, entry.last_commit_at_index
        )
        row.update(
            files=file_count,
            symbols=symbol_count,
            pages=page_count,
            docs_mode=resolve_docs_mode(load_state(abs_path)),
            storage_bytes=_index_storage_bytes(repowise_dir),
            head=current_head,
            stale=is_stale,
            commits_behind=behind,
        )
        rows.append(row)
    return rows


def _workspace_status(target: CommandTarget, fmt: str = "table") -> None:
    """Show status for all repos in a workspace."""
    ws_root = target.ws_root
    ws_config = target.ws_config
    if ws_root is None or ws_config is None:
        notice_console(fmt).print(
            "[yellow]No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' first.[/yellow]"
        )
        if fmt == "json":
            emit_json({"workspace": None, "repos": []})
        return

    rows = _workspace_rows(target)

    if fmt == "json":
        emit_json(
            {
                "workspace": {
                    "name": ws_root.name,
                    "path": str(ws_root),
                    "default_repo": ws_config.default_repo,
                    "repo_count": len(rows),
                    "indexed_count": sum(1 for r in rows if r["indexed"]),
                    "stale_count": sum(1 for r in rows if r["stale"]),
                },
                "repos": rows,
            }
        )
        return

    table = Table(title=f"Workspace: {ws_root.name}")
    table.add_column("Repo", style="cyan", min_width=16)
    table.add_column("Files", justify="right")
    table.add_column("Symbols", justify="right")
    table.add_column("Docs", justify="right")
    table.add_column("Storage", justify="right")
    table.add_column("Indexed", style="dim")
    table.add_column("HEAD", style="dim")
    table.add_column("Status")

    total_stale = 0
    no_docs: list[str] = []  # aliases with index but no generated pages

    for row in rows:
        label = row["alias"]
        if row["primary"]:
            label += " [bold](primary)[/bold]"

        if not row["indexed"]:
            table.add_row(label, "-", "-", "-", "-", "-", "-", "[yellow]not indexed[/yellow]")
            continue

        page_count = row["pages"]
        docs_mode = row["docs_mode"]

        # One axis in the Docs column: every wiki is complete, and the only
        # question is whether the subsystem pages carry written prose or are
        # still stubs. `repowise generate` writes the stubs.
        if page_count > 0:
            if docs_mode == "deterministic":
                docs_cell = f"[cyan]{page_count} · stubs[/cyan]"
            else:
                docs_cell = f"[green]{page_count} · prose[/green]"
        elif docs_mode == "none":
            docs_cell = "[yellow]None[/yellow]"
            no_docs.append(row["alias"])
        else:
            docs_cell = "[yellow]0[/yellow]"
            no_docs.append(row["alias"])

        behind = row["commits_behind"]
        if row["stale"] and behind > 0:
            status = f"[yellow]{behind} new commit(s)[/yellow]"
            total_stale += 1
        elif row["stale"]:
            status = "[yellow]stale[/yellow]"
            total_stale += 1
        elif row["files"] > 0:
            status = "[green]up to date[/green]"
        else:
            status = "[yellow]empty[/yellow]"

        table.add_row(
            label,
            str(row["files"]),
            f"{row['symbols']:,}",
            docs_cell,
            format_bytes(row["storage_bytes"]),
            _format_relative_time(row["indexed_at"]),
            (row["head"] or "-")[:7],
            status,
        )

    console.print(table)

    # Summary line
    indexed = sum(1 for r in rows if r["indexed"])
    summary = f"\n  {indexed}/{len(rows)} repos indexed. Default: {ws_config.default_repo}"
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
@format_option()
def status_command(path: str | None, workspace: bool, no_workspace: bool, fmt: str) -> None:
    """Show wiki sync state and page statistics.

    Auto-detects workspace mode when invoked from a workspace root.
    """
    notices = notice_console(fmt)

    target = resolve_command_target(
        path=path,
        workspace_flag=workspace,
        no_workspace_flag=no_workspace,
    )
    target.notice(notices, command="status")

    if target.is_workspace:
        _workspace_status(target, fmt)
        return

    repo_path = target.repo_path
    assert repo_path is not None
    repowise_dir = get_repowise_dir(repo_path)

    if not repowise_dir.exists():
        notices.print(
            "[yellow]No .repowise/ directory found. Run 'repowise init' first.[/yellow]"
        )
        if fmt == "json":
            emit_json({"repo": str(repo_path), "indexed": False})
        return

    state = load_state(repo_path)
    storage_bytes = _index_storage_bytes(repowise_dir)
    db_path = repowise_dir / "wiki.db"
    has_db = db_path.exists() or db_configured()

    if fmt == "json":
        counts, total_db_tokens = (
            run_async(_query_pages(repo_path)) if has_db else ({}, 0)
        )
        emit_json(
            {
                "repo": str(repo_path),
                "indexed": True,
                "state": {
                    "last_sync_commit": state.get("last_sync_commit"),
                    "total_pages": state.get("total_pages", 0),
                    "provider": state.get("provider"),
                    "model": state.get("model"),
                    "total_tokens": state.get("total_tokens", 0),
                    "storage_bytes": storage_bytes,
                },
                "database_found": has_db,
                "pages_by_type": counts,
                "page_total": sum(counts.values()),
                "page_tokens": total_db_tokens,
                "health": _query_health(repo_path),
            }
        )
        return

    # State table. Printed before the page query runs, not after: the query is
    # unguarded, and on a wiki.db whose schema predates the current models it
    # raises. Ordering it first means a reader still gets the sync state that
    # tells them the index is old.
    state_table = Table(title="Sync State")
    state_table.add_column("Key", style="cyan")
    state_table.add_column("Value")
    state_table.add_row("Last sync commit", state.get("last_sync_commit", "—") or "—")
    state_table.add_row("Total pages", str(state.get("total_pages", 0)))
    state_table.add_row("Provider", state.get("provider", "—") or "—")
    state_table.add_row("Model", state.get("model", "—") or "—")
    state_table.add_row("Total tokens", f"{state.get('total_tokens', 0):,}")
    state_table.add_row("Index storage", format_bytes(storage_bytes))
    console.print(state_table)

    if not has_db:
        console.print(f"[yellow]Database not found at {db_path}.[/yellow]")
        return

    counts, total_db_tokens = run_async(_query_pages(repo_path))
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
