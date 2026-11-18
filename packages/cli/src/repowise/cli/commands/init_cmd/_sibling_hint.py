"""Point a single-repo init at workspace mode when the repo's folder holds other repos."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.helpers import console, find_workspace_root
from repowise.core.workspace import scan_for_repos


def sibling_repo_count(repo_path: Path) -> int:
    """Git repos directly in *repo_path*'s parent, this one included; 0 when no hint fits.

    Only the parent's direct children are scanned, so the check stays one
    directory listing. A home or filesystem root is skipped: a folder of
    unrelated clones is not a workspace anyone asked for.
    """
    parent = repo_path.parent
    if parent == repo_path or parent in (Path.home().resolve(), Path(parent.anchor)):
        return 0
    if find_workspace_root(repo_path) is not None:
        return 0  # already a workspace member; init handles that case itself
    try:
        repos = scan_for_repos(parent, max_depth=1).repos
    except OSError:
        return 0  # an unreadable sibling must not fail an init this note only advises

    return len(repos) if len(repos) > 1 else 0


def suggest_workspace(repo_path: Path) -> None:
    """Name the workspace command when *repo_path* sits next to other git repos."""
    count = sibling_repo_count(repo_path)
    if not count:
        return
    parent = repo_path.parent
    console.print(
        f"[dim]{parent} holds {count} git repos, this one included. To index them "
        f'together as a workspace, run [bold]repowise init "{parent}"[/bold] instead. '
        "Pass --no-workspace to hide this note.[/dim]\n"
    )
