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
    "health",
    "skeleton",
)

#: Card keys the projection replaces or drops. Everything else passes through.
#:
#: A denylist, not an allowlist. Most of what a card carries is opt-in: nine
#: ``--include`` blocks, each landing under its own key, and an allowlist that
#: misses one turns ``--include ownership`` into a flag that changes nothing
#: the caller can see. The failure mode of a denylist is a payload slightly
#: larger than intended; the failure mode of an allowlist is a silently
#: discarded answer, and that is the whole risk this trim carries.
_REPLACED_KEYS = frozenset(
    {
        # Placed by hand above the sweep.
        "target",
        "type",
        "docs",
        "hotspot",
        "fix_history",
        "episodes",
        # Folded into flatter keys.
        "architectural_layer",
        "freshness",
        # Placed by hand below, whole: opt-in, so it was asked for.
        "skeleton",
        # A breadcrumb to the wiki page the card came from, not a signal.
        "parent_page",
    }
)

#: The two ``docs`` keys the card lifts to the top level. Everything else under
#: ``docs`` stays: for a **symbol** target the whole card lives there
#: (``signature``, ``docstring``, ``used_by``, ``candidates``), and for a file
#: target it holds ``symbols`` — the list a caller pipes into ``repowise
#: symbol`` — plus ``content_md``/``documentation`` when ``--include full_doc``
#: asked for them. Denylisting ``docs`` wholesale projected a symbol target to
#: an empty card and made ``--include full_doc`` an inert flag.
_LIFTED_DOC_KEYS = ("title", "summary")


def _project_one(card: dict) -> dict:
    """One target's triage card, trimmed."""
    out: dict = {"target": card.get("target", "")}
    if card.get("type"):
        out["type"] = card["type"]
    if card.get("error"):
        # A target the tool could not resolve gets a card carrying only this.
        # Trimming it away leaves an empty card, which reads as "indexed, but
        # nothing to say" rather than "that path does not exist". A tombstone
        # (a page deleted or renamed after indexing) carries the redirect
        # beside the error, which is the whole point of the tombstone.
        out["error"] = card["error"]
        for key in ("successor_paths", "hint"):
            if card.get(key):
                out[key] = card[key]
        return out
    docs = card.get("docs") or {}
    for key in _LIFTED_DOC_KEYS:
        if docs.get(key):
            out[key] = docs[key]
    rest = {k: v for k, v in docs.items() if k not in _LIFTED_DOC_KEYS and v not in (None, [], {}, "")}
    if rest:
        out["docs"] = rest
    layer = card.get("architectural_layer") or {}
    if layer.get("name"):
        out["layer"] = layer["name"]
    if card.get("hotspot"):
        out["hotspot"] = True
    if card.get("fix_history"):
        out["fix_history"] = card["fix_history"]
    freshness = card.get("freshness") or {}
    # ``is_stale`` is None when the tool could not judge; reporting that as
    # False would say "these docs are current" on no evidence.
    if freshness.get("is_stale") is not None:
        out["stale"] = bool(freshness["is_stale"])
    if card.get("episodes"):
        out["episodes"] = card["episodes"]
    # Everything else the card carries, including every --include block. A
    # block the caller asked for by name is never trimmed: they paid a flag
    # for it.
    for key, value in card.items():
        if key not in _REPLACED_KEYS and key not in out and value not in (None, [], {}, ""):
            out[key] = value
    skeleton = card.get("skeleton") or {}
    if skeleton:
        # The skeleton is opt-in on the tool now (``--include skeleton``), so a
        # card that carries one carries it because this caller asked by name.
        # Trimming its ``text`` away here would make the flag inert — the
        # failure mode the ``--include`` passthrough above exists to avoid — so
        # the block passes through whole. It is still summarised rather than
        # printed on the table path.
        out["skeleton"] = skeleton
    return out


def project(payload: dict, targets: tuple[str, ...]) -> dict:
    """``get_context``'s dict, trimmed to the triage card itself.

    Field mapping, per target:

    ==================  ===========================================
    kept                target, type, docs.title -> title,
                        docs.summary -> summary,
                        architectural_layer.name -> layer,
                        freshness.is_stale -> stale, and **every other
                        key the card carries** — hotspot, fix_history,
                        episodes, the skeleton when ``--include
                        skeleton`` asked for one, and each other
                        ``--include`` block under its own name
    dropped             parent_page, freshness.confidence_score,
                        ``_meta`` minus its freshness keys
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
    for key in ("dropped_targets", "dropped_symbols", "omission_marker"):
        # omission_marker is the only handle on what truncation banked, so a
        # `truncated: true` without it says content went missing and offers no
        # way to get it back.
        if payload.get(key):
            out[key] = payload[key]
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
    pass --include skeleton for the whole file body-elided and line-verified,
    or just read the file.
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

    payload = _ta.run(repo_path, _factory, "get_context")

    if full:
        _ta.emit_full(payload)
        return
    _ta.emit_error(payload, fmt, extra={"targets": list(targets)})
    projected = project(payload, targets)
    if fmt == "json":
        emit_json(projected)
        return
    _render(projected)
    _ta.print_index_note(payload, fmt)


def _render(projected: dict) -> None:
    from rich.markup import escape
    from rich.table import Table

    from repowise.cli.helpers import console

    cards = projected.get("targets") or {}
    if not cards:
        console.print("[yellow]No context for those targets.[/yellow]")

    for card in cards.values():
        if card.get("error"):
            console.print(
                f"[yellow]{escape(str(card.get('target', '')))}: "
                f"{escape(_ta.as_cli_prose(str(card['error'])))}[/yellow]"
            )
            if card.get("hint"):
                console.print(f"  [dim]{escape(_ta.as_cli_prose(str(card['hint'])))}[/dim]")
            for successor in card.get("successor_paths") or []:
                console.print(f"  [cyan]{successor}[/cyan]")
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
                # Every cell here is tool text. Rich markup-parses a cell, so a
                # summary containing `list[str]` renders as `list` and a stray
                # `[/x]` raises MarkupError and takes the command down.
                table.add_row(label, escape(str(card[key])))
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
            # Summarised, never printed: the text is the reason the caller
            # passed --include skeleton, and a terminal table is not where a
            # few thousand lines of source belong. --format json carries it.
            table.add_row(
                "Skeleton",
                f"{skeleton.get('mode', '?')}, {skeleton.get('tokens', '?')} of "
                f"{skeleton.get('full_tokens', '?')} tokens "
                f"({skeleton.get('pct_of_full', '?')}% of full) — --format json for the text",
            )
        if card.get("decision_records"):
            table.add_row(
                "Decision records",
                escape(", ".join(str(v) for v in card["decision_records"])),
            )
        # Every remaining block, including each --include the caller asked
        # for by name. Rendering only the ones this function happens to know
        # about is how `--include ownership` ends up printing nothing at all.
        for key, value in card.items():
            if key in _RENDERED_KEYS:
                continue
            table.add_row(key.replace("_", " ").title(), escape(_flatten(value)))
        console.print(table)

    for label, key in (
        ("Not found", "not_found"),
        ("Dropped", "dropped_targets"),
    ):
        if projected.get(key):
            console.print(f"[yellow]{label}: {escape(', '.join(projected[key]))}[/yellow]")
    if projected.get("truncated"):
        console.print("[yellow]Response was truncated to fit the tool's budget.[/yellow]")
        marker = projected.get("omission_marker")
        if marker:
            # Printed as-is: the marker already reads
            # "[repowise#<ref>: N lines omitted; restore: repowise expand <ref>]",
            # so it carries its own instruction — and it opens with a bracket,
            # which rich would parse as a style tag and delete outright.
            console.print(escape(str(marker)), style="cyan")


#: Card keys ``_render`` has already placed by hand, by the time it sweeps the
#: rest. Everything not listed here is rendered generically rather than dropped.
_RENDERED_KEYS = frozenset(
    {
        "target",
        "type",
        "title",
        "summary",
        "layer",
        "hotspot",
        "fix_history",
        "stale",
        "episodes",
        "skeleton",
        "decision_records",
    }
)


def _flatten(value: object) -> str:
    """One cell for an arbitrary block a caller opted into with ``--include``."""
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_flatten(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(_flatten(v) for v in value)
    return str(value)
