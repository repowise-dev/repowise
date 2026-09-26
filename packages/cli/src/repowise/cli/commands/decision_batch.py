"""Resolve decision ids, and apply one lifecycle verb to many of them.

``confirm`` and ``dismiss`` take any number of ids and report each one's
outcome; ``show`` and ``deprecate`` resolve a single id the same way. The
command bodies live in ``decision_cmd``; this is the part they share that is
about ids and runs rather than about any one verb.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import console, get_db_url_for_repo
from repowise.cli.output import emit_json, emit_refusal


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
        if remedy:
            console.print(f"[dim]{remedy}[/dim]")
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
