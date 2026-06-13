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
import subprocess
from dataclasses import replace

import click
from rich.table import Table

from repowise.cli.helpers import console, err_console

# Repo-relative tercile wording — mirrors the web UI's PriorityBadge so the same
# commit reads the same in both surfaces.
_PRIORITY_LABEL = {"high": "Elevated", "moderate": "Typical", "low": "Below typical"}
_PRIORITY_COLOR = {"high": "yellow", "moderate": "dim", "low": "green"}
_PRIORITY_LEAD = {
    "low": "Lower risk than a typical commit in this repo",
    "moderate": "About as risky as a typical commit in this repo",
    "high": "Riskier than most commits in this repo",
}

# Below this many sampled commits a percentile isn't worth showing — fall back
# to the absolute calibrated band instead.
_MIN_BASELINE = 8


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 93 -> '93rd', 11 -> '11th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _baseline_scores(
    repo_path: str,
    anchor: str,
    limit: int,
    extensions: tuple[str, ...],
    exclude: str,
) -> list[float]:
    """Score the repo's recent commits to build a local risk distribution.

    One ``git log --numstat`` call (no per-commit author lookup), so it stays
    cheap enough for a pre-merge gate. Experience is left unknown for the
    baseline; the target is ranked with experience likewise unknown, so the
    comparison is like-with-like — a diff-shape percentile within this repo.
    """
    from repowise.core.analysis.change_risk import (
        features_from_file_changes,
        score_change,
    )

    out = subprocess.run(
        ["git", "log", f"-n{limit}", "--no-merges", "--format=%x1e%H", "--numstat", anchor],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout

    scores: list[float] = []
    for block in out.split("\x1e"):
        lines = block.strip().split("\n")
        if not lines or not lines[0]:
            continue
        sha, rows = lines[0].strip(), lines[1:]
        # Drop the target itself from its own baseline (short or full sha).
        if exclude and (sha.startswith(exclude) or exclude.startswith(sha)):
            continue
        changes: list[tuple[str, int, int]] = []
        for row in rows:
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            a_raw, d_raw, path = parts
            if extensions and not path.endswith(extensions):
                continue
            a = int(a_raw) if a_raw.isdigit() else 0
            d = int(d_raw) if d_raw.isdigit() else 0
            changes.append((path, a, d))
        if not changes:
            continue
        feats = features_from_file_changes(changes, exp=None)
        scores.append(score_change(feats).score)
    return scores


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
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    help="Output format.",
)
def risk_command(
    revspec: str, repo_path: str, ext: str | None, baseline: int, fmt: str
) -> None:
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

    # Repo-relative ranking: where this change sits in the repo's own recent
    # distribution. The raw score is corpus-anchored and skews high on repos
    # whose typical commit is large; the percentile is the portable signal.
    percentile: float | None = None
    priority: str | None = None
    if baseline:
        if ".." in revspec:
            _, _, anchor = revspec.partition("..")
            anchor, exclude = anchor or "HEAD", ""
        else:
            anchor, exclude = revspec, features.ref
        if fmt == "table":
            status.print(f"[dim]Sampling up to {baseline} recent commits…[/dim]")
        scores = _baseline_scores(repo_path, anchor, baseline, extensions, exclude)
        if len(scores) >= _MIN_BASELINE:
            from repowise.core.analysis.change_risk import RiskNormalizer

            normalizer = RiskNormalizer.from_scores(scores)
            # Rank with experience unknown, matching the baseline (diff-shape
            # percentile within the repo) — keeps the comparison like-with-like.
            rank_score = score_change(replace(features, exp=None)).score
            percentile = normalizer.percentile(rank_score)
            priority = normalizer.priority(rank_score)

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "ref": features.ref,
                    "score": risk.score,
                    "probability": round(risk.probability, 4),
                    "level": risk.level,
                    "risk_percentile": round(percentile, 1) if percentile is not None else None,
                    "review_priority": priority,
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

    if percentile is not None and priority is not None:
        pcolor = _PRIORITY_COLOR[priority]
        console.print(
            f"\n[bold]Change risk[/bold] for [cyan]{features.ref}[/cyan]: "
            f"[{pcolor}]{_PRIORITY_LABEL[priority]}[/{pcolor}] · "
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
