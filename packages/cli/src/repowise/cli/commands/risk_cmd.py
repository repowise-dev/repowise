"""``repowise risk`` — just-in-time change-risk for a commit or diff range.

Scores a *change* (not a file) from its diff shape — size, diffusion, author
familiarity — and prints a 0-10 risk with an attributable breakdown. Runs
in-process: pure git + learned constants, no LLM, no network. A natural
pre-merge / PR gate, complementary to ``repowise health`` (which scores files).

Examples:
    repowise risk                 # score HEAD
    repowise risk abc123          # score a single commit
    repowise risk main..HEAD      # score a branch / PR range as one change
"""

from __future__ import annotations

import json

import click
from rich.table import Table

from repowise.cli.helpers import console, err_console
from repowise.core.analysis.change_risk import (
    change_risk_payload,
    review_priority_classification,
    score_live_change,
)

_PRIORITY_COLOR = {"high": "yellow", "moderate": "dim", "low": "green"}
_PRIORITY_LEAD = {
    "low": "Lower risk than a typical commit in this repo",
    "moderate": "About as risky as a typical commit in this repo",
    "high": "Riskier than most commits in this repo",
}


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 93 -> '93rd', 11 -> '11th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@click.command("risk")
@click.argument("revspec", required=False, default="HEAD")
@click.option(
    "--path",
    "repo_path",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Path to the git repository (default: current directory).",
)
@click.option(
    "--ext",
    "ext",
    default=None,
    help="Comma-separated file suffixes to count (e.g. '.py' or '.ts,.tsx'). "
    "Default: count every changed file.",
)
@click.option(
    "--baseline",
    "baseline",
    default=200,
    type=click.IntRange(min=0),
    help="Sample this many recent commits to rank the change within the repo "
    "(0 disables; shows only the absolute calibrated band).",
)
@click.option(
    "--exclude",
    "-x",
    "exclude",
    multiple=True,
    metavar="PATTERN",
    help="Gitignore-style pattern to exclude. Repeatable; also applies to the baseline.",
)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    help="Output format.",
)
def risk_command(
    revspec: str,
    repo_path: str,
    ext: str | None,
    baseline: int,
    exclude: tuple[str, ...],
    fmt: str,
) -> None:
    """Score the defect risk of a change (commit or ``base..head`` range)."""
    extensions = tuple(e.strip() for e in ext.split(",")) if ext else ()
    status = err_console if fmt != "table" else console

    if baseline and fmt == "table":
        status.print(f"[dim]Sampling up to {baseline} recent commits…[/dim]")
    try:
        result = score_live_change(
            repo_path,
            revspec,
            extensions=extensions,
            exclude_patterns=exclude,
            baseline=baseline,
        )
    except Exception as exc:
        # Surface git errors (bad revspec, not a repo) as a clean CLI message.
        raise click.ClickException(
            f"Could not read change {revspec!r} in {repo_path}: {exc}"
        ) from exc

    features = result.features
    risk = result.risk
    percentile = result.percentile
    priority = result.priority
    request_excludes = result.request_excludes

    if features.nf == 0:
        status.print(
            f"[yellow]No counted file changes in {revspec!r} "
            f"(check the revspec, --ext, or exclusion filters).[/yellow]"
        )

    if fmt == "json":
        click.echo(json.dumps(change_risk_payload(result), indent=2))
        return

    if percentile is not None and priority is not None:
        pcolor = _PRIORITY_COLOR[priority]
        console.print(
            f"\n[bold]Change risk[/bold] for [cyan]{features.ref}[/cyan]: "
            f"[{pcolor}]{review_priority_classification(priority)}[/{pcolor}] · "
            f"{_ordinal(round(percentile))} percentile of recent commits"
        )
    else:
        # No usable baseline (shallow repo, --baseline 0): fall back to the
        # absolute calibrated band, labelled honestly as such.
        color = {"high": "red", "moderate": "yellow", "low": "green"}[risk.level]
        console.print(
            f"\n[bold]Change risk[/bold] for [cyan]{features.ref}[/cyan]: "
            f"[{color}]{risk.level}[/{color}] (absolute band — no repo baseline to rank against)"
        )
    if features.subject:
        console.print(f"  [dim]{features.subject}[/dim]")
    if request_excludes:
        console.print(f"  [dim]Excluding {len(request_excludes)} pattern(s).[/dim]")
    console.print(
        f"  +{features.la} / -{features.ld} lines · {features.nf} files · "
        f"{features.nd} dirs · {features.ns} subsystems · "
        f"entropy {features.entropy:.2f} · author exp {features.exp}"
        + ("  [magenta](fix)[/magenta]" if features.is_fix else "")
    )
    if percentile is not None and priority is not None:
        console.print(
            f"  [dim]{_PRIORITY_LEAD[priority]} ({_ordinal(round(percentile))} percentile).[/dim]"
        )
    # Raw score kept as a clearly-secondary, clearly-labelled number.
    console.print(
        f"  [dim]Raw model score: {risk.score:.1f}/10 — corpus-anchored ({risk.level}); "
        f"prefer the percentile for review order.[/dim]"
    )

    table = Table(title="Why this score (each driver vs. the model's baseline commit)")
    table.add_column("Driver")
    table.add_column("Value", justify="right")
    table.add_column("Push", justify="right")
    for d in risk.top_drivers:
        push_color = "red" if d.contribution > 0 else "green"
        sign = "+" if d.contribution >= 0 else ""
        table.add_row(
            d.label,
            f"{d.value:g}",
            f"[{push_color}]{sign}{d.contribution:.2f}[/{push_color}]",
        )
    console.print(table)
