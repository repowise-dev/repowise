"""``repowise decision`` — manage architectural decision records."""

from __future__ import annotations

import json

import click
from rich.panel import Panel
from rich.table import Table

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_repo_path,
    run_async,
)


@click.group("decision")
def decision_group() -> None:
    """Manage architectural decision records."""


# ---------------------------------------------------------------------------
# decision add
# ---------------------------------------------------------------------------


@decision_group.command("add")
@click.argument("path", required=False, default=None)
def decision_add(path: str | None) -> None:
    """Interactively add a new architectural decision."""
    repo_path = resolve_repo_path(path)
    ensure_repowise_dir(repo_path)

    console.print("[bold]Add Architectural Decision[/bold]\n")

    title = click.prompt("Decision title (short)")
    context = click.prompt("Context (what forced this decision?)", default="")
    decision_text = click.prompt("Decision (what was chosen?)")
    rationale = click.prompt("Rationale (why?)", default="")

    alternatives_raw = click.prompt("Rejected alternatives (comma-separated, optional)", default="")
    alternatives = [a.strip() for a in alternatives_raw.split(",") if a.strip()]

    consequences_raw = click.prompt(
        "Tradeoffs/consequences (comma-separated, optional)", default=""
    )
    consequences = [c.strip() for c in consequences_raw.split(",") if c.strip()]

    affected_raw = click.prompt("Affected files/modules (comma-separated, optional)", default="")
    affected_files = [f.strip() for f in affected_raw.split(",") if f.strip()]

    tags_raw = click.prompt(
        "Tags (comma-separated: auth, database, api, performance, security, infra, testing)",
        default="",
    )
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    async def _persist() -> str:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_decision,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            rec = await upsert_decision(
                session,
                repository_id=repo.id,
                title=title,
                status="active",
                context=context,
                decision=decision_text,
                rationale=rationale,
                alternatives=alternatives,
                consequences=consequences,
                affected_files=affected_files,
                affected_modules=[],
                tags=tags,
                source="cli",
                confidence=1.0,
            )
            decision_id = rec.id

        await engine.dispose()
        return decision_id

    decision_id = run_async(_persist())
    console.print(f"\n[green]Decision recorded[/green] — ID: [bold]{decision_id[:8]}[/bold]")


# ---------------------------------------------------------------------------
# decision list
# ---------------------------------------------------------------------------


@decision_group.command("list")
@click.argument("path", required=False, default=None)
@click.option(
    "--status",
    type=click.Choice(["proposed", "active", "deprecated", "superseded", "all"]),
    default="all",
)
@click.option(
    "--source",
    type=click.Choice(["git_archaeology", "inline_marker", "readme_mining", "cli", "all"]),
    default="all",
)
@click.option("--proposed", is_flag=True, default=False, help="Show only proposed decisions.")
@click.option("--stale-only", is_flag=True, default=False, help="Show only stale decisions.")
def decision_list(
    path: str | None,
    status: str,
    source: str,
    proposed: bool,
    stale_only: bool,
) -> None:
    """List architectural decision records."""
    repo_path = resolve_repo_path(path)

    async def _query() -> list:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            list_decisions,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            decisions = await list_decisions(
                session,
                repo.id,
                status=status if status != "all" else ("proposed" if proposed else None),
                source=source if source != "all" else None,
                include_proposed=True,
                limit=100,
            )

        await engine.dispose()
        return decisions

    decisions = run_async(_query())

    if proposed:
        decisions = [d for d in decisions if d.status == "proposed"]
    if stale_only:
        decisions = [d for d in decisions if d.staleness_score >= 0.5]

    if not decisions:
        console.print("[dim]No decisions found.[/dim]")
        return

    table = Table(title="Architectural Decisions")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Title", max_width=40)
    table.add_column("Status")
    table.add_column("Source", style="dim")
    table.add_column("Conf.", justify="right")
    table.add_column("Stale", justify="right")
    table.add_column("Created", style="dim")

    status_colors = {
        "active": "green",
        "proposed": "yellow",
        "deprecated": "red",
        "superseded": "dim",
    }

    for d in decisions:
        color = status_colors.get(d.status, "white")
        stale_str = f"{d.staleness_score:.1f}" if d.staleness_score > 0 else "-"
        created = d.created_at.strftime("%Y-%m-%d") if d.created_at else ""
        table.add_row(
            d.id[:8],
            d.title[:40],
            f"[{color}]{d.status}[/{color}]",
            d.source,
            f"{d.confidence:.0%}",
            stale_str,
            created,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# decision show
# ---------------------------------------------------------------------------


@decision_group.command("show")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
def decision_show(decision_id: str, path: str | None) -> None:
    """Show full details of a decision record."""
    repo_path = resolve_repo_path(path)

    async def _query():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_decision,
            get_session,
            init_db,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            rec = await get_decision(session, decision_id)

        await engine.dispose()
        return rec

    rec = run_async(_query())
    if rec is None:
        console.print(f"[red]Decision not found: {decision_id}[/red]")
        return

    lines = [
        f"[bold]{rec.title}[/bold]",
        f"Status: {rec.status}  |  Source: {rec.source}  |  Confidence: {rec.confidence:.0%}",
        f"Staleness: {rec.staleness_score:.2f}",
        "",
    ]
    if rec.context:
        lines.append(f"[cyan]Context:[/cyan] {rec.context}")
    if rec.decision:
        lines.append(f"[cyan]Decision:[/cyan] {rec.decision}")
    if rec.rationale:
        lines.append(f"[cyan]Rationale:[/cyan] {rec.rationale}")

    alternatives = json.loads(rec.alternatives_json)
    if alternatives:
        lines.append("[cyan]Alternatives rejected:[/cyan]")
        for a in alternatives:
            lines.append(f"  - {a}")

    consequences = json.loads(rec.consequences_json)
    if consequences:
        lines.append("[cyan]Consequences:[/cyan]")
        for c in consequences:
            lines.append(f"  - {c}")

    affected = json.loads(rec.affected_files_json)
    if affected:
        lines.append(f"[cyan]Affected files:[/cyan] {', '.join(affected[:10])}")

    tags = json.loads(rec.tags_json)
    if tags:
        lines.append(f"[cyan]Tags:[/cyan] {', '.join(tags)}")

    if rec.evidence_file:
        loc = rec.evidence_file
        if rec.evidence_line:
            loc += f":{rec.evidence_line}"
        lines.append(f"[cyan]Evidence:[/cyan] {loc}")

    console.print(Panel("\n".join(lines), title=f"Decision {rec.id[:8]}"))


# ---------------------------------------------------------------------------
# decision confirm
# ---------------------------------------------------------------------------


@decision_group.command("confirm")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
def decision_confirm(decision_id: str, path: str | None) -> None:
    """Confirm a proposed decision (set status to active)."""
    repo_path = resolve_repo_path(path)

    async def _update():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            update_decision_status,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            rec = await update_decision_status(session, decision_id, "active")

        await engine.dispose()
        return rec

    rec = run_async(_update())
    if rec is None:
        console.print(f"[red]Decision not found: {decision_id}[/red]")
    else:
        console.print(f"[green]Decision {rec.id[:8]} confirmed (active)[/green]")


# ---------------------------------------------------------------------------
# decision dismiss
# ---------------------------------------------------------------------------


@decision_group.command("dismiss")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
def decision_dismiss(decision_id: str, path: str | None) -> None:
    """Dismiss (delete) a proposed decision."""
    repo_path = resolve_repo_path(path)

    if not click.confirm(f"Delete decision {decision_id[:8]}?"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    async def _delete():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            delete_decision,
            get_session,
            init_db,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            deleted = await delete_decision(session, decision_id)

        await engine.dispose()
        return deleted

    deleted = run_async(_delete())
    if deleted:
        console.print(f"[green]Decision {decision_id[:8]} dismissed.[/green]")
    else:
        console.print(f"[red]Decision not found: {decision_id}[/red]")


# ---------------------------------------------------------------------------
# decision deprecate
# ---------------------------------------------------------------------------


@decision_group.command("deprecate")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
@click.option("--superseded-by", default=None, help="ID of the decision that replaces this one.")
def decision_deprecate(decision_id: str, path: str | None, superseded_by: str | None) -> None:
    """Deprecate an active decision."""
    repo_path = resolve_repo_path(path)

    async def _update():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            update_decision_status,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            rec = await update_decision_status(
                session, decision_id, "deprecated", superseded_by=superseded_by
            )

        await engine.dispose()
        return rec

    rec = run_async(_update())
    if rec is None:
        console.print(f"[red]Decision not found: {decision_id}[/red]")
    else:
        console.print(f"[yellow]Decision {rec.id[:8]} deprecated.[/yellow]")


# ---------------------------------------------------------------------------
# decision health
# ---------------------------------------------------------------------------


@decision_group.command("health")
@click.argument("path", required=False, default=None)
def decision_health(path: str | None) -> None:
    """Show decision health: stale decisions, proposed, ungoverned hotspots."""
    repo_path = resolve_repo_path(path)

    async def _query():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_decision_health_summary,
            get_session,
            init_db,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            health = await get_decision_health_summary(session, repo.id)

        await engine.dispose()
        return health

    health = run_async(_query())
    summary = health["summary"]

    console.print("[bold]Decision Health[/bold]\n")

    # Summary stats
    stats_table = Table(show_header=False, box=None)
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", justify="right")
    stats_table.add_row("Active decisions", str(summary.get("active", 0)))
    stats_table.add_row("Proposed (needs review)", f"[yellow]{summary.get('proposed', 0)}[/yellow]")
    stats_table.add_row("Stale decisions", f"[red]{summary.get('stale', 0)}[/red]")
    stats_table.add_row("Deprecated", str(summary.get("deprecated", 0)))
    console.print(stats_table)

    # Stale decisions
    stale = health["stale_decisions"]
    if stale:
        console.print(f"\n[red]Stale decisions ({len(stale)}):[/red]")
        for d in stale[:5]:
            console.print(f"  {d.id[:8]}  {d.title[:50]}  (staleness: {d.staleness_score:.2f})")

    # Ungoverned hotspots
    ungoverned = health["ungoverned_hotspots"]
    if ungoverned:
        console.print(f"\n[yellow]Ungoverned hotspots ({len(ungoverned)}):[/yellow]")
        for fp in ungoverned[:10]:
            console.print(f"  {fp}")

    # Proposed
    proposed = health["proposed_awaiting_review"]
    if proposed:
        console.print(f"\n[yellow]Proposed decisions ({len(proposed)}):[/yellow]")
        for d in proposed[:5]:
            console.print(f"  {d.id[:8]}  {d.title[:50]}  (source: {d.source})")
