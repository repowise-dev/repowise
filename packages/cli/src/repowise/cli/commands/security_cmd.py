"""``repowise security scan`` — security signal scanning.

Subcommands:
  scan --history [--since <rev>] [--to <rev>] [--output json]
      Walk the entire git history of the repo (not just the working tree) with
      the same pattern registry the indexer uses, and persist any secrets or
      risky patterns into the shared ``security_findings`` table — tagged with
      the commit that introduced them. Re-runs are idempotent.
"""

from __future__ import annotations

import json

import click

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output,
)


@click.group("security")
def security_command() -> None:
    """Security signal scanning (working tree + full git history)."""


@security_command.command("scan")
@click.option(
    "--history",
    is_flag=True,
    default=False,
    help="Scan the full git history, not just the current working tree.",
)
@click.option(
    "--since",
    default=None,
    help="Lower git revision bound (exclusive). Defaults to all history.",
)
@click.option(
    "--to",
    default=None,
    help="Upper git revision bound (inclusive). Defaults to all history/HEAD.",
)
@click.option(
    "--path",
    "repo",
    default=None,
    help="Repo path (defaults to cwd / workspace primary).",
)
@click.option(
    "--all-patterns",
    is_flag=True,
    default=False,
    help="History mode: also report code-smell patterns (eval, os.system, "
    "weak hashes, ...). By default history mode reports only leaked-secret "
    "patterns (hardcoded_password / hardcoded_secret) to avoid noise.",
)
@click.option(
    "--output",
    "output_format",
    default="table",
    type=click.Choice(["table", "json"]),
    help="Output format. ``json`` is the machine-readable summary.",
)
def security_scan(
    history: bool,
    since: str | None,
    to: str | None,
    all_patterns: bool,
    repo: str | None,
    output_format: str,
) -> None:
    """Scan for security signals and persist findings to the local store.

    Without ``--history`` this is a no-op stub (working-tree scanning already
    happens during ``repowise init`` / ``repowise update``). With ``--history``
    it walks every tracked revision and surfaces leaked secrets / risky
    patterns that were later removed — something the working-tree scan cannot
    see.
    """
    if not history:
        console.print(
            "[yellow]Working-tree scanning runs automatically during "
            "`repowise init`/`repowise update`.[/yellow]\n"
            "Pass [cyan]--history[/cyan] to scan the full git history for secrets "
            "and risky patterns (including ones deleted in later commits)."
        )
        return

    if output_format == "json":
        silence_logs_for_machine_output()

    from pathlib import Path

    from repowise.core.analysis.history_scan import HistorySecurityScanner
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        upsert_repository,
    )

    target = resolve_command_target(path=repo)
    target.notice(console, command="security scan --history")

    if target.is_workspace:
        primary = target.primary_path()
        if primary is None:
            raise click.ClickException("Workspace has no primary repo configured.")
        repo_path = primary
    else:
        assert target.repo_path is not None
        repo_path = target.repo_path

    ensure_repowise_dir(repo_path)

    async def _do() -> dict:
        engine = create_engine(get_db_url_for_repo(repo_path))
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            row = await get_repository_by_path(session, str(repo_path))
            if row is None:
                row = await upsert_repository(
                    session,
                    name=repo_path.name,
                    local_path=str(repo_path),
                )
            scanner = HistorySecurityScanner(session, row.id)
            summary = await scanner.scan_history(
                Path(repo_path),
                since=since,
                to=to,
                secrets_only=not all_patterns,
                progress=lambda msg: (
                    console.print(f"[dim]{msg}[/dim]") if output_format != "json" else None
                ),
            )
            await session.commit()
            return {
                "commits_scanned": summary.commits_scanned,
                "blobs_scanned": summary.blobs_scanned,
                "files_scanned": summary.files_scanned,
                "findings_inserted": summary.findings_inserted,
                "by_severity": summary.by_severity,
                "by_kind": summary.by_kind,
            }

    result = run_async(_do())

    if output_format == "json":
        click.echo(json.dumps(result, indent=2))
        return

    console.print(f"[bold]repowise security scan --history[/bold] — {repo_path}")
    console.print(f"  Commits scanned: {result['commits_scanned']}")
    console.print(f"  Blobs scanned:   {result['blobs_scanned']}")
    console.print(f"  Files scanned:   {result['files_scanned']}")
    console.print(f"  Findings stored: {result['findings_inserted']}")
    if result["by_severity"]:
        sev = ", ".join(f"{k}={v}" for k, v in sorted(result["by_severity"].items()))
        console.print(f"  By severity:     {sev}")
    if result["by_kind"]:
        kinds = ", ".join(f"{k}={v}" for k, v in sorted(result["by_kind"].items()))
        console.print(f"  By kind:         {kinds}")
    console.print(
        "\nFindings are written to the security_findings table and show up in "
        "`repowise server`'s security API and UI. Re-running is idempotent."
    )
