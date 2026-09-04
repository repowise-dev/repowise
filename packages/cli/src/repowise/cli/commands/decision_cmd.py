"""``repowise decision`` — manage architectural decision records."""

from __future__ import annotations

import json
import re
from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
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


def _register_config_commands() -> None:
    """Attach the capture-control commands.

    They live in their own module (policy resolution, presets, per-source
    switches) and are attached here so ``decision`` stays one group.
    """
    from repowise.cli.commands.decision_config_cmd import (
        config_group,
        llm_command,
        source_group,
    )

    decision_group.add_command(config_group)
    decision_group.add_command(source_group)
    decision_group.add_command(llm_command)

    from repowise.cli.commands.decision_review_cmd import (
        candidates_command,
        export_command,
        import_command,
        merge_command,
        migrate_command,
        split_command,
        status_command,
    )

    for command in (
        migrate_command,
        candidates_command,
        merge_command,
        split_command,
        export_command,
        import_command,
        status_command,
    ):
        decision_group.add_command(command)


_register_config_commands()


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
    if ids:
        return ids[0]

    # Merging and superseding retire ids that are already written down
    # somewhere. Resolving through the alias keeps those working instead of
    # reporting the decision as gone.
    from repowise.core.persistence.models import DecisionAlias

    alias = await session.execute(
        select(DecisionAlias.decision_id)
        .where(DecisionAlias.alias_id.like(f"{escape_like(decision_id)}%", escape=LIKE_ESCAPE))
        .limit(2)
    )
    alias_ids = [row[0] for row in alias.all()]
    if len(alias_ids) > 1:
        raise click.ClickException(
            f"Decision id prefix {decision_id!r} is ambiguous; use more characters."
        )
    return alias_ids[0] if alias_ids else None


# ---------------------------------------------------------------------------
# decision add
# ---------------------------------------------------------------------------


@decision_group.command("add")
@click.argument("path", required=False, default=None)
@click.option("--title", default=None, help="Decision title (short).")
@click.option("--context", default=None, help="What forced this decision?")
@click.option("--decision", "decision_text", default=None, help="What was chosen?")
@click.option("--rationale", default=None, help="Why it was chosen.")
@click.option(
    "--alternative", "alternatives", multiple=True, help="A rejected alternative. Repeatable."
)
@click.option(
    "--consequence", "consequences", multiple=True, help="A tradeoff accepted. Repeatable."
)
@click.option(
    "--affects", "affected", multiple=True, help="A file or module this governs. Repeatable."
)
@click.option("--tag", "tags", multiple=True, help="A tag. Repeatable.")
@format_option()
def decision_add(
    path: str | None,
    title: str | None,
    context: str | None,
    decision_text: str | None,
    rationale: str | None,
    alternatives: tuple[str, ...],
    consequences: tuple[str, ...],
    affected: tuple[str, ...],
    tags: tuple[str, ...],
    fmt: str,
) -> None:
    """Add an architectural decision, interactively or from flags.

    With both --title and --decision, records without prompting and prints the
    new id, so a script or an agent can call it. Everything else is optional.

    A flag-driven record lands as `proposed`, where the prompts record `active`.
    A person answering eight questions has reviewed the decision; a caller
    inferring one from a diff has not, and the store should be able to tell
    them apart. Promote with `repowise decision confirm <id>`.
    """
    # Flags and prompts are the two paths, and a half-filled command line is
    # neither: falling through to the prompts would hang a caller that has no
    # stdin, which is the failure this command exists to stop having.
    non_interactive = bool(title and decision_text)
    if not non_interactive:
        flagged = any((title, context, decision_text, rationale)) or any(
            (alternatives, consequences, affected, tags)
        )
        if flagged or fmt == "json":
            _ta.emit_error(
                {
                    "error": "--title and --decision are both required to add a "
                    "decision without prompting.",
                    "guidance": "Run `repowise decision add` with no flags to be "
                    "prompted for each field instead.",
                },
                fmt,
            )

    repo_path = _resolve_decision_repo(path, fmt)
    ensure_repowise_dir(repo_path)

    status = "proposed" if non_interactive else "active"
    alternatives_list = list(alternatives)
    consequences_list = list(consequences)
    affected_files = list(affected)
    tags_list = list(tags)

    if not non_interactive:
        console.print("[bold]Add Architectural Decision[/bold]\n")

        title = click.prompt("Decision title (short)")
        context = click.prompt("Context (what forced this decision?)", default="")
        decision_text = click.prompt("Decision (what was chosen?)")
        rationale = click.prompt("Rationale (why?)", default="")

        alternatives_raw = click.prompt(
            "Rejected alternatives (comma-separated, optional)", default=""
        )
        alternatives_list = [a.strip() for a in alternatives_raw.split(",") if a.strip()]

        consequences_raw = click.prompt(
            "Tradeoffs/consequences (comma-separated, optional)", default=""
        )
        consequences_list = [c.strip() for c in consequences_raw.split(",") if c.strip()]

        affected_raw = click.prompt(
            "Affected files/modules (comma-separated; required to make it govern)",
            default="",
        )
        affected_files = [f.strip() for f in affected_raw.split(",") if f.strip()]

        tags_raw = click.prompt(
            "Tags (comma-separated: auth, database, api, performance, security, infra, testing)",
            default="",
        )
        tags_list = [t.strip() for t in tags_raw.split(",") if t.strip()]

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
                status=status,
                context=context or "",
                decision=decision_text,
                rationale=rationale or "",
                alternatives=alternatives_list,
                consequences=consequences_list,
                affected_files=affected_files,
                affected_modules=[],
                tags=tags_list,
                source="cli",
                confidence=1.0,
            )
            decision_id = rec.id
            # A decision that names nothing cannot be checked against the code
            # and cannot reach the agent editing a governed file, so it cannot
            # be accepted. Keeping it as a candidate is better than discarding
            # eight answered questions; ``confirm --scope`` finishes the job.
            if status == "active" and affected_files:
                # Answering the prompts is the acceptance; recording it as one
                # is what makes this record indistinguishable from any other
                # accepted decision to every reader.
                from repowise.core.analysis.decisions.accepter import resolve_accepter
                from repowise.core.persistence.crud.authority import (
                    AcceptanceRefusedError,
                    accept_decision,
                )

                try:
                    await accept_decision(
                        session, rec, accepter=resolve_accepter(repo_path)
                    )
                except AcceptanceRefusedError as exc:
                    raise click.ClickException(
                        f"Cannot accept this decision: {exc}."
                    ) from exc

        await engine.dispose()
        return decision_id, rec.status

    decision_id, stored_status = run_async(_persist())
    if stored_status != "active" and not non_interactive:
        console.print(
            "[yellow]Stored as a candidate: it names no files, so it cannot be "
            "checked against the code.[/yellow]\n"
            f"[dim]repowise decision confirm {decision_id[:8]} --scope <path>[/dim]"
        )

    if fmt == "json":
        # The full id, not the table's 8-char prefix — a caller that parses
        # this is about to pass it back to `confirm` or `show`.
        emit_json(
            {
                "repo": str(repo_path),
                "decision": {"id": decision_id, "title": title, "status": status},
            }
        )
        return
    console.print(
        f"\n[green]Decision recorded[/green] [dim]({status})[/dim] — "
        f"ID: [bold]{decision_id[:8]}[/bold]"
    )


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
        # Non-zero for the same reason the lifecycle commands are: a caller
        # scripting `show` cannot tell a missing id from an empty record.
        raise click.exceptions.Exit(1)

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



def _emit_lifecycle(rec, decision_id: str, action: str, fmt: str, note: str = "") -> None:
    """Report one status transition to a person or to a machine.

    Not found exits non-zero: an agent driving the lifecycle could not tell a
    typo'd id from a successful confirm when both returned 0.
    """
    if rec is None:
        if fmt == "json":
            emit_json({"error": "decision_not_found", "decision_id": decision_id})
        else:
            console.print(f"[red]Decision not found: {decision_id}[/red]")
        raise click.exceptions.Exit(1)
    if fmt == "json":
        emit_json({"id": rec.id, "status": rec.status, "action": action})
        return
    console.print(f"[green]Decision {rec.id[:8]} {action}[/green]" + (f" {note}" if note else ""))


# ---------------------------------------------------------------------------
# decision confirm / dismiss
# ---------------------------------------------------------------------------


#: The remedy printed beside a refused acceptance, naming the flags that
#: supply what the contract found missing.
_ACCEPT_REMEDY = "Supply the missing parts with --reason, --scope or --evidence."


class _PreviewRollbackError(Exception):
    """Signals a preview to roll its savepoint back."""


#: A decision id, or a prefix of one, as ``_resolve_decision_id`` accepts it.
_ID_SHAPED = re.compile(r"[0-9a-fA-F]{4,64}\Z")


def _split_ids_and_path(tokens: tuple[str, ...]) -> tuple[list[str], str | None]:
    """Separate decision ids from the optional trailing repo path.

    ``confirm ID [PATH]`` shipped before batch arity, so the path is still a
    positional. An id-shaped last token stays an id even when a directory of
    that name happens to exist, because reading one as a path would drop it
    from the batch and still exit 0.
    """
    last = tokens[-1] if tokens else ""
    if len(tokens) > 1 and last and not _ID_SHAPED.match(last) and Path(last).is_dir():
        return list(tokens[:-1]), last
    return list(tokens), None


async def _resolve_one(session, token: str):
    """The record *token* names, or the failure to report for it."""
    from repowise.core.persistence.models import DecisionRecord

    try:
        full_id = await _resolve_decision_id(session, token)
    except click.ClickException as exc:
        return None, {"given": token, "ok": False, "error": "ambiguous_id", "message": str(exc)}
    rec = await session.get(DecisionRecord, full_id) if full_id else None
    if rec is None:
        return None, {
            "given": token,
            "ok": False,
            "error": "decision_not_found",
            "message": f"Decision not found: {token}",
        }
    return rec, None


async def _review_batch(repo_path, tokens, *, action: str, verb: str, preview: bool, apply_one):
    """Apply *apply_one* to every id, keeping one refusal from ending the run.

    Each id runs inside its own savepoint. ``accept_decision`` edits the
    record's rationale and scope before the contract can refuse it, so without
    one a refused id would leave that edit in the transaction the accepted ids
    commit. A preview runs the real write and then rolls the whole session
    back, so what it reports is what the contract actually said; the schema
    reconcile every subcommand opens the store with still runs.
    """
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.crud.authority import AcceptanceRefusedError

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        await init_db(engine)
    except BaseException:
        await engine.dispose()
        raise
    results: list[dict] = []
    try:
        async with get_session(create_session_factory(engine)) as session:
            for token in tokens:
                rec, failure = await _resolve_one(session, token)
                if failure is not None:
                    results.append(failure)
                    continue
                entry = {"given": token, "id": rec.id, "title": rec.title}
                try:
                    async with session.begin_nested():
                        await apply_one(session, rec)
                        if preview:
                            raise _PreviewRollbackError
                except _PreviewRollbackError:
                    results.append({**entry, "ok": True, "action": f"would_{verb}"})
                except AcceptanceRefusedError as exc:
                    results.append(
                        {
                            **entry,
                            "ok": False,
                            "error": "acceptance_refused",
                            "message": str(exc),
                            "blockers": list(exc.blockers),
                        }
                    )
                else:
                    results.append({**entry, "ok": True, "action": action, "status": rec.status})
            if preview:
                await session.rollback()
    finally:
        await engine.dispose()
    return results


def _emit_batch(results: list[dict], action: str, verb: str, preview: bool, fmt: str) -> None:
    """Report a multi-id run, exiting non-zero when any id was refused."""
    failed = [r for r in results if not r["ok"]]
    if fmt == "json":
        emit_json(
            {
                "action": action,
                "preview": preview,
                "results": results,
                "succeeded": len(results) - len(failed),
                "failed": len(failed),
            }
        )
        if failed:
            raise click.exceptions.Exit(1)
        return

    headline = f"Would {verb}" if preview else action.capitalize()
    table = Table(title=f"{headline} {len(results) - len(failed)} of {len(results)}")
    for column in ("ID", "Title", "Outcome"):
        table.add_column(column)
    for result in results:
        outcome = "[green]ok[/green]" if result["ok"] else f"[red]{result['message']}[/red]"
        table.add_row(result.get("id", result["given"])[:8], result.get("title", "")[:50], outcome)
    console.print(table)
    if preview:
        console.print("[dim]Nothing was written. Re-run without --preview.[/dim]")
    if failed:
        raise click.exceptions.Exit(1)


def _emit_single(result: dict, token: str, verb: str, fmt: str, note: str, remedy: str) -> None:
    """The one-id document, unchanged from before these verbs took many."""
    if result["ok"]:
        if fmt == "json":
            emit_json({"id": result["id"], "status": result["status"], "action": result["action"]})
            return
        console.print(
            f"[green]Decision {result['id'][:8]} {result['action']}[/green]"
            + (f" {note}" if note else "")
        )
        return
    if result["error"] == "decision_not_found":
        _emit_lifecycle(None, token, "", fmt)
        return
    if "id" not in result:
        # An ambiguous prefix never resolved to a record, so there is nothing
        # to name but the token the caller gave.
        emit_refusal(result["error"], result["message"], fmt)
        return
    extra: dict = {"decision_id": result["id"]}
    if "blockers" in result:
        extra["blockers"] = result["blockers"]
        if remedy:
            extra["remedy"] = remedy
    emit_refusal(
        result["error"], f"Cannot {verb} {result['id'][:8]}: {result['message']}", fmt, **extra
    )


@decision_group.command("confirm")
@click.argument("decision_ids", nargs=-1, required=True)
@click.option("--reason", default="", help="Rationale, or why the constraint needs none.")
@click.option(
    "--scope",
    multiple=True,
    help="File or module this governs. Repeatable; replaces the proposed scope.",
)
@click.option(
    "--evidence",
    multiple=True,
    help="Commit, file or link the decision rests on. Repeatable.",
)
@click.option("--as", "accepter", default="", help="Record a different accepter identity.")
@click.option(
    "--preview", is_flag=True, default=False, help="Report what each id would do, and write nothing."
)
@format_option()
def decision_confirm(
    decision_ids: tuple[str, ...],
    reason: str,
    scope: tuple[str, ...],
    evidence: tuple[str, ...],
    accepter: str,
    preview: bool,
    fmt: str,
) -> None:
    """Accept candidates, making them decisions that govern.

    Takes one id or many, and an optional repository path after them. This is
    the acceptance event, and it is the only thing that produces one:
    extraction, recurrence and confidence all stop at a candidate. Acceptance
    is refused rather than stored blank when the candidate carries no reason,
    no scope or no evidence; ``--reason``, ``--scope`` and ``--evidence``
    supply what is missing, and correcting them here corrects the record too.
    A refused id does not stop the others, and the run exits non-zero if any
    were refused.
    """
    ids, path = _split_ids_and_path(decision_ids)
    repo_path = _resolve_decision_repo(path, fmt)

    async def _accept(session, rec) -> None:
        from repowise.core.analysis.decisions.accepter import resolve_accepter
        from repowise.core.persistence.crud.authority import accept_decision

        await accept_decision(
            session,
            rec,
            accepter=resolve_accepter(repo_path, override=accepter),
            reason=reason,
            scope=list(scope) or None,
            evidence=list(evidence) or None,
        )

    results = run_async(
        _review_batch(
            repo_path, ids, action="accepted", verb="accept", preview=preview, apply_one=_accept
        )
    )
    if len(ids) > 1 or preview:
        _emit_batch(results, "accepted", "accept", preview, fmt)
        return
    _emit_single(results[0], ids[0], "accept", fmt, "(governing)", _ACCEPT_REMEDY)


@decision_group.command("dismiss")
@click.argument("decision_ids", nargs=-1, required=True)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the confirmation prompt.")
@click.option("--reason", default="", help="Why it was tombstoned.")
@click.option(
    "--preview", is_flag=True, default=False, help="Report what each id would do, and write nothing."
)
@format_option()
def decision_dismiss(
    decision_ids: tuple[str, ...], yes: bool, reason: str, preview: bool, fmt: str
) -> None:
    """Dismiss proposed decisions (kept as tombstones; never re-proposed).

    Takes one id or many, and an optional repository path after them.
    """
    ids, path = _split_ids_and_path(decision_ids)
    repo_path = _resolve_decision_repo(path, fmt)

    # A machine-readable invocation is non-interactive by construction: the
    # prompt read EOF and aborted every scripted dismissal.
    subject = ids[0][:8] if len(ids) == 1 else f"{len(ids)} decisions"
    if not yes and not preview and fmt != "json" and not click.confirm(f"Dismiss {subject}?"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    async def _dismiss(session, rec) -> None:
        from repowise.core.analysis.decisions.accepter import resolve_accepter
        from repowise.core.persistence.crud.authority import dismiss_candidate

        await dismiss_candidate(session, rec, reason=reason, accepter=resolve_accepter(repo_path))

    results = run_async(
        _review_batch(
            repo_path, ids, action="dismissed", verb="dismiss", preview=preview, apply_one=_dismiss
        )
    )
    if len(ids) > 1 or preview:
        _emit_batch(results, "dismissed", "dismiss", preview, fmt)
        return
    _emit_single(
        results[0],
        ids[0],
        "dismiss",
        fmt,
        "[dim](kept as a tombstone; reindexing will not re-propose it)[/dim]",
        "",
    )


# ---------------------------------------------------------------------------
# decision deprecate
# ---------------------------------------------------------------------------


@decision_group.command("deprecate")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
@click.option("--superseded-by", default=None, help="ID of the decision that replaces this one.")
@format_option()
def decision_deprecate(
    decision_id: str, path: str | None, superseded_by: str | None, fmt: str
) -> None:
    """Retire a decision, optionally naming the one that replaces it.

    With ``--superseded-by`` this writes an explicit lineage edge and keeps the
    retired id resolving to its successor. Similarity never does either: an edge
    exists because somebody named the successor.
    """
    repo_path = _resolve_decision_repo(path, fmt)

    async def _update():
        from repowise.core.analysis.decisions.accepter import resolve_accepter
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            update_decision_status,
        )
        from repowise.core.persistence.crud.authority import (
            AcceptanceRefusedError,
            is_accepted,
            supersede_decision,
        )
        from repowise.core.persistence.models import DecisionRecord

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        try:
            async with get_session(sf) as session:
                full_id = await _resolve_decision_id(session, decision_id)
                rec = await session.get(DecisionRecord, full_id) if full_id else None
                if rec is None:
                    return None
                successor = (
                    await _resolve_decision_id(session, superseded_by)
                    if superseded_by
                    else None
                )
                if superseded_by and successor is None:
                    emit_refusal(
                        "decision_not_found",
                        f"Unknown successor: {superseded_by}",
                        fmt,
                        decision_id=superseded_by,
                    )
                if successor and await is_accepted(session, rec.id):
                    try:
                        await supersede_decision(
                            session,
                            rec,
                            successor_id=successor,
                            accepter=resolve_accepter(repo_path),
                        )
                    except (AcceptanceRefusedError, ValueError) as exc:
                        emit_refusal("supersede_refused", str(exc), fmt, decision_id=rec.id)
                else:
                    # A candidate has no authority to retire, so this stays the
                    # plain status change it always was.
                    await update_decision_status(
                        session, rec.id, "deprecated", superseded_by=successor
                    )
                return rec
        finally:
            await engine.dispose()

    _emit_lifecycle(run_async(_update()), decision_id, "deprecated", fmt)


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
