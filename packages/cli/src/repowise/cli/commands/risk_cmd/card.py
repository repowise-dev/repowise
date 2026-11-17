"""One ``--target`` risk card, a line per block the projection keeps."""

from __future__ import annotations

from rich.markup import escape

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.helpers import console

#: Co-change partners printed before the "and N more" line.
_PARTNERS_SHOWN = 8


def _print_list(label: str, values: list[str], *, indent: str = "  ") -> None:
    if not values:
        return
    console.print(f"{indent}[bold]{label}[/bold] " + ", ".join(escape(str(v)) for v in values))


def _render_card(name: str, card: dict) -> None:
    """One target's risk card.

    Every key the projection keeps owes a line here: a kept key no renderer
    prints is a silent failure a projection test cannot see.
    """
    console.print(f"\n[bold cyan]{escape(name)}[/bold cyan]")
    summary = card.get("risk_summary")
    if summary:
        console.print(f"  {escape(str(summary))}")
    if card.get("resolved") is False:
        # The lines below default missing fields to numbers, so stop here rather
        # than print "0 direct dependents".
        reason = escape(str(card.get("unresolved_reason") or "unknown"))
        console.print(f"  [dim]unresolved: {reason}[/dim]")
        return
    _print_activity(card)
    _print_health(card)
    _print_biomarkers(card.get("top_biomarkers") or [])
    _print_magnitude(card)
    _print_ownership_moves(card)
    _print_cross_repo(card.get("cross_repo_impact") or {})
    _print_defects(card.get("defect_profile"))
    if card.get("test_gap"):
        console.print("  [yellow]No test is shown to reach this file.[/yellow]")
    for signal in card.get("security_signals") or []:
        console.print(
            f"  [red]{escape(str(signal.get('severity', '')))}[/red] "
            f"{escape(str(signal.get('kind', '')))}: {escape(str(signal.get('snippet', '')))}"
        )
    _print_partners(card.get("co_change_partners") or [])
    episodes = card.get("episodes")
    if episodes:
        console.print(f"  [dim]{episodes} recorded episode(s) — see 'repowise why'.[/dim]")


def _print_activity(card: dict) -> None:
    """Hotspot, dependents and ownership; history alone when nothing else was measured."""
    trend = card.get("trend") or "unknown"
    owner = escape(str(card.get("primary_owner") or "unknown"))
    if card.get("signal_tier") == "history":
        last = str(card.get("last_commit_at") or "unknown")[:10]
        console.print(
            f"  [dim]history only · {card.get('commit_count_total', 0)} commits ({trend}) · "
            f"last {escape(last)} · owned {_ta.owner_share(card.get('owner_pct'))} by "
            f"{owner}[/dim]"
        )
        return
    console.print(
        f"  [dim]hotspot {float(card.get('hotspot_score') or 0.0):.0%} ({trend}) · "
        f"{card.get('dependents_count', 0)} direct dependents · "
        f"{card.get('risk_type', 'unknown')} · owned "
        f"{_ta.owner_share(card.get('owner_pct'))} by "
        f"{owner}[/dim]"
    )


def _print_health(card: dict) -> None:
    health = card.get("health_score")
    coverage = card.get("line_coverage_pct")
    if health is None and coverage is None:
        return
    parts = []
    if health is not None:
        parts.append(f"health {float(health):.1f}/10")
    if coverage is not None:
        parts.append(f"coverage {float(coverage):.0f}%")
    console.print(f"  [dim]{escape(' · '.join(parts))}[/dim]")


def _biomarker_text(b: object) -> str:
    # Keyed ``biomarker_type``: a generic key guess prints the whole dict repr.
    if not isinstance(b, dict):
        return str(b)
    where = f" in {b['function_name']}" if b.get("function_name") else ""
    return f"{b.get('biomarker_type', '?')} ({b.get('severity', '?')}){where}"


def _print_biomarkers(biomarkers: list) -> None:
    if biomarkers:
        shown = [_biomarker_text(b) for b in biomarkers[:3]]
        console.print(f"  [dim]{escape(', '.join(shown))}[/dim]")


def _print_magnitude(card: dict) -> None:
    magnitude = card.get("change_magnitude") or {}
    if magnitude:
        console.print(
            f"  [dim]+{magnitude.get('lines_added_90d', 0)} / "
            f"-{magnitude.get('lines_deleted_90d', 0)} lines in 90d · "
            f"avg commit {magnitude.get('avg_commit_size', 0)} lines · "
            f"{card.get('contributor_count', 0)} contributor(s), "
            f"bus factor {card.get('bus_factor', '?')}[/dim]"
        )


def _print_ownership_moves(card: dict) -> None:
    """A rename, and a recent owner who is not the historical one."""
    if card.get("original_path"):
        console.print(f"  [dim]Renamed from {escape(str(card['original_path']))}.[/dim]")
    if card.get("recent_owner") and card["recent_owner"] != card.get("primary_owner"):
        console.print(
            f"  [dim]Recently owned {_ta.owner_share(card.get('recent_owner_pct'))} by "
            f"{escape(str(card['recent_owner']))}.[/dim]"
        )


def _print_cross_repo(cross: dict) -> None:
    if not cross:
        return
    repos = cross.get("affected_repos") or []
    historical = cross.get("cross_repo_consumers") or []
    contracts = cross.get("contract_consumers") or []
    parts = []
    if historical:
        parts.append(
            f"{cross.get('cross_repo_consumers_total', len(historical))} historical co-change(s)"
        )
    if contracts:
        parts.append(
            f"{cross.get('contract_consumers_total', len(contracts))} typed contract consumer(s)"
        )
    where = f" in {', '.join(str(r) for r in repos)}" if repos else ""
    console.print(f"  [bold]Crosses repo boundaries[/bold] [dim]{', '.join(parts)}{where}[/dim]")


def _print_defects(defect: dict | None) -> None:
    if not defect:
        return
    magnet = " [magenta](bug magnet)[/magenta]" if defect.get("bug_magnet") else ""
    window = f" in {defect['window']}" if defect.get("window") else ""
    console.print(
        f"  [yellow]{defect.get('fix_count', 0)} bug fix(es)[/yellow]{window}, last "
        f"{defect.get('last_fix_days_ago', '?')} day(s) ago{magnet}"
    )
    # ``top_symbols`` is a ``{name: fix_count}`` dict; iterating it bare drops the counts.
    top = defect.get("top_symbols") or {}
    pairs = top.items() if isinstance(top, dict) else [(s, None) for s in top]
    _print_list(
        "Mostly in",
        [f"{name}" + (f" (x{count})" if count is not None else "") for name, count in pairs],
        indent="    ",
    )


def _print_partners(partners: list) -> None:
    if not partners:
        return
    console.print("  [bold]Co-changes with[/bold]")
    for p in partners[:_PARTNERS_SHOWN]:
        link = " [dim](also imports)[/dim]" if p.get("has_import_link") else ""
        support = p.get("support")
        support_text = f" [dim]x{support}[/dim]" if support is not None else ""
        console.print(f"    {escape(str(p.get('file_path', '')))}{support_text}{link}")
    if len(partners) > _PARTNERS_SHOWN:
        console.print(
            f"    [dim]… and {len(partners) - _PARTNERS_SHOWN} more (--format json).[/dim]"
        )
