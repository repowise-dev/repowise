"""Git commit-id validation and HEAD lookups."""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


_COMMIT_ID_RE = re.compile(r"[0-9a-fA-F]{7,40}")


def as_commit_id(value: object) -> str | None:
    """*value* when it is a full or abbreviated hex commit id, else ``None``.

    Commit ids read back from ``.repowise/state.json`` (or a file beside it)
    can be edited by anyone who can commit that file, so they are checked
    before they reach a ``git`` argument list, where a leading ``-`` would be
    read as an option.
    """
    if isinstance(value, str) and _COMMIT_ID_RE.fullmatch(value):
        return value
    return None


def get_head_commit(repo_path: Path) -> str | None:
    """Return the HEAD commit SHA or ``None`` if not a git repo.

    Delegates to the core implementation (``git rev-parse HEAD``) so the CLI
    and the workspace updater resolve HEAD identically — the old gitpython
    version here could diverge on worktree/detached-HEAD edge cases.
    """
    from repowise.core.workspace.update import get_head_commit as _core_head

    return _core_head(Path(repo_path))


def head_commit_ts(repo_path: Path) -> float | None:
    """Committer timestamp of the repo's HEAD, or None when git is unavailable.

    Anchors the periodic idle-file health re-score gate (#728) to repo time
    rather than wall clock, the same anchor the git history windows use, so
    the cadence is deterministic and correct for historical checkouts.

    Shared with ``init`` so a fresh index can stamp ``last_full_rescore_at`` in
    the same units the gate reads it back in.
    """
    try:
        from repowise.core.git_repo import Repo

        repo = Repo(repo_path, search_parent_directories=True)
        try:
            return float(repo.head.commit.committed_date)
        finally:
            repo.close()
    except Exception:
        return None
