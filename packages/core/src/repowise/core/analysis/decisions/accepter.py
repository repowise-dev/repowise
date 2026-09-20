"""Who is accepting a decision, when a command line is the surface.

An acceptance without an identity is not auditable, and asking for one on every
confirm would make the common case worse than the current unattributed promote.
So it is resolved: the repository's own git identity first, because that is the
name the decision's commits will carry, then the OS user, then a last-resort
constant that at least says the acceptance was local and manual.
"""

from __future__ import annotations

import getpass
import subprocess
from pathlib import Path

__all__ = ["is_tracked", "resolve_accepter"]

_FALLBACK = "local"


def _git_identity(repo_path: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def resolve_accepter(repo_path: Path | str, *, override: str = "") -> str:
    """The identity to stamp on an acceptance, never empty."""
    if override.strip():
        return override.strip()
    name = _git_identity(Path(repo_path))
    if name:
        return name
    try:
        return getpass.getuser() or _FALLBACK
    except Exception:
        return _FALLBACK


def is_tracked(repo_path: Path | str, rel_path: str) -> bool:
    """Whether *rel_path* is committed, so somebody other than this machine saw it.

    The tracked-artifact acceptance rests entirely on this: an ADR a coding
    agent wrote and never committed is a file on one machine, not a statement
    the team reviewed.

    Only an actual "git tracks this repository and not this file" is a refusal.
    Where there is no git to ask — an unversioned directory, no git binary —
    there is no commit gate to fail, and the document is exactly as
    authoritative as anything else the user put on disk.
    """
    if not rel_path:
        return False
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel_path],
            cwd=Path(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    # 0 tracked, 1 untracked, 128 not a repository.
    return proc.returncode != 1
