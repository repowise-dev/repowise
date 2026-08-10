"""``repowise symbol`` — the CLI adapter over the ``get_symbol`` MCP tool."""

from __future__ import annotations

import click

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.output import emit_json

#: Everything the tool returns except ``_meta``, in the order it returns it.
#: ``get_symbol`` is the one tool whose payload is already its answer — the
#: source body — so the projection is the whole dict minus the call envelope,
#: not a selection from it. Keys absent from a given response are skipped.
_KEPT = (
    "symbol_id",
    "file",
    "name",
    "kind",
    "qualified_name",
    "signature",
    "language",
    "start_line",
    "end_line",
    "symbol_start_line",
    "symbol_end_line",
    "bounds",
    "verified",
    "source",
    "truncated",
    "continuation",
    "candidates",
    "fallback_lines",
    "note",
)


def project(payload: dict) -> dict:
    """``get_symbol``'s dict, minus ``_meta``.

    Field mapping:

    ==================  ===========================================
    kept                every key in ``_KEPT`` that is present,
                        including ``source``, ``candidates`` and
                        ``continuation``, plus ``index`` from the
                        freshness half of ``_meta``
    dropped             ``_meta``'s timing and token accounting
    ==================  ===========================================

    Nothing else is trimmed on purpose. Dropping ``candidates`` would silently
    pick one of an ambiguous id's bodies, and dropping ``continuation`` would
    make a truncated body un-continuable — both change the answer rather than
    its size.
    """
    out = {key: payload[key] for key in _KEPT if key in payload}
    note = _ta.index_note(payload)
    if note:
        out["index"] = note
    return out


@click.command("symbol")
@click.argument("symbol_id")
@click.option(
    "--context-lines",
    type=click.IntRange(0, 50),
    default=0,
    help="Extra lines before and after the body.",
)
@click.option(
    "--query",
    default=None,
    help="Omission refs only: regex or substring filter on the restored lines.",
)
@_ta.target_options
def symbol_command(
    symbol_id: str,
    context_lines: int,
    query: str | None,
    path: str | None,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
    full: bool,
) -> None:
    """Read one symbol's body with live-verified line bounds.

    SYMBOL_ID is "path/to/file.py::Name" (as 'repowise context' reports it),
    "path/to/file.py:140-180" for a live range read, or a "repowise#<hex>"
    omission ref from a distilled command.
    """
    fmt = _ta.resolve_format_for(fmt, full)
    repo_path = _ta.resolve_indexed_repo(
        path=path,
        repo_alias=repo_alias,
        no_workspace=no_workspace,
        fmt=fmt,
        command="symbol",
    )

    def _factory():
        from repowise.server.mcp_server.tool_symbol import get_symbol

        return get_symbol(
            symbol_id=symbol_id,
            context_lines=context_lines,
            query=query,
        )

    payload = _ta.run(repo_path, _factory)

    if full:
        emit_json(payload)
        return
    _ta.emit_error(payload, fmt, extra={"symbol_id": symbol_id})
    projected = project(payload)
    if fmt == "json":
        emit_json(projected)
        return
    _render(projected)
    _ta.print_index_note(payload, fmt)


def _render(projected: dict) -> None:
    from repowise.cli.helpers import console

    candidates = projected.get("candidates") or []
    if candidates:
        console.print(
            f"[yellow]{len(candidates)} symbols match that id — showing all.[/yellow]"
        )
        for candidate in candidates:
            _render_body(console, candidate)
        return
    _render_body(console, projected)


def _render_body(console, body: dict) -> None:
    where = body.get("file", "")
    start, end = body.get("start_line"), body.get("end_line")
    if start is not None:
        where = f"{where}:{start}-{end}"
    verified = "verified" if body.get("verified") else body.get("bounds") or "unverified"
    console.print(f"[cyan]{body.get('qualified_name') or body.get('name') or ''}[/cyan] "
                  f"[dim]{body.get('kind', '')} · {where} · {verified}[/dim]")
    source = body.get("source") or ""
    if source:
        # Already in Read's line-numbered format — printing it through rich's
        # markup would eat any [bracketed] token in the source.
        console.print(source, markup=False, highlight=False)
    for line in body.get("fallback_lines") or []:
        console.print(line, markup=False, highlight=False)
    if body.get("note"):
        console.print(f"[dim]{_ta.as_cli_prose(str(body['note']))}[/dim]")
    if body.get("truncated"):
        continuation = body.get("continuation")
        if continuation:
            console.print(
                "[yellow]Body truncated.[/yellow] Continue with: "
                f"[cyan]repowise symbol {continuation}[/cyan]"
            )
        else:
            console.print("[yellow]Body truncated.[/yellow]")
