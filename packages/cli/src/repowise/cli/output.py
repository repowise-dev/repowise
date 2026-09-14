"""The CLI's single output seam: console width policy and machine-readable output.

Two problems this module owns, both of which only bite non-interactive
consumers (agents, pipes, CI):

1. **Width.** Rich sizes a non-terminal ``Console`` at 80 columns — *narrower*
   than any real terminal. So the consumer that cannot ask for the rest is the
   one whose output gets truncated hardest: at 80 columns ``repowise search``
   renders every path as ``packages/core…``, which cannot be opened, grepped,
   or even recognised as truncated without counting characters.
   :func:`resolve_console_width` is the fix, applied once where the shared
   consoles are built (``helpers.py``) rather than at each of the ~36 table
   call sites.

2. **Format.** A rich table is not a machine format at any width: widening it
   stops the ellipsis, but folding a long path across three lines inside box
   drawing is no more parseable. Structured output is the real answer, and
   :func:`format_option` / :func:`emit_json` are the one spelling of it, so the
   CLI stops growing a new flag name per command (it already has four:
   ``--format``, ``--json``, ``--output``, ``--progress``).
"""

from __future__ import annotations

import json
import os
from typing import IO, Any

import click

#: Width used for a non-interactive stream. Rich sizes tables to their content,
#: so a generous value costs nothing when rows are short — measured on this
#: repo, ``repowise search`` emits 4,318 chars with 14 ellipses at 200 columns
#: and 3,620 chars with none at 400, i.e. the wider rendering is both complete
#: *and* smaller. 400 carries enough headroom for a deep monorepo path in a
#: multi-column table.
#:
#: Ceiling: ``Panel`` expands to the full width, so a panel printed to a pipe
#: costs ~1.2K chars here against ~240 at 80 columns. That is affordable today
#: because only ``decision`` prints a panel on a result path; the rest live in
#: the interactive ``ui/`` flows, which only render to a real terminal. If a
#: future command prints panels to a pipe, give it ``expand=False`` rather than
#: narrowing this constant and reintroducing truncation everywhere.
NON_TTY_WIDTH = 400


def resolve_console_width(stream: IO[str]) -> int | None:
    """Width for a shared :class:`~rich.console.Console` writing to *stream*.

    ``None`` means "let rich decide" — it reads ``COLUMNS`` and then the real
    terminal size. An explicit width is returned only for the non-interactive
    case rich would otherwise render at 80 columns.

    ``COLUMNS`` still wins when set, so an operator (or a benchmark harness
    pinning a width deliberately) keeps full control.

    Known limitation: this cannot tell a deliberate ``COLUMNS`` from an
    inherited one. Some shells and CI runners export ``COLUMNS=80``, and in
    that environment the truncation fix silently does not apply. Honouring the
    variable is the standard contract and a benchmark pinning a narrow width
    depends on it, so it is honoured unconditionally rather than second-guessed
    by comparing against a threshold — but it does mean "a non-TTY never
    truncates" holds only when ``COLUMNS`` is unset.
    """
    if os.environ.get("COLUMNS"):
        return None
    try:
        if stream.isatty():
            return None
    except Exception:
        # A stream that cannot answer isatty() (a captured buffer, a closed
        # handle) is not a terminal for our purposes.
        pass
    return NON_TTY_WIDTH


def _silence_when_machine_readable(ctx: Any, param: Any, value: str) -> str:
    """Turn log output off the moment a machine-readable format is selected.

    Structlog's unconfigured default writes to **stdout**, so one ``info()``
    call anywhere below a command lands inside its JSON document. Doing this in
    the option's callback rather than in each command body means it holds for
    every consumer of :func:`format_option`, including ones added later, and
    it runs at parse time — before any command body has had a chance to import
    a module that logs on import.
    """
    if value != "table":
        from repowise.cli.helpers import silence_logs_for_machine_output

        silence_logs_for_machine_output()
    return value


def format_option(
    *,
    choices: tuple[str, ...] = ("table", "json"),
    default: str = "table",
    help: str = "Output format.",
) -> Any:
    """The CLI's one ``--format`` option, bound to the ``fmt`` parameter.

    Matches the spelling the commands that already have a machine-readable
    mode use (``dead-code``, ``doctor``, ``health``, ``impacted-tests``,
    ``risk``), so adopting it elsewhere adds no new convention.
    """
    return click.option(
        "--format",
        "fmt",
        type=click.Choice(list(choices)),
        default=default,
        callback=_silence_when_machine_readable,
        help=help,
    )


def _silence_when_alias_selects_json(ctx: Any, param: Any, value: bool) -> bool:
    """:func:`_silence_when_machine_readable` for the boolean ``--json`` alias.

    The alias selects json while ``--format`` never leaves ``table``, so it has
    to silence logs itself or the one payload that can still be corrupted is
    the one a legacy caller asked for.
    """
    if value:
        from repowise.cli.helpers import silence_logs_for_machine_output

        silence_logs_for_machine_output()
    return value


def json_option(*, help: str = "Deprecated alias for --format json.") -> Any:
    """A hidden ``--json`` flag, for commands that shipped that spelling first.

    ``hook stats``, ``workspace check/diagnostics/metrics`` and (as
    ``--output``) ``security scan`` were machine-readable before ``--format``
    was the convention. Removing their flag would break every script and CI
    job already calling them, so it stays and stays working — just hidden from
    ``--help``, so the documented surface is one flag rather than four.

    Bound to ``as_json``; combine with :func:`format_option` and resolve the
    pair through :func:`resolve_format`.
    """
    return click.option(
        "--json",
        "as_json",
        is_flag=True,
        default=False,
        hidden=True,
        callback=_silence_when_alias_selects_json,
        help=help,
    )


def full_option(*, help: str | None = None) -> Any:
    """``--full``: emit the underlying tool payload instead of the CLI projection.

    ``ask``, ``context``, ``symbol`` and ``why`` are adapters over the MCP tool
    functions, and those return considerably more than a command needs to show
    — ``get_context`` on two files is ~20K chars, most of it skeleton source.
    The default is therefore a trimmed projection and the raw dict is opt-in,
    so payload size is a switch a caller (or a benchmark) can measure both
    sides of rather than a choice baked into the command.

    A raw tool dict has no table rendering, so this implies ``--format json``.
    It carries the same log-silencing callback as :func:`json_option` for that
    reason: ``--full`` alone leaves ``--format`` on ``table``, so without it the
    one payload that stays corruptible is the one asked for by name.
    """
    return click.option(
        "--full",
        "full",
        is_flag=True,
        default=False,
        callback=_silence_when_alias_selects_json,
        help=help or "Emit the complete tool payload as JSON (implies --format json).",
    )


def resolve_format(fmt: str, as_json: bool) -> str:
    """Fold a legacy boolean alias into the ``--format`` value.

    The alias can only ever *select* json, never deselect it, so
    ``--format json`` and ``--json`` agree and passing both is not an error.
    """
    return "json" if as_json else fmt


def notice_console(fmt: str) -> Any:
    """The console a command's human-facing asides should print to.

    Under ``--format json`` stdout has to be one parseable document, so every
    notice, warning and tip moves to stderr rather than being suppressed — an
    agent's ``jq`` still works and a human running the command still sees why
    the payload looks the way it does. Under any other format this is the
    ordinary stdout console and nothing moves.

    Only covers prints the command itself makes. A print in a module it calls
    is invisible from here and has to be fixed at its own source (see
    ``providers/vector_store.py``).
    """
    from repowise.cli.helpers import console, err_console

    return err_console if fmt == "json" else console


def emit_json(payload: Any) -> None:
    """Write *payload* to stdout as JSON, the way the existing commands do.

    ``default=str`` keeps a stray non-serialisable value (a ``Decimal`` or
    ``datetime`` off a database row) from turning a working command into a
    traceback; the field degrades to its string form instead.
    """
    click.echo(json.dumps(payload, indent=2, default=str))


__all__ = [
    "NON_TTY_WIDTH",
    "emit_json",
    "format_option",
    "full_option",
    "json_option",
    "notice_console",
    "resolve_console_width",
    "resolve_format",
]


def emit_refusal(code: str, message: str, fmt: str, **extra: Any) -> None:
    """Report a refused operation and exit non-zero.

    A ``--format json`` caller is owed a document on every exit, including this
    one: raising a ``ClickException`` prints prose on the channel the caller is
    parsing, and the difference between a refusal and a crash disappears.
    """
    import click

    if fmt == "json":
        emit_json({"error": code, "message": message, **extra})
    else:
        from rich.markup import escape

        notices = notice_console(fmt)
        notices.print(f"[red]{escape(message)}[/red]")
        if extra.get("remedy"):
            notices.print(f"[dim]{escape(str(extra['remedy']))}[/dim]")
    raise click.exceptions.Exit(1)
