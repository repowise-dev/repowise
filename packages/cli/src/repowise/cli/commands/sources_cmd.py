"""``repowise sources sync``: fetch from every configured plugin source into the index.

The same sync runs in ``repowise update`` for configured sources
(:func:`sync_after_update`; issue sources at its start, the rest at its end), where a failing source costs one line and never
the update. See :mod:`repowise.core.source_sync`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click
from rich.markup import escape

from repowise.cli.helpers import console, run_async
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console

_log = logging.getLogger("repowise.sources")

_COLORS = {"stored": "green", "unavailable": "yellow", "not_configured": "yellow"}


@click.group(name="sources")
def sources_group() -> None:
    """Fetch from the sources plugins register (a database, a profiler, an issue tracker)."""


async def _sync(repo_path: Path, planned: list[Any]) -> list[dict[str, Any]] | None:
    """Run each configured source in *planned* into the index; ``None`` with no index.

    Each source runs in its own session, committed before its cursor is kept,
    and a source that raises past ``run_fetch`` costs only its own row.
    """
    from repowise.cli.helpers import (
        get_db_url_for_repo,
        load_state,
        reconcile_schema_best_effort,
        save_state,
    )
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.source_sync import ledger_cursor, not_configured, record_result, sync_source

    url = get_db_url_for_repo(repo_path)
    await reconcile_schema_best_effort(url)
    engine = create_engine(url)
    try:
        factory = create_session_factory(engine)
        async with get_session(factory) as session:
            if await get_repository_by_path(session, str(repo_path)) is None:
                return None
        results = []
        for p in planned:
            if not p.configured:
                results.append(not_configured(p))
                continue
            cursor = ledger_cursor(load_state(repo_path), p)
            try:
                async with get_session(factory) as session:
                    repo = await get_repository_by_path(session, str(repo_path))
                    result, fetched = await sync_source(
                        p,
                        session=session,
                        repo=repo,
                        repo_path=repo_path,
                        cursor=cursor,
                    )
                # Only now, with the batches committed, may the cursor move past them.
                cursor = fetched
            except Exception as exc:  # one source must not cost the others
                _log.warning("source %s failed: %s", p.ledger_id, type(exc).__name__)
                result = {
                    "source": p.name,
                    "seam": p.seam.name,
                    "state": "failed",
                    "batches": 0,
                    "message": type(exc).__name__,
                    "next_command": "repowise plugins",
                    "missing": [],
                }
            # Read again just before the write: a fetch can take a while, and
            # this keeps whatever else wrote state.json meanwhile.
            state = load_state(repo_path)
            record_result(state, p, result, cursor)
            save_state(repo_path, state)
            results.append(result)
        return results
    finally:
        await engine.dispose()


def _line(r: dict[str, Any]) -> str:
    """One source's result, ending with the command to run next."""
    color = _COLORS.get(r["state"], "red")
    if r["state"] == "stored":
        what = f"stored {r['batches']} batch{'es' if r['batches'] != 1 else ''}"
    else:
        what = r["state"].replace("_", " ")
        if r["message"]:
            what += f": {r['message']}"
    head = f"{r['seam']} [bold]{escape(r['source'])}[/bold]: [{color}]{escape(what)}[/{color}]"
    return f"{head}. Next: [cyan]{escape(r['next_command'])}[/cyan]"


def _planned(repo_path: Path, only: str | None, fmt: str) -> list[Any]:
    """The sources to report, narrowed to *only*; refuses an unreadable config or unknown name."""
    from repowise.core.repo_config import RepoConfigError, load_repo_config
    from repowise.core.source_sync import plan_sources

    try:
        planned = plan_sources(load_repo_config(repo_path))
    except RepoConfigError as exc:
        emit_refusal(
            "config_unreadable", str(exc), fmt, remedy="Fix the file, then run this again."
        )
        raise AssertionError("unreachable") from None
    if only is None:
        return planned
    planned = [p for p in planned if p.name == only]
    if not planned:
        emit_refusal(
            "unknown_source",
            f"No source named {only} is registered.",
            fmt,
            remedy="`repowise plugins` lists each source under its seam.",
            next_command="repowise plugins",
        )
    return planned


def _say_none_installed(repo_path: Path, fmt: str) -> None:
    if fmt == "json":
        emit_json({"repo": str(repo_path), "sources": [], "next_command": "repowise plugins"})
        return
    notice_console(fmt).print(
        "No plugin source is installed. Install a plugin that registers one "
        "(an evidence-source). Next: [cyan]repowise plugins[/cyan]"
    )


@sources_group.command("sync")
@click.option("--source", "only", default=None, help="Sync only the source with this name.")
@click.option("--path", default=None, help="Repo path (defaults to cwd).")
@format_option()
def sources_sync_command(only: str | None, path: str | None, fmt: str) -> None:
    """Fetch from every configured source and store what it returns.

    A source is configured once every setting it needs has a value. Each one
    prints what it stored, or why it did not and the command to run next.
    Exits non-zero only when the source named by --source is unknown or did
    not store.
    """
    from repowise.cli.helpers import resolve_command_target

    repo_path = resolve_command_target(path=path).single_repo_path()
    planned = _planned(repo_path, only, fmt)
    if not planned:
        _say_none_installed(repo_path, fmt)
        return
    results = run_async(_sync(repo_path, planned))
    if results is None:
        emit_refusal(
            "no_index",
            "This repository has no index to store into.",
            fmt,
            remedy="Run `repowise init`, then sync again.",
            next_command="repowise init",
        )
        return
    if fmt == "json":
        emit_json({"repo": str(repo_path), "sources": results})
    else:
        for r in results:
            console.print(_line(r))
    if only is not None and any(r["state"] != "stored" for r in results):
        raise click.exceptions.Exit(1)


def sync_after_update(repo_path: Path, *, before: bool = False) -> None:
    """Run every configured source after ``repowise update``; never raises.

    With *before*, run instead the sources whose seam syncs at the start of
    the update (``issue-source``), so the update reads what they stored.
    With no configured source this reads nothing past the plugin list and
    writes nothing. A source that fails prints one line and the update
    carries on.
    """
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        config = load_repo_config(repo_path)
    except RepoConfigError:
        return  # the update reports a broken config itself
    try:
        from repowise.core.source_sync import plan_sources

        planned = [
            p for p in plan_sources(config) if p.configured and p.seam.before_update == before
        ]
        if not planned:
            return
        results = run_async(_sync(repo_path, planned)) or []
    except Exception as exc:  # a source must never fail the update
        _log.warning("source sync skipped: %s", type(exc).__name__)
        console.print(
            f"[yellow]Sources not synced ({escape(type(exc).__name__)}).[/yellow] "
            "Next: [cyan]repowise sources sync[/cyan]"
        )
        return
    for r in results:
        console.print(f"Sources: {_line(r)}")
