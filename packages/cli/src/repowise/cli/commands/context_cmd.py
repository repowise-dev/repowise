"""``repowise context`` — the CLI adapter over the ``get_context`` MCP tool."""

from __future__ import annotations

import click

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.output import emit_json

#: ``include`` blocks the tool accepts, as ``--include`` choices.
_INCLUDE_BLOCKS = (
    "full_doc",
    "ownership",
    "last_change",
    "callers",
    "callees",
    "metrics",
    "community",
    "decisions",
    "skeleton",
)


def _project_one(card: dict) -> dict:
    """One target's triage card, trimmed."""
    out: dict = {"target": card.get("target", "")}
    if card.get("type"):
        out["type"] = card["type"]
    if card.get("error"):
        # A target the tool could not resolve gets a card carrying only this.
        # Trimming it away leaves an empty card, which reads as "indexed, but
        # nothing to say" rather than "that path does not exist".
        out["error"] = card["error"]
        return out
    docs = card.get("docs") or {}
    if docs.get("title"):
        out["title"] = docs["title"]
    if docs.get("summary"):
        out["summary"] = docs["summary"]
    layer = card.get("architectural_layer") or {}
    if layer.get("name"):
        out["layer"] = layer["name"]
    if card.get("hotspot"):
        out["hotspot"] = True
    if card.get("fix_history"):
        out["fix_history"] = card["fix_history"]
    freshness = card.get("freshness") or {}
    if freshness:
        out["stale"] = bool(freshness.get("is_stale"))
    if card.get("episodes"):
        out["episodes"] = card["episodes"]
    for key in ("symbol_ids", "signatures", "decision_records", "callers", "callees", "metrics"):
        if card.get(key):
            out[key] = card[key]
    skeleton = card.get("skeleton") or {}
    if skeleton:
        # The skeleton *text* is the bulk of get_context's payload — 10K chars
        # for one file — and it is source a caller can also get from `symbol`
        # or a plain Read. The card keeps its shape and size so a caller can
        # decide whether to pay for it; --full carries the text.
        out["skeleton"] = {
            "mode": skeleton.get("mode", ""),
            "tokens": skeleton.get("tokens"),
            "full_tokens": skeleton.get("full_tokens"),
            "pct_of_full": skeleton.get("pct_of_full"),
            "verified": skeleton.get("verified"),
            "bodies_kept": skeleton.get("bodies_kept") or [],
        }
    return out


def project(payload: dict, targets: tuple[str, ...]) -> dict:
    """``get_context``'s dict, trimmed to the triage card itself.

    Field mapping, per target:

    ==================  ===========================================
    kept                target, type, docs.title -> title,
                        docs.summary -> summary,
                        architectural_layer.name -> layer, hotspot,
                        fix_history, freshness.is_stale -> stale,
                        episodes, and any of symbol_ids / signatures
                        / decision_records / callers / callees /
                        metrics the ``include`` blocks added; the
                        skeleton's shape without its ``text``
    dropped             skeleton.text (~10K chars per file, the bulk
                        of the payload), parent_page,
                        skeleton.opt_out_hint / auto,
                        freshness.confidence_score, ``_meta`` minus
                        its freshness keys
    ==================  ===========================================

    A target the tool could not resolve gets a card carrying only ``error``,
    and that key survives the trim. Top-level ``truncated`` /
    ``dropped_targets`` / ``dropped_symbols`` are kept whenever they say
    something — a silently dropped target is exactly the thing a caller must
    not have to diff two payloads to notice.
    """
    cards = payload.get("targets") or {}
    out: dict = {
        "targets": {key: _project_one(card) for key, card in cards.items()},
    }
    if payload.get("truncated"):
        out["truncated"] = True
    if payload.get("dropped_targets"):
        out["dropped_targets"] = payload["dropped_targets"]
    if payload.get("dropped_symbols"):
        out["dropped_symbols"] = payload["dropped_symbols"]
    # Requested but absent from the response at all: neither a card nor a
    # recorded drop. Without this a typo'd path is indistinguishable from a
    # path the index simply has nothing to say about.
    missing = [t for t in targets if t not in cards and t not in (payload.get("dropped_targets") or [])]
    if missing:
        out["not_found"] = missing
    note = _ta.index_note(payload)
    if note:
        out["index"] = note
    return out


@click.command("context")
@click.argument("targets", nargs=-1, required=True)
@click.option(
    "--include",
    "include",
    multiple=True,
    type=click.Choice(_INCLUDE_BLOCKS),
    help="Opt-in blocks to add. Repeatable.",
)
@click.option(
    "--no-compact",
    is_flag=True,
    default=False,
    help="Add structure, imports and docstrings to each card.",
)
@_ta.target_options
def context_command(
    targets: tuple[str, ...],
    include: tuple[str, ...],
    no_compact: bool,
    path: str | None,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
    full: bool,
) -> None:
    """Triage card for files, modules or symbols.

    TARGETS are file paths, module paths, or "path/to/file.py::Symbol" ids.
    Batch them in one call. Relationships and risk signals, not source bytes:
    pass --full for the verified skeleton, or use 'repowise symbol' for one
    body.
    """
    fmt = _ta.resolve_format_for(fmt, full)
    repo_path = _ta.resolve_indexed_repo(
        path=path,
        repo_alias=repo_alias,
        no_workspace=no_workspace,
        fmt=fmt,
        command="context",
    )

    def _factory():
        from repowise.server.mcp_server.tool_context import get_context

        return get_context(
            targets=list(targets),
            include=list(include) or None,
            compact=not no_compact,
        )

    payload = _ta.run(repo_path, _factory)

    if full:
        emit_json(payload)
        return
    _ta.emit_error(payload, fmt, extra={"targets": list(targets)})
    projected = project(payload, targets)
    if fmt == "json":
        emit_json(projected)
        return
    _render(projected)
    _ta.print_index_note(payload, fmt)


def _render(projected: dict) -> None:
    from rich.table import Table

    from repowise.cli.helpers import console

    cards = projected.get("targets") or {}
    if not cards:
        console.print("[yellow]No context for those targets.[/yellow]")

    for card in cards.values():
        if card.get("error"):
            console.print(
                f"[yellow]{card.get('target', '')}: "
                f"{_ta.as_cli_prose(str(card['error']))}[/yellow]"
            )
            continue
        table = Table(title=card.get("target", ""), show_header=False)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        for label, key in (
            ("Type", "type"),
            ("Title", "title"),
            ("Summary", "summary"),
            ("Layer", "layer"),
        ):
            if card.get(key):
                table.add_row(label, str(card[key]))
        signals = []
        if card.get("hotspot"):
            signals.append("hotspot")
        fix = card.get("fix_history") or {}
        if fix.get("fix_count"):
            magnet = " (bug magnet)" if fix.get("bug_magnet") else ""
            signals.append(
                f"{fix['fix_count']} bug fixes, last {fix.get('last_fix_days_ago', '?')}d ago{magnet}"
            )
        if card.get("stale"):
            signals.append("docs stale")
        if card.get("episodes"):
            signals.append(f"{card['episodes']} episodes")
        if signals:
            table.add_row("Signals", ", ".join(signals))
        skeleton = card.get("skeleton") or {}
        if skeleton:
            table.add_row(
                "Skeleton",
                f"{skeleton.get('mode', '?')}, {skeleton.get('tokens', '?')} of "
                f"{skeleton.get('full_tokens', '?')} tokens "
                f"({skeleton.get('pct_of_full', '?')}% of full) — pass --full for the text",
            )
        for label, key in (("Symbols", "symbol_ids"), ("Decisions", "decision_records")):
            if card.get(key):
                table.add_row(label, ", ".join(str(v) for v in card[key]))
        console.print(table)

    for label, key in (
        ("Not found", "not_found"),
        ("Dropped", "dropped_targets"),
    ):
        if projected.get(key):
            console.print(f"[yellow]{label}: {', '.join(projected[key])}[/yellow]")
    if projected.get("truncated"):
        console.print("[yellow]Response was truncated to fit the tool's budget.[/yellow]")
