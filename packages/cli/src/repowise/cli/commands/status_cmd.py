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
    load_config,
    load_state,
    reconcile_schema_best_effort,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.cli.ui.brand import format_bytes
from repowise.cli.ui.mode_names import (
    INDEX_ONLY,
    commit_cap_label,
    mode_label,
    pages,
    scope_line,
)
from repowise.cli.ui.result_panels import keyless_upgrade_note
from repowise.core.decision_lanes import lanes_label, pending_decision_lanes
from repowise.core.docs_mode import resolve_docs_mode
from repowise.core.index_scope import (
    fast_to_full_command,
    model_command,
    resolve_index_scope,
    upgrade_command,
)

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
        from sqlalchemy import select as sa_select

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import count_files, count_symbols
        from repowise.core.persistence.models import Repository

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

                return (
                    await count_files(session, repo_id),
                    await count_symbols(session, repo_id),
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
                if summary["file_count"] == 0 and not summary.get("unanalysed_file_count"):
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
    if data["average_health"] is None:
        # Every file is in a language health has no dialect for.
        return (
            f"[bold]Health:[/bold] not analysed · {data['unanalysed_file_count']} files in "
            "languages health does not support yet"
        )
    worst_path = data["worst_performer_path"] or "n/a"
    worst_score = data["worst_performer_score"]
    from repowise.core.analysis.health.grading import (
        BAND_LABEL,
        BAND_TERMINAL_COLOR,
        analysed_note,
        format_score,
        headline_band,
    )

    worst_repr = format_score(worst_score) if worst_score is not None else "—"

    band = headline_band(data["average_health"], data.get("analysed_share"))
    if band is not None:
        band_color = BAND_TERMINAL_COLOR[band]
        band_part = f" [[{band_color}]{BAND_LABEL[band]}[/{band_color}]]"
    else:
        band_part = ""
    note = analysed_note(data["file_count"], data.get("unanalysed_file_count") or 0)
    note_part = f" · {note}" if note else ""
    # Maintainability and performance are co-surfaced pillars; show each when the
    # split has populated it (None on indexes that predate the relevant work).
    maint = data.get("maintainability_average")
    maint_part = f" · {format_score(maint)} (maintainability)" if maint is not None else ""
    # Performance leads with the causes worth fixing now (the honest signal);
    # the bounded /10 average trails in parens, never as a verification claim.
    perf = data.get("performance_average")
    causes = data.get("performance_causes")
    perf_part = (
        f" · {causes['now']} of {causes['production']} production perf causes "
        f"worth fixing now ({format_score(perf)})"
        if perf is not None and causes
        else ""
    )
    # ``None`` when the repo has no hotspot files, so the segment is dropped
    # rather than printing a 10.0 that would read as "your hotspots are
    # perfect" when there are none to score.
    hotspot = data.get("hotspot_health")
    hotspot_part = f"{format_score(hotspot)} (hotspots) · " if hotspot is not None else ""
    return (
        f"[bold]Health:[/bold] {format_score(data['average_health'])} (avg)"
        f"{band_part}{note_part} · "
        f"{hotspot_part}"
        f"{worst_repr} (worst: {worst_path})"
        f"{maint_part}"
        f"{perf_part}"
    )


def _rescore_pending(state: dict) -> list[str]:
    """Which stored results the next ``repowise update`` redoes for a newer analyzer."""
    from repowise.cli.commands.update_cmd.persistence import (
        health_analyzer_changed,
        security_scanner_changed,
    )

    pending = ["health"] if health_analyzer_changed(state) else []
    return pending + (["security"] if security_scanner_changed(state) else [])


_RESCORE_LINES = {
    ("health",): "Health was scored by an older analyzer; the next repowise update re-scores it.",
    ("security",): (
        "Security findings came from an older scanner; the next repowise update rescans them."
    ),
    ("health", "security"): (
        "Health and security results came from an older analyzer and scanner; "
        "the next repowise update redoes both."
    ),
}


#: The resume command an interrupted ``init`` leaves for the user.
_RESUME_COMMAND = "repowise init --resume"


def _incomplete_index(repo_path: Path, state: dict) -> dict | None:
    """Why the index is unfinished, or ``None``.

    A live ``init`` holds its lock for the whole run. Without one, ``init``
    writes state.json last, with ``index_scope`` (and ``last_sync_commit`` in a
    git repo), so persisted graph rows without either are a run that stopped
    before it finished.
    """
    from datetime import datetime

    from repowise.core.update_lock import read_init_lock

    lock = read_init_lock(repo_path)
    if lock is not None:
        started = datetime.fromtimestamp(lock["started_at"], UTC).isoformat()
        return {"reason": "running", "started_at": started, "next_command": "repowise status"}
    finished = state.get("last_sync_commit") or "index_scope" in state
    if finished or not _query_repo_counts(repo_path)[0]:
        return None
    return {"reason": "interrupted", "next_command": _RESUME_COMMAND}


def _incomplete_line(incomplete: dict) -> str:
    """The "Next" line for an index that is still running or was interrupted."""
    command = f"[bold]{incomplete['next_command']}[/bold]"
    if incomplete["reason"] == "running":
        started = _format_relative_time(incomplete["started_at"])
        return (
            f"[bold]Next:[/bold] Index in progress (started {started}). "
            f"Wait for it to finish, then run {command}."
        )
    return f"[bold]Next:[/bold] Index incomplete (interrupted). Run {command}."


def _shows_upgrade_row(scope: dict) -> bool:
    """Whether the "Full upgrade" row says something the rest of status does not.

    A finished index built from code stores ``pending`` for the optional model
    run; the keyless note under the table already names that command, and a
    bare "pending" reads as unfinished work.
    """
    status = scope["upgrade"]["status"]
    if status in ("unknown", "not_applicable", "complete"):
        return False
    return not (status == "pending" and scope["content_provenance"] == "template")


def _load_actions(repo_path: Path) -> dict | None:
    """The stored next-actions view, or ``None``; never raises (see ``next_cmd``)."""
    try:
        from repowise.cli.commands.next_cmd import load_view

        return load_view(repo_path)
    except Exception:
        return None


def _next_actions_summary(view: dict | None) -> dict | None:
    """Counts only, for the json mode; ``repowise next --format json`` has the rows."""
    if view is None:
        return None
    return {
        "anchor": view.get("anchor"),
        **{
            name: {"total": h.get("total", 0), "by_tier": h.get("by_tier", {})}
            for name, h in view["horizons"].items()
        },
        "unavailable": sorted(view.get("unavailable") or {}),
    }


def _next_actions_lines(view: dict | None) -> list[str]:
    """The "Next" block: where things stand and the first thing worth doing."""
    if view is None:
        return []
    from rich.markup import escape

    from repowise.cli.commands.next_cmd import default_horizon, render_title, sentence

    # The week's sentence names both windows when the week is quiet; the row
    # under it comes from whichever window ``repowise next`` would open on.
    lines = [f"[bold]Next:[/bold] {escape(sentence(view, 'week'))}"]
    horizon = default_horizon(view)
    top = next(
        (a for a in view["horizons"][horizon]["actions"] if a["tier"] in ("act_now", "plan")),
        None,
    )
    if top is not None:
        lines.append(f"  {render_title(top['title'])}")
        lines.append("  [dim]Run [bold]repowise next[/bold] for the list.[/dim]")
    return lines


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
    from repowise.core.workspace.update import sync_workspace_state_from_disk

    ws_root = target.ws_root
    ws_config = target.ws_config
    assert ws_root is not None and ws_config is not None
    # A member updated on its own leaves the config behind its store.
    sync_workspace_state_from_disk(ws_root, ws_config, save_if_changed=False)

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
            "index_scope": None,
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
        repo_state = load_state(abs_path)
        row.update(
            files=file_count,
            symbols=symbol_count,
            pages=page_count,
            docs_mode=resolve_docs_mode(repo_state),
            index_scope=resolve_index_scope(repo_state, {}),
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
    table.add_column("Documentation", justify="right")
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

        # One axis: who wrote the subsystem pages. `repowise generate` has a
        # model write the ones built from code.
        if page_count > 0:
            colour = "cyan" if docs_mode == "deterministic" else "green"
            docs_cell = (
                f"[{colour}]{pages(page_count)} · {mode_label(row['index_scope'])}[/{colour}]"
            )
        elif docs_mode == "none":
            docs_cell = f"[yellow]{INDEX_ONLY}[/yellow]"
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
            f"[yellow]Note:[/yellow] {len(no_docs)} repo(s) have no documentation: "
            f"[cyan]{', '.join(no_docs)}[/cyan]"
        )
        first = no_docs[0]
        console.print(
            f"  Run [bold]{fast_to_full_command(first)}[/bold] "
            "to build them (no key needed; a configured model writes them)."
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
    scope = resolve_index_scope(state, load_config(repo_path))
    storage_bytes = _index_storage_bytes(repowise_dir)
    db_path = repowise_dir / "wiki.db"
    has_db = db_path.exists() or db_configured()
    incomplete = _incomplete_index(repo_path, state)
    pending_lanes = pending_decision_lanes(state, repo_path)
    # An unfinished init has no stamps yet; the resume line covers it.
    rescore = _rescore_pending(state) if incomplete is None else []

    if fmt == "json":
        counts, total_db_tokens = (
            run_async(_query_pages(repo_path)) if has_db else ({}, 0)
        )
        emit_json(
            {
                "repo": str(repo_path),
                "indexed": True,
                "incomplete": incomplete,
                "state": {
                    "last_sync_commit": state.get("last_sync_commit"),
                    "total_pages": state.get("total_pages", 0),
                    "provider": state.get("provider"),
                    "model": state.get("model"),
                    # The live rows, as the table shows: only init stamps state's.
                    "total_tokens": total_db_tokens if counts else state.get("total_tokens", 0),
                    "storage_bytes": storage_bytes,
                },
                "database_found": has_db,
                "pages_by_type": counts,
                "page_total": sum(counts.values()),
                "page_tokens": total_db_tokens,
                "health": _query_health(repo_path),
                "next_actions": _next_actions_summary(_load_actions(repo_path) if has_db else None),
                "index_scope": scope,
                "upgrade_command": upgrade_command(scope),
                "model_command": model_command(scope),
                "decision_lanes_pending": list(pending_lanes),
                "rescore_pending": rescore,
            }
        )
        return

    # The page query runs first so the state table can show its totals, but
    # its error is held until the state table is printed: on a wiki.db whose
    # schema predates the current models it raises, and a reader should still
    # get the sync state that tells them the index is old.
    counts: dict[str, int] = {}
    total_db_tokens = 0
    page_query_error: Exception | None = None
    if has_db:
        try:
            counts, total_db_tokens = run_async(_query_pages(repo_path))
        except Exception as exc:
            page_query_error = exc

    # Totals from the live rows when the query worked. state.json counts
    # tombstoned pages too, and its token total is stamped only by init, so
    # beside the per-type table below it disagreed with it.
    total_pages = sum(counts.values()) if counts else state.get("total_pages", 0)
    total_tokens = total_db_tokens if counts else state.get("total_tokens", 0)
    state_table = Table(title="Sync State")
    state_table.add_column("Key", style="cyan")
    state_table.add_column("Value")
    state_table.add_row("Last sync commit", state.get("last_sync_commit", "—") or "—")
    state_table.add_row("Total pages", str(total_pages))
    state_table.add_row("Provider", state.get("provider", "—") or "—")
    state_table.add_row("Model", state.get("model", "—") or "—")
    state_table.add_row("Total tokens", f"{total_tokens:,}")
    state_table.add_row("Index storage", format_bytes(storage_bytes))
    state_table.add_row("Scope", scope_line(scope))
    coverage = scope["git_history_coverage"]
    if coverage:
        measured = coverage.get(
            "files_with_history", coverage.get("files_measured", coverage.get("files_indexed", "?"))
        )
        eligible = coverage.get("files_eligible", coverage.get("eligible_files", "?"))
        cap = commit_cap_label(scope["git_commit_cap"])
        state_table.add_row(
            "Git coverage", f"{measured}/{eligible} files" + (f" ({cap})" if cap else "")
        )
    fp = scope["file_pages"]
    if fp["eligible"] is not None:
        configured = "unlimited" if fp["configured_cap"] == 0 else fp["configured_cap"]
        if configured is None:
            configured = "auto"
        effective = "unlimited" if fp["effective_cap"] is None else fp["effective_cap"]
        state_table.add_row(
            "File pages",
            f"{fp['generated']}/{fp['eligible']} generated · {fp['omitted']} omitted · "
            f"cap {effective} (configured {configured})",
        )
    if scope["analysis"]["unavailable"]:
        state_table.add_row(
            "Unavailable analysis",
            ", ".join(scope["analysis"]["unavailable"]),
        )
    if scope["analysis"]["skipped"]:
        state_table.add_row("Intentionally skipped", ", ".join(scope["analysis"]["skipped"]))
    if scope["test_run"]:
        state_table.add_row("Test run", "documentation covers the top 10 files only")
    if _shows_upgrade_row(scope):
        state_table.add_row("Full upgrade", scope["upgrade"]["status"])
    if pending_lanes:
        state_table.add_row(
            "Decisions",
            f"{lanes_label(pending_lanes)} not read yet (needs a model): {model_command(scope)}",
        )
    console.print(state_table)

    # A template-only scope is a finished keyless index, not a broken one —
    # name the upgrade commands here too, since "Full upgrade  pending" above
    # names no command on its own (#3105). Mirrors the init closing note.
    if scope["content_provenance"] == "template":
        note = keyless_upgrade_note(
            scope["search"]["semantic"] == "available", scope["search"]["next_command"]
        )
        console.print(f"[dim]{note}[/dim]")
    elif upgrade_command(scope):
        console.print(
            f"[dim]Index only: no documentation yet. {upgrade_command(scope)} builds it from "
            "code and fills in the full git history (no key needed).[/dim]"
        )

    if not has_db:
        console.print(f"[yellow]Database not found at {db_path}.[/yellow]")
        return

    if page_query_error is not None:
        raise page_query_error
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
    if rescore:
        if not health_line:
            console.print()
        console.print(f"[dim]{_RESCORE_LINES[tuple(rescore)]}[/dim]")

    if incomplete is not None:
        next_lines = [_incomplete_line(incomplete)]
    else:
        next_lines = _next_actions_lines(_load_actions(repo_path))
    if next_lines:
        console.print()
        for line in next_lines:
            console.print(line)
