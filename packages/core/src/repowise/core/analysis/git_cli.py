"""The two git helpers every change reader shares: split a revspec, run git.

A leaf with no package imports, so a caller that only needs the diff shape
(changed lines, the drift gate's scope) does not load change risk's baseline
and, through it, GitPython.
"""

from __future__ import annotations

import subprocess

# Generous ceiling: even a 200-commit numstat walk finishes in seconds. The
# point is that a stuck git (lock contention, network filesystem) must fail
# loud instead of hanging the caller's thread forever.
GIT_TIMEOUT_SECONDS = 60


def split_revspec(revspec: str) -> tuple[str, str, str] | None:
    """Split ``base..head`` / ``base...head`` into ``(base, sep, head)``.

    ``None`` for a single revision. An empty side means ``HEAD``, as in git. Three dots
    keep their git meaning (diff from the merge-base), so every reader of a
    range measures the same change.
    """
    sep = "..." if "..." in revspec else ".." if ".." in revspec else None
    if sep is None:
        return None
    base, _, head = revspec.partition(sep)
    return base or "HEAD", sep, head or "HEAD"


def _git(args: list[str], cwd: str, *, check: bool = True) -> str:
    # stdin=DEVNULL: on MCP stdio transport a child that inherits the JSON-RPC
    # pipe handles can wedge the session (same failure mode _meta.py guards
    # against). check=True so a bad revspec raises instead of yielding empty
    # stdout, which used to score as a zero-feature "low risk" change.
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
        )
    return proc.stdout
