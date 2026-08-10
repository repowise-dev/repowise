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
        help=help,
    )


def emit_json(payload: Any) -> None:
    """Write *payload* to stdout as JSON, the way the existing commands do.

    ``default=str`` keeps a stray non-serialisable value (a ``Decimal`` or
    ``datetime`` off a database row) from turning a working command into a
    traceback; the field degrades to its string form instead.
    """
    click.echo(json.dumps(payload, indent=2, default=str))


__all__ = ["NON_TTY_WIDTH", "emit_json", "format_option", "resolve_console_width"]
