"""Lightweight data records and path helpers shared across git-index tiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._constants import _CODE_EXTENSIONS

__all__ = [
    "GitIndexSummary",
    "_CommitRec",
    "_extract_rename_paths",
    "_should_skip_index",
]


@dataclass
class _CommitRec:
    """Lightweight commit record parsed from ``git log --numstat``."""

    sha: str
    author_name: str
    author_email: str
    ts: int  # unix epoch
    is_merge: bool
    subject: str
    added: int = 0
    deleted: int = 0


@dataclass
class GitIndexSummary:
    files_indexed: int
    hotspots: int
    stable_files: int
    duration_seconds: float = 0.0


_RENAME_RE = re.compile(r"\{(.+?) => (.+?)\}")


def _extract_rename_paths(
    stat_path: str, known_paths: set[str]
) -> tuple[str | None, str | None]:
    """Extract old/new paths from a git numstat rename line and add to *known_paths*.

    Git ``--numstat`` with ``--follow`` emits rename lines like::

        10\t5\t{old => new}/shared_suffix
        10\t5\told_dir/{old_name => new_name}.py

    This helper parses both forms, adds both expanded paths to *known_paths*,
    and returns ``(old_path, new_path)`` so the caller can attribute churn to
    the correct file.  Returns ``(None, None)`` if the pattern is not found.
    """
    m = _RENAME_RE.search(stat_path)
    if m:
        prefix = stat_path[: m.start()]
        suffix = stat_path[m.end() :]
        old_path = prefix + m.group(1) + suffix
        new_path = prefix + m.group(2) + suffix
        known_paths.add(old_path)
        known_paths.add(new_path)
        return old_path, new_path
    return None, None


def _should_skip_index(file_path: str) -> bool:
    """Return True for files where per-file git indexing should be skipped.

    Uses an allowlist: only files with known source-code extensions are indexed.
    Everything else (data, config, markup, dotfiles, binaries) is skipped.
    """
    return Path(file_path).suffix.lower() not in _CODE_EXTENSIONS
