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
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.core.analysis.decisions.provenance import LISTABLE_SOURCES
from repowise.core.precedent.currency import describe_decision_currency

#: The ladder's real sources plus the no-filter sentinel. Derived, because the
#: hand-written copy had drifted: it offered ``readme_mining`` (since retired)
#: while omitting ``session``, the source carrying a user's own words and the
#: one you would most want to filter for.
_SOURCE_CHOICES: tuple[str, ...] = (*LISTABLE_SOURCES, "all")


def _resolve_decision_repo(path: str | None, fmt: str = "table"):
    """Resolve the repo path for decision subcommands.

    Honors workspace auto-detection: in workspace mode without an explicit
    path, targets the primary repo and prints a transparency notice.
    """

    target = resolve_command_target(path=path)
    target.notice(notice_console(fmt), command="decision")
    if target.is_workspace:
        primary = target.primary_path()
        if primary is None:
            raise click.ClickException("Workspace has no primary repo configured.")
        return primary
    assert target.repo_path is not None
    return target.repo_path


@click.group("decision")
def decision_group() -> None:
    """Manage architectural decision records."""


async def _resolve_decision_id(session, decision_id: str) -> str | None:
    """Expand a (possibly truncated) decision id to the full stored id.

    ``decision list`` prints 8-char prefixes, so every id-taking subcommand
    accepts a unique prefix. Returns None when nothing matches; raises on an
    ambiguous prefix.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import DecisionRecord
    from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like

    result = await session.execute(
        select(DecisionRecord.id)
        .where(DecisionRecord.id.like(f"{escape_like(decision_id)}%", escape=LIKE_ESCAPE))
        .limit(2)
    )
    ids = [row[0] for row in result.all()]
    if len(ids) > 1:
        raise click.ClickException(
            f"Decision id prefix {decision_id!r} is ambiguous; use more characters."
        )
    return ids[0] if ids else None


# ---------------------------------------------------------------------------
# decision add
# ---------------------------------------------------------------------------


@decision_group.command("add")
@click.argument("path", required=False, default=None)
def decision_add(path: str | None) -> None:
    """Interactively add a new architectural decision."""
    repo_path = _resolve_decision_repo(path)
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
    type=click.Choice(["proposed", "active", "deprecated", "superseded", "dismissed", "all"]),
    default="all",
)
@click.option(
    "--source",
    type=click.Choice(_SOURCE_CHOICES),
    default="all",
)
@click.option("--proposed", is_flag=True, default=False, help="Show only proposed decisions.")
@click.option("--stale-only", is_flag=True, default=False, help="Show only stale decisions.")
@format_option()
def decision_list(
    path: str | None,
    status: str,
    source: str,
    proposed: bool,
    stale_only: bool,
    fmt: str,
) -> None:
    """List architectural decision records."""
    repo_path = _resolve_decision_repo(path, fmt)

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

    if fmt == "json":
        emit_json(
            {
                "repo": str(repo_path),
                "decisions": [
                    {
                        # Full id, not the table's 8-char prefix: the prefix
                        # exists to fit a column, and every id-taking
                        # subcommand accepts either.
                        "id": d.id,
                        "title": d.title,
                        "status": d.status,
                        "source": d.source,
                        "confidence": d.confidence,
                        "staleness_score": d.staleness_score,
                        "created_at": d.created_at.isoformat() if d.created_at else None,
                    }
                    for d in decisions
                ],
            }
        )
        return

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
        "dismissed": "dim",
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
@format_option()
def decision_show(decision_id: str, path: str | None, fmt: str) -> None:
    """Show full details of a decision record."""
    repo_path = _resolve_decision_repo(path, fmt)

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
            full_id = await _resolve_decision_id(session, decision_id)
            rec = await get_decision(session, full_id) if full_id else None

        await engine.dispose()
        return rec

    rec = run_async(_query())
    if rec is None:
        notice_console(fmt).print(f"[red]Decision not found: {decision_id}[/red]")
        if fmt == "json":
            emit_json({"query": decision_id, "decision": None})
        return

    if fmt == "json":
        emit_json(
            {
                "query": decision_id,
                "decision": {
                    "id": rec.id,
                    "title": rec.title,
                    "status": rec.status,
                    "source": rec.source,
                    "confidence": rec.confidence,
                    "staleness_score": rec.staleness_score,
                    "created_at": rec.created_at.isoformat() if rec.created_at else None,
                    "currency": describe_decision_currency(
                        repo_path,
                        created_at=rec.created_at,
                        nodes=json.loads(rec.affected_files_json or "[]"),
                    ),
                    "context": rec.context,
                    "decision": rec.decision,
                    "rationale": rec.rationale,
                    "alternatives": json.loads(rec.alternatives_json),
                    "consequences": json.loads(rec.consequences_json),
                    # Not clipped to 10 the way the panel clips it: the panel
                    # clips to stay readable, and a caller asking for json is
                    # asking for the record, not a summary of it.
                    "affected_files": json.loads(rec.affected_files_json),
                    "tags": json.loads(rec.tags_json),
                    "evidence_file": rec.evidence_file,
                    "evidence_line": rec.evidence_line,
                },
            }
        )
        return

    lines = [
        f"[bold]{rec.title}[/bold]",
        f"Status: {rec.status}  |  Source: {rec.source}  |  Confidence: {rec.confidence:.0%}",
        f"Staleness: {rec.staleness_score:.2f}",
    ]
    # The stored score is a proportion; this is the fact behind it, asked of
    # git at read time. `show` is one record on demand, which is exactly where
    # a subprocess is affordable — nothing on the hook or update path may do
    # this. None means git could not decide, and then we say nothing.
    currency = describe_decision_currency(
        repo_path,
        created_at=rec.created_at,
        nodes=json.loads(rec.affected_files_json or "[]"),
    )
    if currency:
        lines.append(f"[dim]{currency}[/dim]")
    lines.append("")
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
    repo_path = _resolve_decision_repo(path)

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
            full_id = await _resolve_decision_id(session, decision_id)
            rec = await update_decision_status(session, full_id, "active") if full_id else None

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
    """Dismiss a proposed decision (kept as a tombstone; never re-proposed)."""
    repo_path = _resolve_decision_repo(path)

    if not click.confirm(f"Dismiss decision {decision_id[:8]}?"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    async def _dismiss():
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
            full_id = await _resolve_decision_id(session, decision_id)
            rec = await update_decision_status(session, full_id, "dismissed") if full_id else None

        await engine.dispose()
        return rec

    rec = run_async(_dismiss())
    if rec is not None:
        console.print(
            f"[green]Decision {rec.id[:8]} dismissed[/green] "
            "[dim](kept as a tombstone; reindexing will not re-propose it)[/dim]"
        )
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
    repo_path = _resolve_decision_repo(path)

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
            full_id = await _resolve_decision_id(session, decision_id)
            rec = (
                await update_decision_status(
                    session, full_id, "deprecated", superseded_by=superseded_by
                )
                if full_id
                else None
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
@format_option()
def decision_health(path: str | None, fmt: str) -> None:
    """Show decision health: stale decisions, proposed, ungoverned hotspots."""
    repo_path = _resolve_decision_repo(path, fmt)

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

    if fmt == "json":
        # The table caps each list (5 stale, 10 hotspots, 5 proposed) to keep
        # the report short; json carries them whole.
        emit_json(
            {
                "repo": str(repo_path),
                "summary": summary,
                "stale_decisions": [
                    {"id": d.id, "title": d.title, "staleness_score": d.staleness_score}
                    for d in health["stale_decisions"]
                ],
                "ungoverned_hotspots": list(health["ungoverned_hotspots"]),
                "proposed_awaiting_review": [
                    {"id": d.id, "title": d.title, "source": d.source}
                    for d in health["proposed_awaiting_review"]
                ],
            }
        )
        return

    console.print("[bold]Decision Health[/bold]\n")

    # Summary stats
    stats_table = Table(show_header=False, box=None)
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", justify="right")
    stats_table.add_row("Active decisions", str(summary.get("active", 0)))
    stats_table.add_row("Proposed (needs review)", f"[yellow]{summary.get('proposed', 0)}[/yellow]")
    stats_table.add_row("Stale decisions", f"[red]{summary.get('stale', 0)}[/red]")
    unscoped = summary.get("unscoped", 0)
    if unscoped:
        # Not folded into "stale": these were never checked, which is a
        # different thing from checked and found to have drifted.
        stats_table.add_row("Unscoped (cannot be checked)", f"[yellow]{unscoped}[/yellow]")
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
