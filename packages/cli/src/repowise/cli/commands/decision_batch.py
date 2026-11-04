"""Resolve decision ids, and apply one lifecycle verb to many of them.

Shared by the ``decision`` commands in ``decision_cmd``.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import console, get_db_url_for_repo
from repowise.cli.output import emit_json, emit_refusal
from repowise.core.agents.identity import is_agent_slug
from repowise.core.analysis.decisions.lifecycle import ACCEPTER_SESSION_MAX


@asynccontextmanager
async def _open_store(repo_path: Path):
    """Yield a session factory on the repo's store; the engine is disposed after.

    A factory, so ``add`` can embed after its session commits. A locked or
    unreadable store is reported as one line, not a SQL traceback.
    """
    from sqlalchemy.exc import OperationalError

    from repowise.core.persistence import create_engine, create_session_factory, init_db

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        await init_db(engine)
        yield create_session_factory(engine)
    except OperationalError as exc:
        raise click.ClickException(
            f"Could not use the index at {repo_path}: {exc.orig}. "
            "If `repowise update` is running, retry once it finishes."
        ) from exc
    finally:
        await engine.dispose()


async def _resolve_decision_id(session, decision_id: str) -> str | None:
    """Expand a (possibly truncated) decision id to the full stored id.

    ``decision list`` prints 8-char prefixes, so every id-taking subcommand
    accepts a unique prefix. Returns None when nothing matches; raises on an
    ambiguous prefix.
    """
    from repowise.core.persistence.models import DecisionAlias, DecisionRecord

    full_id = await _match_prefix(session, DecisionRecord.id, DecisionRecord.id, decision_id)
    if full_id is not None:
        return full_id
    # Merging and superseding retire ids that are already written down
    # somewhere. Resolving through the alias keeps those working instead of
    # reporting the decision as gone.
    return await _match_prefix(
        session, DecisionAlias.decision_id, DecisionAlias.alias_id, decision_id
    )


async def _match_prefix(session, selected, matched, prefix: str) -> str | None:
    """*selected* from the one row whose *matched* column starts with *prefix*."""
    from sqlalchemy import select

    from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like

    result = await session.execute(
        select(selected)
        .where(matched.like(f"{escape_like(prefix)}%", escape=LIKE_ESCAPE))
        .limit(2)
    )
    ids = [row[0] for row in result.all()]
    if len(ids) > 1:
        raise click.ClickException(
            f"Decision id prefix {prefix!r} is ambiguous; use more characters."
        )
    return ids[0] if ids else None


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


class _PreviewRollbackError(Exception):
    """Signals a preview to roll its savepoint back."""


class _RefusedError(ValueError):
    """A verb's own refusal of one id, carrying the error code to report."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error


async def _refuse_if_governing(session, rec) -> None:
    """Refuse to accept a decision that already governs; ``reaffirm`` renews one.

    A withdrawn decision is let through: accepting it again is how it returns.
    """
    from repowise.core.analysis.decisions.lifecycle import HISTORY_CURRENCIES
    from repowise.core.persistence.crud.authority import current_currency

    currency = await current_currency(session, rec)
    if currency is not None and currency not in HISTORY_CURRENCIES:
        raise _RefusedError(
            "already_accepted",
            f"already a decision ({currency}); renew it with "
            f"`repowise decision reaffirm {rec.id[:8]}`",
        )


#: A decision id, or a prefix of one, as ``_resolve_decision_id`` accepts it.
_ID_SHAPED = re.compile(r"[0-9a-fA-F]{4,64}\Z")


def _require_ids(ids: list[str], verb: str, fmt: str) -> None:
    """Refuse a run that names no decision, pointing at where ids come from."""
    if not ids:
        emit_refusal(
            "no_decision_ids",
            f"Name the decision ids to {verb}.",
            fmt,
            remedy="List candidates and their ids with `repowise decision candidates`.",
        )


def _split_ids_and_path(tokens: tuple[str, ...]) -> tuple[list[str], str | None]:
    """Separate decision ids from the optional trailing repo path.

    ``confirm ID [PATH]`` shipped before batch arity, so the path is still a
    positional. An id-shaped last token stays an id even when a directory of
    that name happens to exist, because reading one as a path would drop it
    from the batch and still exit 0. A lone directory is the path, so
    ``confirm .`` reports that no id was named rather than an unknown id.
    """
    last = tokens[-1] if tokens else ""
    # An empty token would otherwise read as the current directory.
    if last and not _ID_SHAPED.match(last) and Path(last).is_dir():
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
    from repowise.core.persistence import get_session

    results: list[dict] = []
    async with _open_store(repo_path) as sf, get_session(sf) as session:
        for token in tokens:
            # A token that resolves to nothing already carries its outcome.
            rec, outcome = await _resolve_one(session, token)
            if outcome is None:
                outcome = await _apply_in_savepoint(
                    session, rec, token, action=action, verb=verb, preview=preview,
                    apply_one=apply_one,
                )
            results.append(outcome)
        if preview:
            await session.rollback()
    return results


async def _apply_in_savepoint(
    session, rec, token: str, *, action: str, verb: str, preview: bool, apply_one
) -> dict:
    """One id's outcome: applied, would-apply under preview, or refused."""
    from repowise.core.persistence.crud.authority import AcceptanceRefusedError

    entry = {"given": token, "id": rec.id, "title": rec.title}
    try:
        async with session.begin_nested():
            # A verb may add fields to its outcome, e.g. where a merge went.
            extra = await apply_one(session, rec) or {}
            if preview:
                raise _PreviewRollbackError(extra)
    except _PreviewRollbackError as stop:
        return {**entry, "ok": True, "action": f"would_{verb}", **stop.args[0]}
    except AcceptanceRefusedError as exc:
        return {
            **entry,
            "ok": False,
            "error": "acceptance_refused",
            "message": str(exc),
            "blockers": list(exc.blockers),
        }
    except ValueError as exc:
        # The verb's own refusal, e.g. a merge target that is not a decision.
        # A corrupt stored JSON column is a fault, not a refusal.
        if isinstance(exc, json.JSONDecodeError):
            raise
        error = exc.error if isinstance(exc, _RefusedError) else f"{verb}_refused"
        return {**entry, "ok": False, "error": error, "message": str(exc)}
    return {**entry, "ok": True, "action": action, "status": rec.status, **extra}


def _emit_batch(
    results: list[dict], action: str, verb: str, preview: bool, fmt: str, remedy: str = ""
) -> None:
    """Report a run, exiting non-zero when any id was refused.

    Every ``--format json`` review run lands here, one id or many, so an
    agent parses one shape. *remedy* names the contract's flags, so it is
    shown only when the contract refused something.
    """
    failed = [r for r in results if not r["ok"]]
    if not any("blockers" in r for r in failed):
        remedy = ""
    if fmt == "json":
        emit_json(
            {
                "action": action,
                "preview": preview,
                "results": results,
                "succeeded": len(results) - len(failed),
                "failed": len(failed),
                # Once for the run: every refusal here is the same verb.
                **({"remedy": remedy} if failed and remedy else {}),
            }
        )
    else:
        _print_batch(results, len(failed), action, verb, preview, remedy)
    if failed:
        raise click.exceptions.Exit(1)


def _print_batch(
    results: list[dict], failed: int, action: str, verb: str, preview: bool, remedy: str
) -> None:
    headline = f"Would {verb}" if preview else action.capitalize()
    table = Table(title=f"{headline} {len(results) - failed} of {len(results)}")
    for column in ("ID", "Title", "Outcome"):
        table.add_column(column)
    for result in results:
        outcome = "[green]ok[/green]" if result["ok"] else f"[red]{result['message']}[/red]"
        table.add_row(result.get("id", result["given"])[:8], result.get("title", "")[:50], outcome)
    console.print(table)
    if preview:
        console.print("[dim]Nothing was written. Re-run without --preview.[/dim]")
    if failed and remedy:
        console.print(f"[dim]{remedy}[/dim]")


def _emit_single(result: dict, token: str, verb: str, fmt: str, note: str, remedy: str) -> None:
    """Report a one-id run to a person: the transition, or the refusal naming that id.

    ``--format json`` goes through :func:`_emit_batch` instead, for one shape.
    """
    if result["ok"]:
        console.print(
            f"[green]Decision {result['id'][:8]} {result['action']}[/green]"
            + (f" {note}" if note else "")
        )
        return
    if result["error"] == "decision_not_found":
        _emit_lifecycle(None, token, "", fmt)
        return
    if "id" not in result:
        # An ambiguous prefix has no record, only the token the caller gave.
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


@dataclass(frozen=True)
class _Signer:
    """Who signs an acceptance-log row: an agent by slug, or the repository's person."""

    agent: str
    kind: str
    session: str

    def name(self, repo_path: Path, override: str = "") -> str:
        from repowise.core.analysis.decisions.accepter import resolve_accepter

        return self.agent or resolve_accepter(repo_path, override=override)


def _signer(agent: str, agent_session: str, *, accepter: str = "") -> _Signer:
    """Validate the signing flags and return who they sign as.

    Shared by every verb that appends to the acceptance log, so an agent can
    say it is one wherever it can act.
    """
    _check_agent_flag(agent, accepter)
    _check_session_flag(agent_session, agent)
    return _Signer(agent=agent, kind="agent" if agent else "person", session=agent_session)


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


def _preview_option(command):
    """``--preview``: run the write, report each id's outcome, roll it back."""
    return click.option(
        "--preview",
        is_flag=True,
        default=False,
        help="Report what each id would do, and write nothing.",
    )(command)


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
