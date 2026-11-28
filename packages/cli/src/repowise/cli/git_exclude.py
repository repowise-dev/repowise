"""Keeping repowise's machine-specific files out of git, through ``.git/info/exclude``.

Only the clone-local exclude file is written, never ``.gitignore``: whether a
file is shared is the team's call, and an exclude line is invisible to them.
Which files count is :data:`~repowise.core.owned_paths.MACHINE_SPECIFIC_FILES`, plus
a workspace root's config and data directory.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from repowise.core.owned_paths import MACHINE_SPECIFIC_FILES
from repowise.core.workspace.config import WORKSPACE_CONFIG_FILENAME, WORKSPACE_DATA_DIR

HEADER = "# repowise (machine-specific)"


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _layout(repo_path: Path) -> tuple[Path, str] | None:
    """``(exclude file, repo prefix inside the work tree)``, or ``None`` outside git.

    ``--git-path`` resolves a linked worktree's ``.git`` file to the common
    dir, where git reads ``info/exclude`` for every worktree.
    """
    result = _git(repo_path, "rev-parse", "--show-prefix", "--git-path", "info/exclude")
    if result is None or result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return None
    prefix, exclude = lines[0].strip(), Path(lines[1].strip())
    if not exclude.is_absolute():
        exclude = repo_path / exclude
    return exclude, prefix


def _listed(repo_path: Path, args: tuple[str, ...], paths: list[str]) -> set[str]:
    result = _git(repo_path, *args, "--", *paths)
    if result is None:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def is_tracked(repo_path: Path, rel_path: str) -> bool:
    """Whether git tracks *rel_path*. False outside git; True when git cannot be run."""
    result = _git(repo_path, "ls-files", "--error-unmatch", "--", rel_path)
    return result is None or result.returncode == 0


def keep_out_candidates(repo_path: Path, planned: Iterable[str] = ()) -> tuple[str, ...]:
    """Machine-specific files git would still see: present or *planned*, untracked, not ignored."""
    if _layout(repo_path) is None:
        return ()
    wanted = set(planned)
    paths = [p for p in MACHINE_SPECIFIC_FILES if p in wanted or (repo_path / p).is_file()]
    if not paths:
        return ()
    tracked = _listed(repo_path, ("ls-files",), paths)
    paths = [p for p in paths if p not in tracked]
    if not paths:
        return ()
    ignored = _listed(repo_path, ("check-ignore", "--no-index"), paths)
    return tuple(p for p in paths if p not in ignored)


def _missing_block(existing: str, lines: Iterable[str]) -> tuple[list[str], str]:
    """``(lines not in *existing*, text to append)``; the header only once per file."""
    present = {line.strip() for line in existing.splitlines()}
    added = [line for line in dict.fromkeys(lines) if line not in present]
    if not added:
        return [], ""
    block = ([] if HEADER in present else [HEADER]) + added
    lead = "" if not existing or existing.endswith("\n") else "\n"
    return added, lead + "\n".join(block) + "\n"


def add_to_exclude(repo_path: Path, paths: Iterable[str]) -> list[str]:
    """Append the missing *paths* to the exclude file; returns the lines added.

    Anchored to the repo (``/<prefix><path>``) so a same-named file elsewhere
    stays visible. A no-op outside git or when every line is already there.
    """
    layout = _layout(repo_path)
    if layout is None:
        return []
    exclude, prefix = layout
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        added, text = _missing_block(existing, (f"/{prefix}{p}" for p in paths))
        if added:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    except (OSError, UnicodeDecodeError):
        return []
    return added


def keep_workspace_data_out(workspace_root: Path) -> list[str]:
    """Exclude the workspace config and data dir when the workspace root is in a git work tree.

    Both are per machine (local paths, index timestamps, derived analysis).
    A path a ``.gitignore`` already covers gets no second line.
    """
    if _layout(workspace_root) is None:
        return []
    entries = [WORKSPACE_CONFIG_FILENAME, f"{WORKSPACE_DATA_DIR}/"]
    ignored = _listed(workspace_root, ("check-ignore", "--no-index"), entries)
    return add_to_exclude(workspace_root, [e for e in entries if e not in ignored])
