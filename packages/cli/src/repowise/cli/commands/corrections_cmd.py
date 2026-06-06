"""``repowise corrections`` — fail→fixed command pairs the agent keeps hitting.

Mines local agent transcripts (the same reader as ``repowise saved --missed``)
for shell commands that failed and were re-run successfully in a corrected
form, classifies each fumble (wrong tool, wrong path, unknown flag, missing
argument), and reports the recurring rules. Report-only by default;
``--write`` additionally maintains a short "Known command corrections"
managed block in the repo's CLAUDE.md/AGENTS.md so the next session doesn't
repeat the fumble.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import console

_KIND_LABELS = {
    "wrong_tool": "wrong tool",
    "wrong_path": "wrong path",
    "unknown_flag": "unknown flag",
    "missing_arg": "missing arg",
}


@click.command("corrections")
@click.argument("path", required=False, default=None)
@click.option(
    "--days",
    type=click.FloatRange(min=0.1),
    default=30.0,
    show_default=True,
    metavar="DAYS",
    help="Transcript window for the scan.",
)
@click.option(
    "--write",
    "write_block",
    is_flag=True,
    help=(
        "Maintain the 'Known command corrections' managed block in the "
        "repo's CLAUDE.md/AGENTS.md (strictly opt-in; report-only otherwise)."
    ),
)
@click.option(
    "--min-count",
    type=click.IntRange(min=1),
    default=2,
    show_default=True,
    help="Occurrences a rule needs before --write includes it.",
)
def corrections_command(path: str | None, days: float, write_block: bool, min_count: int) -> None:
    """Show recurring command fumbles mined from local agent transcripts.

    PATH defaults to the current directory's enclosing repowise repo. The
    scan is read-only and stays entirely local — commands are read from your
    own Claude Code transcript directory; nothing leaves this machine.
    """
    from repowise.cli.helpers import find_repowise_repo_root
    from repowise.core.distill.corrections import scan_corrections

    start = Path(path).resolve() if path else Path.cwd()
    repo_root = find_repowise_repo_root(start) or start
    report = scan_corrections(repo_root, days=days)

    if not report["rules"]:
        console.print(
            f"[yellow]No command corrections found in the last {days:g} days.[/yellow] "
            "Either the agent isn't fumbling commands here, or no transcripts "
            "cover this repo."
        )
        return

    table = Table(
        title=f"Command corrections - last {days:g} days",
        border_style="dim",
        caption=(
            "Fail->fixed pairs mined from local Claude Code transcripts; "
            "nothing leaves this machine."
        ),
    )
    table.add_column("Count", justify="right")
    table.add_column("Kind", style="cyan")
    table.add_column("Wrong", overflow="fold", max_width=40)
    table.add_column("Fixed", overflow="fold", max_width=40)
    for rule in report["rules"]:
        fixed = rule["fixed"]
        if rule.get("hint"):
            fixed += f"\n[dim]{rule['hint']}[/dim]"
        table.add_row(
            f"{rule['count']}x",
            _KIND_LABELS.get(rule["kind"], rule["kind"]),
            rule["wrong"],
            fixed,
        )

    console.print()
    console.print(table)
    example = report["rules"][0]["example"]
    console.print(f"  [dim]e.g. {example['failed'][:100]}[/dim]")
    console.print(f"  [dim]  -> {example['fixed'][:100]}[/dim]")

    if write_block:
        _write_managed_blocks(repo_root, report["rules"], min_count)
    else:
        qualifying = sum(1 for r in report["rules"] if r["count"] >= min_count)
        if qualifying:
            console.print(
                f"  [dim]repowise corrections --write adds the top {qualifying} "
                "rule(s) to CLAUDE.md/AGENTS.md as agent guidance.[/dim]"
            )
    console.print()


def _write_managed_blocks(repo_root: Path, rules: list[dict], min_count: int) -> None:
    """Upsert the managed block into CLAUDE.md/AGENTS.md (or prune it)."""
    from repowise.core.distill.corrections import (
        render_corrections_block,
        update_corrections_block,
    )

    block = render_corrections_block(rules, min_count=min_count)
    claude_md = repo_root / ".claude" / "CLAUDE.md"
    agents_md = repo_root / "AGENTS.md"

    # CLAUDE.md is created when absent (the primary agent surface); AGENTS.md
    # is only updated when the user already maintains one.
    targets = [claude_md] + ([agents_md] if agents_md.exists() else [])
    if block is None:
        console.print(
            f"  [yellow]No rule seen {min_count}+ times - managed block "
            "removed where present.[/yellow]"
        )
    for target in targets:
        try:
            changed = update_corrections_block(target, block)
        except OSError:
            console.print(f"  [yellow]Could not update {target}[/yellow]")
            continue
        if changed:
            verb = "updated" if block is not None else "removed"
            console.print(f"  [green]✓[/green] Known-corrections block {verb} ({target})")
