"""Workspace-level doctor checks (per-entry drift, MCP registration)."""

from __future__ import annotations

from pathlib import Path as _DoctorPath

from rich.table import Table

from repowise.cli.helpers import console


def _run_workspace_checks(
    ws_root: _DoctorPath,
    ws_config,
    *,
    repair: bool,
    fmt: str = "table",
) -> list[str]:
    """Run workspace-level validation. Returns a list of issue strings.

    Covers:
      - Per-entry directory existence & ``.git`` presence.
      - State drift between ``WorkspaceConfig.last_commit_at_index`` and
        each repo's ``.repowise/state.json``.
      - MCP server registration (best-effort detection in claude config).
      - ``--repair``: rebuild missing ``state.json``, drop dead workspace
        entries (with a notice). Skipped when ``fmt != "table"``; callers
        must not pass ``repair=True`` with a non-table format.
    """
    from repowise.core.workspace.update import (
        read_state_commit,
        sync_workspace_state_from_disk,
    )

    rows: list[tuple[str, str, str]] = []
    issues: list[str] = []

    dead_entries: list[str] = []
    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()

        # Dir & git presence
        if not abs_path.is_dir():
            rows.append((entry.alias, "[red]MISSING[/red]", f"directory not found: {entry.path}"))
            dead_entries.append(entry.alias)
            issues.append(f"{entry.alias}: missing directory")
            continue
        if not (abs_path / ".git").exists():
            rows.append((entry.alias, "[yellow]WARN[/yellow]", "not a git repo"))
            issues.append(f"{entry.alias}: not a git repo")

        # State drift
        disk_commit = read_state_commit(abs_path)
        cfg_commit = entry.last_commit_at_index
        if disk_commit and cfg_commit and disk_commit != cfg_commit:
            rows.append(
                (
                    entry.alias,
                    "[yellow]DRIFT[/yellow]",
                    f"config={cfg_commit[:8]}, state.json={disk_commit[:8]}",
                )
            )
            issues.append(f"{entry.alias}: workspace config / state.json drift")
        elif disk_commit and not cfg_commit:
            rows.append(
                (
                    entry.alias,
                    "[yellow]DRIFT[/yellow]",
                    f"workspace config missing last_commit_at_index (state.json has {disk_commit[:8]})",
                )
            )
            issues.append(f"{entry.alias}: workspace config missing commit pointer")
        elif (abs_path / ".repowise").is_dir() and not disk_commit:
            rows.append(
                (
                    entry.alias,
                    "[yellow]WARN[/yellow]",
                    "state.json missing or empty (run `repowise update`)",
                )
            )
            issues.append(f"{entry.alias}: missing state.json")
        else:
            rows.append((entry.alias, "[green]OK[/green]", entry.path))

    if fmt == "table":
        table = Table(title="repowise Workspace Doctor")
        table.add_column("Repo", style="cyan")
        table.add_column("Status")
        table.add_column("Detail")
        for r in rows:
            table.add_row(*r)
        console.print(table)

        # MCP server registration: best-effort, advisory only.
        _check_mcp_registered(ws_root)

    # --repair: sync the workspace config from disk and drop dead entries.
    if repair:
        changed = sync_workspace_state_from_disk(ws_root, ws_config)
        if changed:
            console.print(
                f"[green]Repaired workspace config from disk for:[/green] {', '.join(changed)}"
            )
        if dead_entries:
            console.print(
                f"[yellow]Removing dead workspace entries:[/yellow] {', '.join(dead_entries)}"
            )
            for alias in dead_entries:
                ws_config.remove_repo(alias)
            ws_config.save(ws_root)
            console.print("[green]Workspace config updated.[/green]")
        if not changed and not dead_entries:
            console.print("[green]No workspace-level repairs needed.[/green]")

    return issues


def _check_mcp_registered(ws_root: _DoctorPath) -> None:
    """Best-effort check that a Claude MCP entry points at this workspace.

    The check is advisory: a missing entry is not an error, since the user
    may use the HTTP server or a different MCP client. We just print a
    helpful hint so the workspace can be wired up if the user wants it.
    """
    import json as _json
    import os as _os

    candidates: list[_DoctorPath] = []
    appdata = _os.environ.get("APPDATA")
    if appdata:
        candidates.append(_DoctorPath(appdata) / "Claude" / "claude_desktop_config.json")
    home = _DoctorPath.home()
    candidates.extend(
        [
            home / ".claude" / "claude_desktop_config.json",
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            home / ".config" / "Claude" / "claude_desktop_config.json",
        ]
    )

    found_paths: list[str] = []
    for cfg in candidates:
        if not cfg.is_file():
            continue
        try:
            data = _json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            continue
        servers = data.get("mcpServers", {}) or {}
        for name, spec in servers.items():
            args = spec.get("args", []) if isinstance(spec, dict) else []
            arg_str = " ".join(str(a) for a in args)
            if str(ws_root) in arg_str or str(ws_root.resolve()) in arg_str:
                found_paths.append(f"{cfg.name}:{name}")

    if found_paths:
        console.print(f"  [dim]MCP: registered ({', '.join(found_paths)})[/dim]")
    else:
        console.print(
            "  [dim]MCP: no claude_desktop_config.json entry found — run "
            "`repowise hook install` to register.[/dim]"
        )
