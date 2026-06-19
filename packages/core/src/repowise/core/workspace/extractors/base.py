"""Shared file-scanning primitives for contract extractors.

Both the HTTP and gRPC extractors walk a repository the same way: skip a fixed
set of build/vendor directories, ignore files above a size cap, and read each
candidate file as UTF-8. That traversal lives here once so the per-contract-type
orchestrators only declare *which* extensions they care about.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Directories that never contain first-party source worth scanning.
BLOCKED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
        "vendor",
        ".next",
        ".nuxt",
        ".tox",
        ".mypy_cache",
        ".gradle",
        ".mvn",
        "out",
        "bin",
    }
)

MAX_FILE_SIZE = 512 * 1024  # 512 KB


@dataclass(frozen=True)
class ScanContext:
    """A single source file presented to a dialect.

    ``rel_path`` is POSIX-relative to the repo root; ``suffix`` is the
    lower-cased file extension (including the dot); ``content`` is the decoded
    file text.
    """

    repo_alias: str
    rel_path: str
    suffix: str
    content: str


def iter_source_files(
    repo_root: Path, extensions: frozenset[str]
) -> Iterator[tuple[str, str, str]]:
    """Yield ``(rel_path, suffix, content)`` for each scannable source file.

    Walks *repo_root*, pruning :data:`BLOCKED_DIRS` and dotfile directories,
    skipping files whose extension is not in *extensions* or that exceed
    :data:`MAX_FILE_SIZE`, and reading the rest as UTF-8 (replacing undecodable
    bytes). Unreadable files are silently skipped.
    """
    root = repo_root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in BLOCKED_DIRS and not d.startswith(".")]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            suffix = fpath.suffix.lower()
            if suffix not in extensions:
                continue
            try:
                if fpath.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield fpath.relative_to(root).as_posix(), suffix, content
