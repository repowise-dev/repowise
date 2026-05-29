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
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    help="Output format.",
)
def risk_command(revspec: str, repo_path: str, ext: str | None, fmt: str) -> None:
    """Score the defect risk of a change (commit or ``base..head`` range)."""
    from repowise.core.analysis.change_risk import (
        extract_commit_features,
        extract_range_features,
        score_change,
    )

    extensions = tuple(e if e.startswith(".") else f".{e}" for e in ext.split(",")) if ext else ()
    status = err_console if fmt != "table" else console

    try:
        if ".." in revspec:
            base, _, head = revspec.partition("..")
            head = head or "HEAD"
            features = extract_range_features(repo_path, base, head, extensions=extensions)
        else:
            features = extract_commit_features(repo_path, revspec, extensions=extensions)
    except Exception as exc:
        # Surface git errors (bad revspec, not a repo) as a clean CLI message.
        raise click.ClickException(
            f"Could not read change {revspec!r} in {repo_path}: {exc}"
        ) from exc

    if features.nf == 0:
        status.print(
            f"[yellow]No counted file changes in {revspec!r} "
            f"(check the revspec or --ext filter).[/yellow]"
        )

    risk = score_change(features)

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "ref": features.ref,
                    "score": risk.score,
                    "probability": round(risk.probability, 4),
                    "level": risk.level,
                    "is_fix": features.is_fix,
                    "features": {
                        "la": features.la,
                        "ld": features.ld,
                        "nf": features.nf,
                        "nd": features.nd,
                        "ns": features.ns,
                        "entropy": round(features.entropy, 4),
                        "exp": features.exp,
                    },
                    "drivers": [
                        {
                            "feature": d.feature,
                            "value": d.value,
                            "contribution": round(d.contribution, 4),
                            "label": d.label,
                        }
                        for d in risk.top_drivers
                    ],
                },
                indent=2,
            )
        )
        return

    color = {"high": "red", "moderate": "yellow", "low": "green"}[risk.level]
    console.print(
        f"\n[bold]Change risk[/bold] for [cyan]{features.ref}[/cyan]: "
        f"[{color}]{risk.score:.1f}/10[/{color}] ({risk.level})"
    )
    if features.subject:
        console.print(f"  [dim]{features.subject}[/dim]")
    console.print(
        f"  +{features.la} / -{features.ld} lines · {features.nf} files · "
        f"{features.nd} dirs · {features.ns} subsystems · "
        f"entropy {features.entropy:.2f} · author exp {features.exp}"
        + ("  [magenta](fix)[/magenta]" if features.is_fix else "")
    )

    table = Table(title="Risk drivers (signed contribution to the change-risk)")
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
