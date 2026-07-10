"""Advisory-only doctor signals (never flip the pass/fail outcome)."""

from __future__ import annotations

from rich.table import Table

from repowise.cli.helpers import console


def _claude_md_stamp_status(repo_path, state: dict) -> tuple[bool, str] | None:
    """Compare the managed CLAUDE.md "Last indexed" commit to state.json.

    Returns ``(ok, detail)`` or ``None`` to skip the check (no CLAUDE.md, no
    stamp, or no synced commit yet). After any index/update the stamp and
    ``state.json``'s ``last_sync_commit`` should agree; a mismatch means
    editor-file regeneration stopped (e.g. the workspace-update refresh bug or
    ``editor_files.claude_md`` disabled), so the injected "Last indexed" line is
    stale and trains agents to distrust the index. Compared against the synced
    commit, not live HEAD, so being a few commits behind HEAD is not flagged.
    """
    import re

    claude_md = repo_path / ".claude" / "CLAUDE.md"
    try:
        text = claude_md.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"Last indexed:.*?\(commit\s+([0-9a-fA-F]{7,})\)", text)
    if not m:
        # No stamp, or a too-short/abbreviated sha we can't compare safely.
        return None
    stamp = m.group(1).lower()
    synced = ((state or {}).get("last_sync_commit") or "").lower()
    if not synced:
        return None
    if synced.startswith(stamp) or stamp.startswith(synced):
        return (True, f"in sync at {stamp}")
    return (
        False,
        f"stamp {stamp} != index {synced[:8]} — run `repowise update` "
        "or `repowise claude-md` to refresh",
    )


def _advise_claude_md_stamp(repo_path, state: dict) -> None:
    """Print an advisory line when the CLAUDE.md stamp lags the index.

    Advisory only (never flips the doctor's pass/fail): a stamp can briefly lag
    when a commit lands mid-update, which self-heals on the next sync. Skipped
    entirely when ``editor_files.claude_md`` is disabled, since there is nothing
    to refresh and the advice would be un-actionable.
    """
    from repowise.cli.editor_integrations.claude import _claude_md_enabled

    if not _claude_md_enabled(repo_path):
        return
    status = _claude_md_stamp_status(repo_path, state)
    if status is None:
        return
    ok, detail = status
    if not ok:
        console.print(f"[yellow]CLAUDE.md stamp drift:[/yellow] {detail}")


def _print_cli_version_status() -> None:
    """Print a best-effort CLI update-check line.

    Advisory only: an outdated CLI is informational, not a broken repo, so this
    never affects doctor's pass/fail outcome and never fails on network errors.
    Runs once per invocation (the CLI version is global, not per-repo).
    """
    try:
        from repowise.cli.update_check import get_cli_update_check

        check = get_cli_update_check()
    except Exception:
        return  # never let the update check break doctor

    # Show the full running command and resolved path verbatim — they can
    # differ (e.g. a stale shim on PATH vs the venv that launched this process),
    # and surfacing that mismatch is the point of this row.
    path_detail = check.resolved_executable or "not on PATH"
    running = check.running_executable or "?"

    if check.latest_version is None:
        status = "[green]OK[/green]"
        detail = (
            f"current {check.current_version}, could not check latest version, "
            f"path {path_detail}, running {running}"
        )
    elif check.update_available:
        status = "[yellow]WARN[/yellow]"
        detail = (
            f"current {check.current_version}, latest {check.latest_version}, "
            f"path {path_detail}, running {running}"
        )
    else:
        status = "[green]OK[/green]"
        detail = f"current {check.current_version} (latest), path {path_detail}, running {running}"

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="cyan")
    table.add_column()
    table.add_column()
    table.add_row("CLI version", status, detail)
    console.print(table)

    if check.update_available:
        console.print(f"  [yellow]Update available:[/yellow] {check.suggested_command}")
        console.print("  [dim]Restart Claude/Codex/Cursor or any MCP client after updating.[/dim]")
