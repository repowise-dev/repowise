"""The ``repowise doctor`` Click command entrypoint."""

from __future__ import annotations

import json

import click

from repowise.cli.helpers import (
    console,
    err_console,
    resolve_command_target,
    silence_logs_for_machine_output,
)

from ._types import DoctorCheck
from .advisories import _print_cli_version_status
from .repo_checks import _run_repo_checks
from .workspace_checks import _run_workspace_checks


@click.command("doctor")
@click.argument("path", required=False, default=None)
@click.option("--repair", is_flag=True, default=False, help="Attempt to fix detected mismatches.")
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (run checks against every repo in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    help="Output format. json is read-only (incompatible with --repair) and exits "
    "1 when any check fails.",
)
def doctor_command(
    path: str | None,
    repair: bool,
    workspace: bool,
    no_workspace: bool,
    fmt: str,
) -> None:
    """Run health checks on the wiki setup.

    Auto-detects workspace mode when invoked from a workspace root. In
    workspace mode, runs the full check battery against each indexed repo
    and prints a per-repo table plus a workspace-level summary.
    """
    if fmt != "table" and repair:
        raise click.UsageError(
            "--repair is not supported with --format json (json mode is read-only)."
        )

    if fmt != "table":
        silence_logs_for_machine_output()

    status = err_console if fmt != "table" else console

    target = resolve_command_target(
        path=path,
        workspace_flag=workspace,
        no_workspace_flag=no_workspace,
    )
    target.notice(status, command="doctor")

    if fmt == "table":
        # Advisory CLI update check, printed once above the repo check table(s).
        _print_cli_version_status()

    if not target.is_workspace:
        assert target.repo_path is not None
        all_ok, checks = _run_repo_checks(target.repo_path, repair, fmt=fmt)
        if fmt != "table":
            payload = {"ok": all_ok, "checks": [c._asdict() for c in checks]}
            click.echo(json.dumps(payload, indent=2))
            if not all_ok:
                raise SystemExit(1)
        return

    # Workspace mode — iterate over every entry, run workspace-level
    # validation, and report a summary table at the end so the user knows
    # which repos need attention.
    assert target.ws_root is not None and target.ws_config is not None
    ws_root = target.ws_root
    ws_config = target.ws_config

    ws_issues = _run_workspace_checks(ws_root, ws_config, repair=repair, fmt=fmt)

    overall_ok = True
    not_indexed: list[str] = []
    all_checks: list[DoctorCheck] = []
    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        if not abs_path.is_dir():
            continue
        if not (abs_path / ".repowise").is_dir():
            not_indexed.append(entry.alias)
            continue
        if fmt == "table":
            console.print()
            console.print(
                f"[bold]── {entry.alias}[/bold]  "
                f"[dim]({entry.path})[/dim]"
                + (
                    "  [bold cyan](primary)[/bold cyan]"
                    if entry.alias == ws_config.default_repo
                    else ""
                )
            )
        ok, checks = _run_repo_checks(abs_path, repair, fmt=fmt)
        overall_ok = overall_ok and ok
        all_checks.extend(DoctorCheck(f"{entry.alias}: {c.name}", c.ok, c.detail) for c in checks)

    if fmt != "table":
        all_ok = overall_ok and not ws_issues and not not_indexed
        payload = {
            "ok": all_ok,
            "checks": [c._asdict() for c in all_checks],
            "workspace": {
                "checked": True,
                "issues": list(ws_issues),
                "not_indexed": not_indexed,
            },
        }
        click.echo(json.dumps(payload, indent=2))
        if not all_ok:
            raise SystemExit(1)
        return

    console.print()
    if not_indexed:
        console.print(f"[yellow]Not indexed:[/yellow] {', '.join(not_indexed)}")
        console.print("  Run [bold]repowise update --workspace[/bold] to index them.")
    if ws_issues and not repair:
        console.print(
            f"[yellow]{len(ws_issues)} workspace-level issue(s); "
            f"rerun with [bold]--repair[/bold] to attempt fixes.[/yellow]"
        )

    workspace_clean = not ws_issues and overall_ok and not not_indexed
    if workspace_clean:
        console.print("[bold green]Workspace healthy.[/bold green]")
    elif overall_ok and not ws_issues:
        console.print("[bold yellow]All indexed repos healthy; some repos unindexed.[/bold yellow]")
    else:
        console.print("[bold yellow]Some checks failed across the workspace.[/bold yellow]")
