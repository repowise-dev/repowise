"""Shared plumbing for the four MCP-tool adapter commands.

``ask``, ``context``, ``symbol`` and ``why`` all do the same three things
before they differ: resolve one indexed repo, decide whether the caller wants
the CLI projection or the raw tool dict, and emit a document even when there is
nothing to say. This module owns those three so the four commands stay thin.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import click

from repowise.cli.output import emit_json, format_option, full_option
from repowise.cli.output import notice_console as _notices


def target_options(fn: Callable) -> Callable:
    """The ``--path --repo --no-workspace --format --full`` option block.

    The repo is an option here rather than the trailing positional ``PATH``
    the older commands take. ``context`` accepts a variadic list of targets,
    which would swallow a trailing path, and on the other three a bare second
    positional would be ambiguous with the query. One spelling across the four
    adapters beats matching a convention only three of them could keep.

    Applied bottom-up the way Click stacks decorators, so the resulting
    ``--help`` lists them in the order written here.
    """
    for decorate in reversed(
        (
            click.option(
                "--path",
                "path",
                default=None,
                type=click.Path(exists=True, file_okay=False),
                help="Repo (or workspace) root. Defaults to the current directory.",
            ),
            click.option(
                "--repo",
                "repo_alias",
                default=None,
                help="Workspace repo alias to query (implies workspace mode).",
            ),
            click.option(
                "--no-workspace",
                is_flag=True,
                default=False,
                help="Force single-repo mode even when invoked from a workspace.",
            ),
            format_option(),
            full_option(),
        )
    ):
        fn = decorate(fn)
    return fn


def resolve_format_for(fmt: str, full: bool) -> str:
    """``--full`` implies json: the raw tool dict has no table rendering."""
    return "json" if full else fmt


def resolve_indexed_repo(
    *,
    path: str | None,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
    command: str,
) -> Path:
    """The one repo these commands run against, or a ``ClickException``.

    Workspace mode picks a single repo rather than fanning out: the tools are
    single-repo by construction (see ``tool_bridge``), so ``--repo <alias>``
    selects whose resources get wired and the primary repo is the default.
    """
    from repowise.cli.helpers import resolve_command_target

    target = resolve_command_target(
        path=path,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(_notices(fmt), command=command)

    if target.is_workspace:
        if target.repo_filter is not None:
            picked = target.resolve_repo_alias(target.repo_filter)
            if picked is None:
                raise click.ClickException(f"Unknown repo alias: {target.repo_filter}")
            repo_path = picked
        else:
            primary = target.primary_path()
            if primary is None:
                raise click.ClickException("Workspace has no primary repo configured.")
            repo_path = primary
    else:
        assert target.repo_path is not None
        repo_path = target.repo_path

    from repowise.cli.helpers import REPOWISE_DIR

    if not (repo_path / REPOWISE_DIR).is_dir():
        raise click.ClickException(
            f"{repo_path} is not indexed. Run 'repowise init' there first."
        )
    return repo_path


def emit_full(payload: dict) -> None:
    """``--full``: the raw tool dict, and a non-zero exit if it is an error.

    Same rule as :func:`emit_error` — the document is always emitted, and the
    status still tells the truth. ``--full`` is exactly the spelling a script
    reaches for, so this is the path where a failed lookup exiting 0 would do
    the most damage.
    """
    emit_json(payload)
    if payload.get("error"):
        raise click.exceptions.Exit(1)


def run(repo_path: Path, factory: Callable[[], Awaitable[dict]], tool_name: str) -> dict:
    """Await one tool coroutine with this repo's resources published.

    Logs are silenced here rather than on the format option, because these
    four commands need it at *every* format. ``format_option``'s callback only
    fires for a machine-readable format, and ``ask`` synthesises through an LLM
    provider that emits three structlog debug lines per call — on stdout, where
    structlog's unconfigured default writes. That corrupts the JSON document,
    but it also lands three timestamped lines in the middle of the human answer
    and inside anything reading it through ``repowise distill``. A tool call's
    own logs are never any of these commands' output.
    """
    from repowise.cli.helpers import silence_logs_for_machine_output
    from repowise.cli.tool_bridge import call_tool

    silence_logs_for_machine_output()
    return call_tool(repo_path, factory, tool_name)


#: MCP tool name -> the CLI command that now does the same thing.
#:
#: The tools write their hints for an agent holding the MCP surface, so a
#: not-found from ``get_symbol`` reads "Use get_context to list available
#: symbols" — advice naming something a CLI user cannot run. Rewriting the
#: names is cheaper and more honest than forking the messages.
_TOOL_TO_COMMAND = {
    "get_answer": "repowise ask",
    "get_context": "repowise context",
    "get_symbol": "repowise symbol",
    "get_why": "repowise why",
    "search_codebase": "repowise search",
}


def as_cli_prose(text: str) -> str:
    """Rewrite MCP tool names in a message meant for a human at a terminal.

    Applied only on the table path. A JSON consumer is reading the tool's own
    projection and is better served by the tool's own vocabulary, so the
    payload keeps the names it was given.
    """
    for tool, command in _TOOL_TO_COMMAND.items():
        text = text.replace(tool, command)
    return text


#: Keys the tools attach *beside* ``error`` to make it recoverable.
#:
#: ``suggestions`` is ``get_symbol``'s did-you-mean list, and its error message
#: literally ends "retry with one of these exact symbol_ids" — dropping the
#: list leaves a message pointing at nothing. ``remedy`` and ``guidance`` come
#: from the failure shield's shaped responses (an unindexed repo, an unknown
#: alias) and are the only part of those that says what to do.
_ERROR_COMPANIONS = ("suggestions", "remedy", "guidance")


def emit_error(payload: dict, fmt: str, *, extra: dict | None = None) -> None:
    """Render a tool's ``error`` key, if it has one, and exit non-zero.

    Every early return owes stdout a document — a json path that exits with
    only a stderr notice is indistinguishable from a crash to whatever is
    reading the pipe. Having emitted it, the command still exits non-zero:
    ``symbol`` on a name that does not exist is a failed lookup, and a script
    that only checks the exit code must not read it as a hit.
    """
    error = payload.get("error")
    if not error:
        return
    companions = {key: payload[key] for key in _ERROR_COMPANIONS if payload.get(key)}
    if fmt == "json":
        emit_json({**(extra or {}), "error": error, **companions})
    else:
        # Escaped: a shaped internal error interpolates the exception verbatim,
        # and exception text routinely carries brackets (``list[str]``, a repr'd
        # list of aliases). Unescaped, a stray closing tag raises MarkupError
        # and the command dies with an empty stdout — which is the exact state
        # this function exists to prevent.
        from rich.markup import escape

        notices = _notices(fmt)
        notices.print(f"[red]{escape(as_cli_prose(str(error)))}[/red]")
        for value in companions.get("suggestions") or []:
            notices.print(f"  [cyan]{escape(str(value))}[/cyan]")
        for key in ("remedy", "guidance"):
            if companions.get(key):
                notices.print(f"[dim]{escape(as_cli_prose(str(companions[key])))}[/dim]")
    raise click.exceptions.Exit(1)


def owner_share(value: object) -> str:
    """Render an ownership share, which is a fraction *or* a percentage.

    Its source stores either, depending on which git-metadata path filled it in
    — ``developer_congestion`` already normalises the same field the same way.
    Printing it raw shows a dominant author as "0.99%". ``why`` reads it as
    ``author_commit_pct`` and ``risk`` as ``owner_pct``; same column, so one
    copy of the normalisation.
    """
    try:
        pct = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    return f"{pct * 100 if pct <= 1.0 else pct:.0f}%"


def index_note(payload: dict) -> dict[str, Any]:
    """The freshness bits of a tool's ``_meta``, as the projections carry them.

    ``_meta`` is otherwise dropped from a projection: ``timing_ms`` and the
    token accounting describe the call, not the answer. What survives is the
    part that tells a caller whether to trust it.
    """
    meta = payload.get("_meta") or {}
    note: dict[str, Any] = {}
    for key in ("indexed_commit", "live_head", "index_behind", "index_age_days"):
        if key in meta:
            note[key] = meta[key]
    # ``index_behind: false`` exists in ``_meta`` so telemetry can tell "current"
    # apart from "never checked". A projection is read by an agent, not an
    # aggregator, and "the index is not behind" is already what silence says.
    if note.get("index_behind") is False:
        note.pop("index_behind")
    if meta.get("stale_warning"):
        note["stale_warning"] = meta["stale_warning"]
    return note


def print_index_note(payload: dict, fmt: str) -> None:
    """Say so on the human path when the index is behind or stale."""
    meta = payload.get("_meta") or {}
    notices = _notices(fmt)
    if meta.get("stale_warning"):
        notices.print(f"[yellow]{meta['stale_warning']}[/yellow]")
    elif meta.get("index_behind"):
        notices.print(
            f"[dim]Index is behind HEAD: indexed {meta.get('indexed_commit', '?')}, "
            f"now {meta.get('live_head', '?')}. Run 'repowise update' to resync.[/dim]"
        )


__all__ = [
    "as_cli_prose",
    "emit_error",
    "emit_full",
    "index_note",
    "owner_share",
    "print_index_note",
    "resolve_format_for",
    "resolve_indexed_repo",
    "run",
    "target_options",
]
