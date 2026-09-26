"""``repowise decision`` — manage decision records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import click
import structlog
from rich.panel import Panel
from rich.table import Table

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.commands.decision_batch import (
    _emit_batch,
    _emit_lifecycle,
    _emit_single,
    _open_store,
    _resolve_decision_id,
    _review_batch,
    _split_ids_and_path,
)
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.core.agents.identity import is_agent_slug
from repowise.core.analysis.decisions.lifecycle import (
    ACCEPTER_SESSION_MAX,
    AGENT_ACCEPTANCE_REMEDY,
    AGREEMENT_KIND,
    ARCHITECTURAL_KIND,
    DECISION_KINDS,
)
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


def _describe_signature(acceptance) -> dict[str, str] | None:
    """Who signed *acceptance*, as a label and its parts. ``None`` for a candidate.

    No stored kind reads as ``unrecorded``, never as a person's. The verb
    comes from the action: the same log records withdrawals.
    """
    if acceptance is None:
        return None
    who = acceptance.accepter or acceptance.artifact
    kind = acceptance.accepter_kind or "unrecorded"
    label = f"{who} ({kind})"
    if acceptance.accepter_session:
        label += f", session {acceptance.accepter_session}"
    return {
        "label": label,
        "verb": acceptance.action.replace("_", " ").capitalize(),
        "accepter": who,
        "kind": kind,
        "session": acceptance.accepter_session,
        "action": acceptance.action,
    }


def _signer(agent: str, agent_session: str, *, accepter: str = "") -> tuple[str, str]:
    """Validate the signing flags and return ``(kind, accepter_session)``.

    Shared by every verb that appends to the acceptance log, so an agent can
    say it is one wherever it can act.
    """
    _check_agent_flag(agent, accepter)
    _check_session_flag(agent_session, agent)
    return ("agent" if agent else "person"), agent_session


def _check_agent_flag(agent: str, accepter: str) -> None:
    if agent and accepter:
        raise click.ClickException("Pass --agent or --as, not both: they name different signers.")
    if agent and not is_agent_slug(agent):
        raise click.ClickException(
            f"{agent!r} is not a well-formed agent slug (lowercase, digits and underscores)."
        )


def _check_session_flag(agent_session: str, agent: str) -> None:
    if agent_session and not agent:
        raise click.ClickException("--session names the agent signing; pass --agent too.")
    if len(agent_session) > ACCEPTER_SESSION_MAX:
        raise click.ClickException(f"--session is at most {ACCEPTER_SESSION_MAX} characters.")


#: The two flags an agent signs with, on every verb that writes an authority
#: row: withdrawing needs no switch, so it is the likeliest to go unsigned.
def _signing_options(command):
    command = click.option(
        "--session", "agent_session", default="", help="The agent session signing."
    )(command)
    return click.option(
        "--agent",
        default="",
        help="Sign as this agent (e.g. claude_code) rather than as a person.",
    )(command)


def _load_policy(repo_path: Path):
    """The resolved policy, through the same loader ``decision config`` uses."""
    from repowise.cli.commands.decision_config_cmd import _load

    return _load(repo_path).policy


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
        dedupe_command,
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
        dedupe_command,
        split_command,
        export_command,
        import_command,
        status_command,
    ):
        decision_group.add_command(command)


_register_config_commands()


# ---------------------------------------------------------------------------
# decision add
# ---------------------------------------------------------------------------


@dataclass
class _DecisionFields:
    """The answers ``add`` records, whether they came from flags or prompts."""

    kind: str
    title: str | None
    context: str | None
    decision: str | None
    rationale: str | None
    alternatives: list[str]
    consequences: list[str]
    affected_files: list[str]
    tags: list[str]


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
@click.option(
    "--evidence-commit",
    "evidence_commits",
    multiple=True,
    help="A commit this decision was made in. Repeatable.",
)
@click.option(
    "--kind",
    type=click.Choice(DECISION_KINDS),
    default=ARCHITECTURAL_KIND,
    show_default=True,
    help="'architectural' governs the code; 'agreement' governs how the work is done.",
)
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
    evidence_commits: tuple[str, ...],
    kind: str,
    fmt: str,
) -> None:
    """Add a decision, interactively or from flags.

    With both --title and --decision, records without prompting and prints the
    new id, so a script or an agent can call it. Everything else is optional.

    `--kind agreement` records a working agreement: a rule about how the work
    is conducted, which names no file and is not checked against the code.

    A flag-driven record lands as `proposed`, where the prompts record `active`.
    A person answering eight questions has reviewed the decision; a caller
    inferring one from a diff has not, and the store should be able to tell
    them apart. Promote with `repowise decision confirm <id>`.
    """
    non_interactive = bool(title and decision_text)
    if not non_interactive:
        _refuse_partial_flags(
            fmt,
            title, context, decision_text, rationale,
            alternatives, consequences, affected, tags, evidence_commits,
        )

    repo_path = _resolve_decision_repo(path, fmt)
    ensure_repowise_dir(repo_path)

    status = "proposed" if non_interactive else "active"
    if non_interactive:
        fields = _DecisionFields(
            kind=kind,
            title=title,
            context=context,
            decision=decision_text,
            rationale=rationale,
            alternatives=list(alternatives),
            consequences=list(consequences),
            affected_files=list(affected),
            tags=list(tags),
        )
    else:
        fields = _prompt_decision_fields(kind)

    decision_id, stored_status = run_async(
        _persist_decision(repo_path, fields, status, list(evidence_commits))
    )
    if stored_status != "active" and not non_interactive:
        console.print(
            "[yellow]Stored as a candidate: it names no files, so it cannot be "
            "checked against the code.[/yellow]\n"
            f"[dim]repowise decision confirm {decision_id[:8]} --scope <path>[/dim]"
        )

    if fmt == "json":
        # The full id: a caller passes it back to `confirm` or `show`.
        emit_json(
            {
                "repo": str(repo_path),
                "decision": {
                    "id": decision_id,
                    "title": fields.title,
                    "status": status,
                    "kind": fields.kind,
                },
            }
        )
        return
    console.print(
        f"\n[green]Decision recorded[/green] [dim]({status})[/dim] — "
        f"ID: [bold]{decision_id[:8]}[/bold]"
    )


def _refuse_partial_flags(fmt: str, *given) -> None:
    """Stop a half-filled command line before it reaches the prompts.

    Falling through to the prompts would hang a caller that has no stdin.
    """
    if any(given) or fmt == "json":
        _ta.emit_error(
            {
                "error": "--title and --decision are both required to add a "
                "decision without prompting.",
                "guidance": "Run `repowise decision add` with no flags to be "
                "prompted for each field instead.",
            },
            fmt,
        )


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _prompt_decision_fields(kind: str) -> _DecisionFields:
    """Ask for each field in turn; *kind* is the flag's value, offered as default."""
    console.print("[bold]Add Decision[/bold]\n")

    kind = click.prompt(
        "Kind (architectural = about the code, agreement = about how we work)",
        type=click.Choice(DECISION_KINDS),
        # The flag, so `--kind agreement` alone is not silently discarded
        # by falling through to the prompts.
        default=kind,
    )
    title = click.prompt("Decision title (short)")
    context = click.prompt("Context (what forced this decision?)", default="")
    decision_text = click.prompt("Decision (what was chosen?)")
    rationale = click.prompt("Rationale (why?)", default="")
    alternatives = _split_csv(
        click.prompt("Rejected alternatives (comma-separated, optional)", default="")
    )
    consequences = _split_csv(
        click.prompt("Tradeoffs/consequences (comma-separated, optional)", default="")
    )
    affected_files: list[str] = []
    if kind == ARCHITECTURAL_KIND:
        # An agreement names no file by definition, so asking is asking a
        # question whose only right answer is blank.
        affected_files = _split_csv(
            click.prompt(
                "Affected files/modules (comma-separated; required to make it govern)",
                default="",
            )
        )
    tags = _split_csv(
        click.prompt(
            "Tags (comma-separated: auth, database, api, performance, security, infra, testing)",
            default="",
        )
    )
    return _DecisionFields(
        kind=kind,
        title=title,
        context=context,
        decision=decision_text,
        rationale=rationale,
        alternatives=alternatives,
        consequences=consequences,
        affected_files=affected_files,
        tags=tags,
    )


async def _persist_decision(
    repo_path: Path, fields: _DecisionFields, status: str, evidence_commits: list[str]
) -> tuple[str, str]:
    """Write the record, accept it when the prompts earned that; return id and status."""
    from repowise.core.persistence import get_session, upsert_decision, upsert_repository

    async with _open_store(repo_path) as sf:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            rec = await upsert_decision(
                session,
                repository_id=repo.id,
                title=fields.title,
                status=status,
                context=fields.context or "",
                decision=fields.decision,
                rationale=fields.rationale or "",
                alternatives=fields.alternatives,
                consequences=fields.consequences,
                affected_files=fields.affected_files,
                # None derives them from the files; [] would clear them.
                affected_modules=None,
                tags=fields.tags,
                # What the capture hook suppresses on: without it, the hook
                # asks again next session for a commit already recorded.
                evidence_commits=evidence_commits,
                kind=fields.kind,
                source="cli",
                # No confidence: upsert_decision scores a manual entry.
            )
            # An architectural decision needs files to govern, so without them
            # it stays a candidate for ``confirm --scope``. An agreement names no
            # file by definition; its scope is the repository.
            if status == "active" and (fields.affected_files or fields.kind == AGREEMENT_KIND):
                await _accept_answered_prompts(session, rec, repo_path)

            embed = (rec.id, rec.title, rec.decision or "", rec.evidence_file)
            stored_status = rec.status

        # After the session commits, so a network embed never holds the write
        # transaction or vectors an uncommitted record.
        await _embed_decision(repo_path, *embed)
    return embed[0], stored_status


async def _accept_answered_prompts(session, rec, repo_path: Path) -> None:
    """Record the answered prompts as an acceptance, like any other."""
    from repowise.core.analysis.decisions.accepter import resolve_accepter
    from repowise.core.persistence.crud.authority import (
        AcceptanceRefusedError,
        accept_decision,
    )

    try:
        await accept_decision(session, rec, accepter=resolve_accepter(repo_path))
    except AcceptanceRefusedError as exc:
        raise click.ClickException(f"Cannot accept this decision: {exc}.") from exc


async def _embed_decision(
    repo_path: Path,
    decision_id: str,
    title: str,
    decision: str,
    evidence_file: str | None,
) -> None:
    """Write a record's ``decision:`` vector, or leave the store untouched.

    Without it a manual entry is invisible to semantic dedup in both
    directions until the next reindex: it can neither find a duplicate nor be
    found as one. Best-effort, like the mined write path, and a no-op when the
    repo has no real embedder, because a keyless user must still be able to
    record a decision.
    """
    from repowise.cli.providers.embedders import build_embedder, resolve_embedder_for_repo
    from repowise.cli.providers.vector_store import build_vector_store
    from repowise.core.analysis.decisions.semantic_match import upsert_decision_vector
    from repowise.core.providers.embedding import is_semantic_embedder

    try:
        # Before building the store, which would create its directory for a
        # repo whose embedder cannot fill it.
        embedder = build_embedder(resolve_embedder_for_repo(repo_path), repo_path)
        if not is_semantic_embedder(embedder):
            return
        store = build_vector_store(repo_path, embedder)
        if store is None:
            return
        await upsert_decision_vector(
            store,
            decision_id,
            title=title,
            decision=decision,
            evidence_file=evidence_file,
        )
    except Exception as err:
        # Covers resolving and building the store. The embed call itself
        # swallows its own failures, so this does not see those.
        structlog.get_logger(__name__).debug(
            "decision.embed_skipped", decision_id=decision_id, error=str(err)
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
    decisions = run_async(
        _query_decisions(
            repo_path,
            status=status if status != "all" else ("proposed" if proposed else None),
            source=source if source != "all" else None,
        )
    )

    decisions = _narrow(decisions, proposed=proposed, stale_only=stale_only)

    if fmt == "json":
        emit_json({"repo": str(repo_path), "decisions": [_listed_json(d) for d in decisions]})
        return

    if not decisions:
        console.print("[dim]No decisions found.[/dim]")
        return
    _print_decision_table(decisions)


def _narrow(decisions: list, *, proposed: bool, stale_only: bool) -> list:
    """Apply ``--proposed`` and ``--stale-only`` to what the query returned."""
    if proposed:
        decisions = [d for d in decisions if d.status == "proposed"]
    if stale_only:
        decisions = [d for d in decisions if d.staleness_score >= 0.5]
    return decisions


def _listed_json(d) -> dict:
    return {
        # Full id, not the table's 8-char prefix: the prefix exists to fit a
        # column, and every id-taking subcommand accepts either.
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "source": d.source,
        "confidence": d.confidence,
        "staleness_score": d.staleness_score,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


async def _query_decisions(repo_path: Path, *, status: str | None, source: str | None) -> list:
    from repowise.core.persistence import get_session, list_decisions, upsert_repository

    async with _open_store(repo_path) as sf, get_session(sf) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        return await list_decisions(
            session,
            repo.id,
            status=status,
            source=source,
            include_proposed=True,
            limit=100,
        )


_STATUS_COLORS = {
    "active": "green",
    "proposed": "yellow",
    "deprecated": "red",
    "superseded": "dim",
    "dismissed": "dim",
}


def _print_decision_table(decisions: list) -> None:
    table = Table(title="Architectural Decisions")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Title", max_width=40)
    table.add_column("Status")
    table.add_column("Source", style="dim")
    table.add_column("Conf.", justify="right")
    table.add_column("Stale", justify="right")
    table.add_column("Created", style="dim")

    for d in decisions:
        color = _STATUS_COLORS.get(d.status, "white")
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
    rec, signed = run_async(_load_decision(repo_path, decision_id))
    if rec is None:
        notice_console(fmt).print(f"[red]Decision not found: {decision_id}[/red]")
        if fmt == "json":
            emit_json({"query": decision_id, "decision": None})
        # Non-zero for the same reason the lifecycle commands are: a caller
        # scripting `show` cannot tell a missing id from an empty record.
        raise click.exceptions.Exit(1)

    # Asked of git at read time: affordable for one record on demand, never on
    # the hook or update path. None means git could not decide.
    currency = describe_decision_currency(
        repo_path,
        created_at=rec.created_at,
        nodes=json.loads(rec.affected_files_json or "[]"),
    )
    if fmt == "json":
        emit_json({"query": decision_id, "decision": _decision_json(rec, signed, currency)})
        return
    lines = _decision_header_lines(rec, signed, currency) + _decision_body_lines(rec)
    console.print(Panel("\n".join(lines), title=f"Decision {rec.id[:8]}"))


async def _load_decision(repo_path: Path, decision_id: str):
    """The record *decision_id* names and its signature, or ``(None, None)``."""
    from repowise.core.persistence import get_decision, get_session
    from repowise.core.persistence.crud.authority import latest_acceptance

    async with _open_store(repo_path) as sf, get_session(sf) as session:
        full_id = await _resolve_decision_id(session, decision_id)
        rec = await get_decision(session, full_id) if full_id else None
        acceptance = await latest_acceptance(session, rec.id) if rec else None
        return rec, _describe_signature(acceptance)


def _decision_json(rec, signed, currency) -> dict:
    return {
        "id": rec.id,
        "title": rec.title,
        "status": rec.status,
        "source": rec.source,
        "confidence": rec.confidence,
        "staleness_score": rec.staleness_score,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        # Not "accepted_by": the same log records withdrawals.
        "signature": signed,
        "currency": currency,
        "context": rec.context,
        "decision": rec.decision,
        "rationale": rec.rationale,
        "alternatives": json.loads(rec.alternatives_json),
        "consequences": json.loads(rec.consequences_json),
        # Unclipped: json is the record, the panel a summary.
        "affected_files": json.loads(rec.affected_files_json),
        "tags": json.loads(rec.tags_json),
        "evidence_file": rec.evidence_file,
        "evidence_line": rec.evidence_line,
    }


def _decision_header_lines(rec, signed, currency) -> list[str]:
    lines = [
        f"[bold]{rec.title}[/bold]",
        f"Status: {rec.status}  |  Source: {rec.source}  |  Confidence: {rec.confidence:.0%}",
        f"Staleness: {rec.staleness_score:.2f}",
    ]
    if currency:
        lines.append(f"[dim]{currency}[/dim]")
    # A candidate has no line at all; status already says so.
    if signed:
        lines.append(f"{signed['verb']} by: {signed['label']}")
    lines.append("")
    return lines


def _decision_body_lines(rec) -> list[str]:
    """The record's prose and lists, each section only when it has content."""
    lines = [
        f"[cyan]{label}:[/cyan] {value}"
        for label, value in (
            ("Context", rec.context),
            ("Decision", rec.decision),
            ("Rationale", rec.rationale),
        )
        if value
    ]
    for heading, items in (
        ("Alternatives rejected", json.loads(rec.alternatives_json)),
        ("Consequences", json.loads(rec.consequences_json)),
    ):
        if items:
            lines.append(f"[cyan]{heading}:[/cyan]")
            lines.extend(f"  - {item}" for item in items)

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
    return lines


# ---------------------------------------------------------------------------
# decision confirm / dismiss
# ---------------------------------------------------------------------------


#: The remedy printed beside a refused acceptance, naming the flags that
#: supply what the contract found missing.
_ACCEPT_REMEDY = "Supply the missing parts with --reason, --scope or --evidence."


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
@_signing_options
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
    agent: str,
    agent_session: str,
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

    ``--agent`` is how an agent signs as itself. Without it the acceptance is
    recorded as a person's, because without it the identity resolves to the
    repository's git name and a person's is what it would be. It is refused
    unless ``decision config agent-acceptance`` is on for this repository.
    """
    ids, path = _split_ids_and_path(decision_ids)
    repo_path = _resolve_decision_repo(path, fmt)

    kind, signing_session = _signer(agent, agent_session, accepter=accepter)
    granted = _load_policy(repo_path).agent_acceptance if agent else False

    async def _accept(session, rec) -> None:
        from repowise.core.analysis.decisions.accepter import resolve_accepter
        from repowise.core.persistence.crud.authority import accept_decision

        await accept_decision(
            session,
            rec,
            accepter=agent or resolve_accepter(repo_path, override=accepter),
            kind=kind,
            accepter_session=signing_session,
            agent_acceptance=granted,
            reason=reason,
            scope=list(scope) or None,
            evidence=list(evidence) or None,
        )

    results = run_async(
        _review_batch(
            repo_path, ids, action="accepted", verb="accept", preview=preview, apply_one=_accept
        )
    )
    # Both, not either: one run can refuse one id for the policy and another
    # for a missing scope.
    remedy = _ACCEPT_REMEDY
    if agent and not granted:
        remedy = f"{AGENT_ACCEPTANCE_REMEDY} {remedy}"
    if len(ids) > 1 or preview:
        _emit_batch(results, "accepted", "accept", preview, fmt, remedy)
        return
    _emit_single(results[0], ids[0], "accept", fmt, "(governing)", remedy)


@decision_group.command("dismiss")
@click.argument("decision_ids", nargs=-1, required=True)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the confirmation prompt.")
@click.option("--reason", default="", help="Why it was tombstoned.")
@_signing_options
@click.option(
    "--preview", is_flag=True, default=False, help="Report what each id would do, and write nothing."
)
@format_option()
def decision_dismiss(
    decision_ids: tuple[str, ...],
    yes: bool,
    reason: str,
    agent: str,
    agent_session: str,
    preview: bool,
    fmt: str,
) -> None:
    """Dismiss proposed decisions (kept as tombstones; never re-proposed).

    Takes one id or many, and an optional repository path after them.
    """
    ids, path = _split_ids_and_path(decision_ids)
    repo_path = _resolve_decision_repo(path, fmt)
    kind, signing_session = _signer(agent, agent_session)

    if not _dismissal_confirmed(ids, yes=yes, preview=preview, fmt=fmt):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    async def _dismiss(session, rec) -> None:
        from repowise.core.analysis.decisions.accepter import resolve_accepter
        from repowise.core.persistence.crud.authority import dismiss_candidate

        await dismiss_candidate(
            session,
            rec,
            reason=reason,
            accepter=agent or resolve_accepter(repo_path),
            kind=kind,
            accepter_session=signing_session,
        )

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


def _dismissal_confirmed(ids: list[str], *, yes: bool, preview: bool, fmt: str) -> bool:
    """Ask before dismissing, unless ``--yes``, ``--preview`` or json skips the prompt.

    A machine-readable invocation is non-interactive by construction: the
    prompt read EOF and aborted every scripted dismissal.
    """
    if yes or preview or fmt == "json":
        return True
    subject = ids[0][:8] if len(ids) == 1 else f"{len(ids)} decisions"
    return click.confirm(f"Dismiss {subject}?")


# ---------------------------------------------------------------------------
# decision deprecate
# ---------------------------------------------------------------------------


@decision_group.command("deprecate")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
@click.option("--superseded-by", default=None, help="ID of the decision that replaces this one.")
@_signing_options
@format_option()
def decision_deprecate(
    decision_id: str,
    path: str | None,
    superseded_by: str | None,
    agent: str,
    agent_session: str,
    fmt: str,
) -> None:
    """Retire a decision, optionally naming the one that replaces it.

    With ``--superseded-by`` this writes an explicit lineage edge and keeps the
    retired id resolving to its successor. Similarity never does either: an edge
    exists because somebody named the successor.
    """
    repo_path = _resolve_decision_repo(path, fmt)
    kind, signing_session = _signer(agent, agent_session)

    async def _update():
        from repowise.core.persistence import get_session
        from repowise.core.persistence.models import DecisionRecord

        async with _open_store(repo_path) as sf, get_session(sf) as session:
            full_id = await _resolve_decision_id(session, decision_id)
            rec = await session.get(DecisionRecord, full_id) if full_id else None
            if rec is None:
                return None
            successor = (
                await _resolve_decision_id(session, superseded_by) if superseded_by else None
            )
            if superseded_by and successor is None:
                emit_refusal(
                    "decision_not_found",
                    f"Unknown successor: {superseded_by}",
                    fmt,
                    decision_id=superseded_by,
                )
            await _retire(
                session,
                rec,
                successor,
                agent=agent,
                repo_path=repo_path,
                kind=kind,
                signing_session=signing_session,
                fmt=fmt,
            )
            return rec

    _emit_lifecycle(run_async(_update()), decision_id, "deprecated", fmt)


async def _retire(
    session, rec, successor, *, agent, repo_path, kind, signing_session, fmt
) -> None:
    """Supersede an accepted record, or mark any other one deprecated.

    An empty *agent* signs as the repository's person.
    """
    from repowise.core.analysis.decisions.accepter import resolve_accepter
    from repowise.core.persistence import update_decision_status
    from repowise.core.persistence.crud.authority import (
        AcceptanceRefusedError,
        is_accepted,
        supersede_decision,
    )

    if successor and await is_accepted(session, rec.id):
        try:
            await supersede_decision(
                session,
                rec,
                successor_id=successor,
                accepter=agent or resolve_accepter(repo_path),
                kind=kind,
                accepter_session=signing_session,
            )
        except (AcceptanceRefusedError, ValueError) as exc:
            emit_refusal("supersede_refused", str(exc), fmt, decision_id=rec.id)
        return
    # A candidate has no authority to retire: a plain status change. An
    # accepted record reaching here still logs a withdrawal, with its kind.
    await update_decision_status(
        session,
        rec.id,
        "deprecated",
        superseded_by=successor,
        accepter=agent or resolve_accepter(repo_path),
        kind=kind,
    )


# ---------------------------------------------------------------------------
# decision health
# ---------------------------------------------------------------------------


@decision_group.command("health")
@click.argument("path", required=False, default=None)
@format_option()
def decision_health(path: str | None, fmt: str) -> None:
    """Show decision health: stale decisions, proposed, ungoverned hotspots."""
    repo_path = _resolve_decision_repo(path, fmt)
    health = run_async(_query_health(repo_path))

    if fmt == "json":
        # The table caps each list (5 stale, 10 hotspots, 5 proposed) to keep
        # the report short; json carries them whole.
        emit_json(
            {
                "repo": str(repo_path),
                "summary": health["summary"],
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
    _print_health_summary(health["summary"])
    _print_health_lists(health)


async def _query_health(repo_path: Path) -> dict:
    from repowise.core.persistence import (
        get_decision_health_summary,
        get_session,
        upsert_repository,
    )

    async with _open_store(repo_path) as sf, get_session(sf) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        return await get_decision_health_summary(session, repo.id)


def _print_health_summary(summary: dict) -> None:
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


def _print_health_lists(health: dict) -> None:
    """Stale, ungoverned and proposed, each capped so the report stays short."""
    _print_capped(
        "red",
        "Stale decisions",
        health["stale_decisions"],
        5,
        lambda d: f"{d.id[:8]}  {d.title[:50]}  (staleness: {d.staleness_score:.2f})",
    )
    _print_capped("yellow", "Ungoverned hotspots", health["ungoverned_hotspots"], 10, str)
    _print_capped(
        "yellow",
        "Proposed decisions",
        health["proposed_awaiting_review"],
        5,
        lambda d: f"{d.id[:8]}  {d.title[:50]}  (source: {d.source})",
    )


def _print_capped(color: str, heading: str, items: list, cap: int, line) -> None:
    """A headed list with its full count, showing the first *cap* items."""
    if not items:
        return
    console.print(f"\n[{color}]{heading} ({len(items)}):[/{color}]")
    for item in items[:cap]:
        console.print(f"  {line(item)}")
