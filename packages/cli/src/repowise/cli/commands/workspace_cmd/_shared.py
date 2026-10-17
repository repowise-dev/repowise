"""Helpers shared by the ``repowise workspace`` subcommands."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import click

from repowise.cli.helpers import console, find_workspace_root

if TYPE_CHECKING:
    from repowise.core.workspace.config import WorkspaceConfig


def _require_workspace(start: Path | None = None) -> tuple[Path, WorkspaceConfig]:  # type: ignore[name-defined]
    """Load the workspace config or abort with a helpful message.

    Returns ``(ws_root, ws_config)``.
    """
    from repowise.core.workspace.config import WorkspaceConfig

    ws_root = find_workspace_root(start)
    if ws_root is None:
        raise click.ClickException(
            "No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' to create a workspace."
        )
    ws_config = WorkspaceConfig.load(ws_root)
    return ws_root, ws_config


def _format_age(generated_at: str | None) -> str:
    """Render an ISO timestamp as a short age, or say it is missing."""
    from datetime import datetime

    if not generated_at:
        return "never built"
    try:
        stamped = datetime.fromisoformat(generated_at)
    except ValueError:
        return f"stamped {generated_at}"
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - stamped).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    if seconds < 172800:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


def _report_artifact_provenance(
    ws_root: Path,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
    generated_at: str | None,
    artifact_repos: list[str],
) -> None:
    """Print how old the artifact is, and flag config/artifact disagreement.

    Commands that read a persisted artifact otherwise present it as the state
    of the tree right now. When the config and the artifact list different
    repos, that gap is itself the finding — it means the artifact describes a
    workspace that no longer exists, or that the config lost a repo.
    """
    console.print(f"[dim]Read from .repowise-workspace/, built {_format_age(generated_at)}.[/dim]")

    configured = {e.alias for e in ws_config.repos}
    covered = set(artifact_repos)
    missing_from_config = sorted(covered - configured)
    missing_from_artifact = sorted(configured - covered)
    if missing_from_config:
        console.print(
            f"[yellow]![/yellow] Covers {', '.join(missing_from_config)}, which "
            f"{'is' if len(missing_from_config) == 1 else 'are'} no longer in "
            f"{ws_root.name}/.repowise-workspace.yaml."
        )
    if missing_from_artifact:
        console.print(
            f"[yellow]![/yellow] {', '.join(missing_from_artifact)} "
            f"{'is' if len(missing_from_artifact) == 1 else 'are'} configured but "
            "absent from this artifact; re-run 'repowise update --workspace'."
        )
