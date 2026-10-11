"""Caller-supplied file paths, reduced to the repo-relative form the stores key on.

Shared by every surface that matches a path by exact string equality: the MCP
risk tool and the code-health routes.
"""

from __future__ import annotations

from pathlib import Path


def normalize_target_path(target: str, repo_root: str | None = None) -> str:
    """Normalize a caller-supplied file path to the POSIX-relative form stored
    in ``git_metadata.file_path``.

    ``get_risk`` matches ``file_path`` by exact string equality, but callers
    reach it through git tools, shell completion, or editors that hand over a
    backslash form (Windows), a leading ``./``, an absolute path, or a trailing
    separator. Any of those makes the row lookup miss, and ``_assess_one_target``
    then reports the indistinguishable ``no git metadata available`` card
    (hotspot_score=0, primary_owner=None, empty co_change_partners) even though
    the row exists — issue #1279. Normalizing the caller's side closes that gap.
    """
    normalized = target.replace("\\", "/")
    # Make a repo-absolute path (``/abs/repo/src/x.py``) relative to the repo
    # root when we know it. Uses a prefix check on the normalized forms, so a
    # path that is already repo-relative is left untouched.
    if repo_root:
        root_path = Path(repo_root).resolve()
        try:
            # Resolve against the repo root, not the process cwd: the MCP
            # server's cwd is not the repo, so a relative path that happens
            # to exist there could resolve somewhere unrelated.
            candidate = Path(normalized)
            resolved = (candidate if candidate.is_absolute() else root_path / candidate).resolve()
            normalized = resolved.relative_to(root_path).as_posix()
        except (OSError, ValueError):
            # Resolution can fail for malformed paths; relative_to() also
            # rejects absolute paths outside the selected repository.
            pass
    # Strip a leading cwd-relative prefix and any leading slash left over.
    # A prefix strip, not lstrip: lstrip takes a character set, so it would
    # eat every leading dot (``.github/...`` -> ``github/...``).
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    # Collapse duplicate slashes and any trailing separator.
    parts = [p for p in normalized.split("/") if p]
    return "/".join(parts)
