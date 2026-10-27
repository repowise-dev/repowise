"""``repowise savings sync`` — record savings the ledger never saw.

The ledger only ever held what happened while it was running, so a repository
indexed today shows an empty savings page despite months of agent history on
disk. The core transcript surface reads that history back; this exposes it.

Deliberately not wired into ``repowise update``: update runs on every commit
and this reads a corpus bounded by how much the user has worked rather than by
the repository. ``init`` runs it once, and this command re-runs it on demand.
"""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option


@click.group("savings")
def savings_group() -> None:
    """Work with this repository's agent savings ledger."""


@savings_group.command("sync")
@click.argument("path", required=False, default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report what would be recorded without writing anything.",
)
@click.option(
    "--budget",
    type=float,
    default=None,
    metavar="SECONDS",
    help="Seconds of transcript reading. Raise it to finish a large backlog sooner.",
)
@format_option()
def sync_command(path: str | None, dry_run: bool, budget: float | None, fmt: str) -> None:
    """Record savings your agents were shown but the ledger never banked.

    Reads only what has been appended since the last run, so repeating it is
    cheap. A non-zero ``deferred`` means the time budget ran out and there is
    more to read; run it again.
    """
    from repowise.core.savings.transcript import SYNC_BUDGET_S, sync_transcript_savings

    repo_root = Path(path).resolve() if path else Path.cwd().resolve()
    result = sync_transcript_savings(
        repo_root,
        budget=SYNC_BUDGET_S if budget is None else budget,
        dry_run=dry_run,
    )

    if fmt == "json":
        emit_json(
            {
                "repository": str(repo_root),
                "dry_run": dry_run,
                "recorded": result.recorded,
                "saved_input_tokens": result.saved_input_tokens,
                "already_recorded": result.already_recorded,
                "transcripts_read": result.transcripts_read,
                "deferred": result.deferred,
                "per_agent": result.per_harness,
            }
        )
        return

    verb = "Would record" if dry_run else "Recorded"
    console.print(
        f"{verb} [bold]{result.recorded}[/bold] savings events "
        f"([bold]{result.saved_input_tokens:,}[/bold] input tokens) "
        f"from {result.transcripts_read} transcripts."
    )
    if result.already_recorded:
        console.print(
            f"  {result.already_recorded} already in the ledger and left alone.",
            style="dim",
        )
    for agent, tokens in sorted(result.per_harness.items(), key=lambda kv: -kv[1]):
        console.print(f"  {agent}: {tokens:,} tokens", style="dim")
    if result.deferred:
        console.print(
            f"  {result.deferred} transcripts not reached within the time budget. "
            f"Run this again to continue.",
            style="yellow",
        )
