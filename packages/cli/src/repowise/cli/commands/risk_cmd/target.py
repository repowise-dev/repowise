"""``repowise risk --target``: the ``get_risk`` adapter and its projection."""

from __future__ import annotations

from rich.markup import escape
from rich.table import Table

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.helpers import console
from repowise.cli.output import emit_json

from .card import _print_list, _render_card
from .records import _DIRECTIVE_RECORD_BLOCKS, _print_records

#: Per-target keys the trim drops, and nothing else. A denylist on purpose: its
#: failure mode is a slightly larger payload, an allowlist's is a lost answer.
#: ``impact_surface`` is opt-in on the tool; the entry stays for hand-built payloads.
_DROPPED_TARGET_KEYS = ("_base_dep_count", "impact_surface")


def _project_target(card: dict) -> dict:
    """One ``get_risk`` target card, minus the blocks in ``_DROPPED_TARGET_KEYS``."""
    return {k: v for k, v in card.items() if k not in _DROPPED_TARGET_KEYS}


def project_risk(payload: dict) -> dict:
    """``get_risk``'s dict, trimmed to what a CLI caller reads.

    Kept whole: ``directive`` (the answer in PR mode), ``pr_blast_radius``
    (already capped by the tool), ``global_hotspots``, and the freshness half
    of ``_meta``. Dropped: the timing half of ``_meta`` and the per-target
    keys in ``_DROPPED_TARGET_KEYS``.
    """
    out: dict = {}
    if payload.get("directive"):
        out["directive"] = payload["directive"]
    out["targets"] = {
        name: _project_target(card) for name, card in (payload.get("targets") or {}).items()
    }
    for key in ("risk_scales", "pr_blast_radius", "global_hotspots", "omission_marker"):
        if payload.get(key):
            out[key] = payload[key]
    note = _ta.index_note(payload)
    if note:
        out["index"] = note
    return out


def _target_risk(
    repo_path: str,
    targets: tuple[str, ...],
    changed_files: tuple[str, ...],
    fmt: str,
    full: bool,
) -> None:
    """``--target``: what history says about touching these files."""
    fmt = _ta.resolve_format_for(fmt, full)
    repo = _ta.resolve_indexed_repo(
        path=repo_path,
        repo_alias=None,
        no_workspace=False,
        fmt=fmt,
        command="risk",
    )

    def _factory():
        from repowise.server.mcp_server.tool_risk.get_risk import get_risk

        return get_risk(
            targets=list(targets),
            changed_files=list(changed_files) or None,
            # Every block the card prints; --format json also keeps the PR blast radius.
            include=["churn", "owners", "blast"],
        )

    payload = _ta.run(repo, _factory, "get_risk")

    if full:
        _ta.emit_full(payload)
        return
    _ta.emit_error(payload, fmt, extra={"targets": list(targets)})
    projected = project_risk(payload)
    if fmt == "json":
        emit_json(projected)
        return
    _render_target_risk(projected, targets)
    _ta.print_index_note(payload, fmt)


def _render_target_risk(projected: dict, requested: tuple[str, ...]) -> None:
    """The human path for ``--target``: the directive first, then a card each."""
    directive = projected.get("directive")
    if directive:
        _print_directive(directive)
    _print_cards(projected.get("targets") or {}, requested)
    hotspots = projected.get("global_hotspots") or []
    if hotspots:
        _print_global_hotspots(hotspots)
    if projected.get("omission_marker"):
        # Escaped: it opens with a bracket rich would parse as a style tag.
        console.print(escape(str(projected["omission_marker"])), style="cyan")


def _print_directive(directive: dict) -> None:
    """PR mode leads with the directive: what may break, what is missing, what to run."""
    console.print(f"\n[bold]Directive[/bold] {escape(str(directive.get('summary', '')))}")
    for label, key in (
        ("May break", "may_break"),
        ("Missing co-changes", "missing_cochanges"),
        ("Files without tests", "missing_tests"),
        ("Tests to run", "tests_to_run"),
    ):
        _print_list(label, directive.get(key) or [])
    for label, key in _DIRECTIVE_RECORD_BLOCKS:
        _print_records(
            label,
            directive.get(key) or [],
            key,
            truncated=directive.get(f"{key}_truncated") or 0,
        )
    calls = directive.get("next_calls") or []
    _print_list("Then run", [c["cli"] for c in calls if c.get("cli")])


def _print_cards(targets: dict, requested: tuple[str, ...]) -> None:
    """A card per requested target, then any the tool returned under another spelling."""
    for name in requested:
        card = targets.get(name)
        if card is None:
            # Excluded paths are filtered before the tool sees them, so no card
            # is an answer, not an empty section.
            console.print(f"\n[yellow]{escape(name)} — not indexed (or excluded).[/yellow]")
            continue
        _render_card(name, card)
    for name, card in targets.items():
        if name not in requested:
            _render_card(name, card)


def _print_global_hotspots(hotspots: list) -> None:
    """The repo's other hotspots, as a table."""
    table = Table(title="Elsewhere in this repo")
    table.add_column("File", style="cyan")
    table.add_column("Hotspot", justify="right")
    table.add_column("Owner")
    # The header carries the window the rows' counts share.
    window = next((h["window"] for h in hotspots if h.get("window")), None)
    table.add_column(f"Fixes ({window})" if window else "Fixes", justify="right")
    for h in hotspots:
        score = h.get("hotspot_score")
        fixes = h.get("fix_count")
        table.add_row(
            escape(str(h.get("file_path", ""))),
            "?" if score is None else f"{float(score):.0%}",
            escape(str(h.get("primary_owner") or "unknown")),
            "" if fixes is None else str(fixes),
        )
    console.print(table)
