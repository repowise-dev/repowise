"""The git helpers every change reader shares: split a revspec, run git, filter
changed paths, list the untracked files and the managed files only repowise edited.

A leaf (only the owned-path list beside it), so a caller that only needs the
diff shape (changed lines, the drift gate's scope) does not load change risk's
baseline and, through it, GitPython.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pathspec

from ..owned_paths import MANAGED_FILES, OWNED_PATTERNS, only_repowise_differs

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


def path_filter(
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
    include_paths: tuple[str, ...] = (),
) -> Callable[[str], bool]:
    """Whether a changed path counts under a caller's suffix, include and exclude filters.

    One rule for every reader of a change (the score, its baseline, the changed
    lines, the health delta), so they agree on which files the change touches.
    Patterns are gitignore-style; an empty filter keeps everything.
    """
    exts = tuple(e if e.startswith(".") else f".{e}" for e in extensions)
    drop = (
        pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns) if exclude_patterns else None
    )
    keep = pathspec.PathSpec.from_lines("gitwildmatch", include_paths) if include_paths else None

    def counts(path: str) -> bool:
        if exts and not path.endswith(exts):
            return False
        if keep is not None and not keep.match_file(path):
            return False
        return drop is None or not drop.match_file(path)

    return counts


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


def untracked_paths(repo_path: str, exclude_patterns: tuple[str, ...] = ()) -> list[str]:
    """Untracked, not-ignored files, root-relative like ``git diff``'s paths.

    The one source of untracked files for every working-tree reader (the score,
    the changed lines, the health delta), so they agree on the file set. Paths
    repowise writes whole are left out here, before anything reads them: an
    unignored index directory holds thousands of files. *exclude_patterns*
    (gitignore-style) drops a caller's excluded paths at the same point.
    """
    listing = _git(
        ["ls-files", "--others", "--exclude-standard", "-z", "--full-name", "--", ":/"],
        repo_path,
        check=False,
    )
    skip = pathspec.PathSpec.from_lines("gitwildmatch", OWNED_PATTERNS + exclude_patterns)
    return [path for path in listing.split("\0") if path and not skip.match_file(path)]


def generated_worktree_paths(repo_path: str) -> tuple[str, ...]:
    """Root-anchored patterns for managed files whose uncommitted change is repowise's alone.

    A file is compared at ``HEAD`` and on disk with repowise's block or entry
    removed, so a refreshed CLAUDE.md block or a newly written ``.mcp.json``
    drops out while any edit of the user's own text keeps the file in.
    """
    root = _git(["rev-parse", "--show-toplevel"], repo_path, check=False).strip()
    if not root:
        return ()
    # --no-renames: a rename entry carries a second path, which would misparse.
    status = ["status", "--porcelain", "-z", "--no-renames", "--untracked-files=all"]
    listing = _git(
        [*status, "--", *MANAGED_FILES],
        root,
        check=False,
    )
    out: list[str] = []
    for path in sorted({entry[3:] for entry in listing.split("\0") if len(entry) > 3}):
        try:
            new = (Path(root) / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            new = ""
        old = _git(["show", f"HEAD:{path}"], root, check=False)
        if only_repowise_differs(path, old, new):
            out.append(f"/{path}")
    return tuple(out)
