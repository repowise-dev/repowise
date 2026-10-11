"""Resolve decision ids, and apply one lifecycle verb to many of them.

Shared by the ``decision`` commands in ``decision_cmd``.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import console, get_db_url_for_repo
from repowise.cli.output import emit_json, emit_refusal


@asynccontextmanager
async def _open_store(repo_path: Path):
    """Yield a session factory on the repo's store; the engine is disposed after.

    A factory, so ``add`` can embed after its session commits.
    """
    from repowise.core.persistence import create_engine, create_session_factory, init_db

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        await init_db(engine)
        yield create_session_factory(engine)
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


#: A decision id, or a prefix of one, as ``_resolve_decision_id`` accepts it.
_ID_SHAPED = re.compile(r"[0-9a-fA-F]{4,64}\Z")


def _split_ids_and_path(tokens: tuple[str, ...]) -> tuple[list[str], str | None]:
    """Separate decision ids from the optional trailing repo path.

    ``confirm ID [PATH]`` shipped before batch arity, so the path is still a
    positional. An id-shaped last token stays an id even when a directory of
    that name happens to exist, because reading one as a path would drop it
    from the batch and still exit 0.
    """
    last = tokens[-1] if len(tokens) > 1 else ""
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
            await apply_one(session, rec)
            if preview:
                raise _PreviewRollbackError
    except _PreviewRollbackError:
        return {**entry, "ok": True, "action": f"would_{verb}"}
    except AcceptanceRefusedError as exc:
        return {
            **entry,
            "ok": False,
            "error": "acceptance_refused",
            "message": str(exc),
            "blockers": list(exc.blockers),
        }
    return {**entry, "ok": True, "action": action, "status": rec.status}


def _emit_batch(
    results: list[dict], action: str, verb: str, preview: bool, fmt: str, remedy: str = ""
) -> None:
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
    """Report a one-id run: the transition, or the refusal naming that id."""
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
