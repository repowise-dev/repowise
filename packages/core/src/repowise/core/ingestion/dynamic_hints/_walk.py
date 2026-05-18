"""Pruned filesystem walk for dynamic-hint extractors.

Every extractor in this package used to call ``repo_root.rglob(pattern)``
directly, which descended into ``node_modules``, ``.venv``, ``.next``,
``__pycache__`` and other multi-million-file junk trees. On a polyrepo
with sibling ``backend/`` and ``frontend/`` directories the dynamic-hints
phase could stall for 5–10+ minutes per extractor with the progress bar
stuck at zero.

``iter_glob`` uses :func:`os.walk` with in-place ``dirnames`` pruning so
known-junk subdirectories are skipped at the point of traversal, not
afterwards. It also guards against:

  - **Symlink loops** — ``os.walk`` is called with ``followlinks=False``.
  - **Junction loops** (Windows) — caught via :func:`os.path.realpath`
    cycle detection.
  - **Pathological recursive copies** — same cycle detector handles real
    directories that share an inode through hard-linking, junctions, or
    repeated nested-copy mistakes.
  - **Catastrophic depth** — a hard depth cap as a final safety net.

Semantics match :py:meth:`pathlib.Path.rglob`: both files and directories
whose basename matches the glob are yielded.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# Directory basenames that are never source-bearing. Pruned at every
# level of the walk. Keep this list conservative: anything ambiguously
# named (``bin``, ``obj``, ``lib``) belongs out of here so we don't
# accidentally drop real source.
PRUNED_DIRS: frozenset[str] = frozenset(
    {
        # VCS / metadata
        ".git",
        ".hg",
        ".svn",
        # Python environments / caches
        ".venv",
        "venv",
        ".env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        # JS / TS
        "node_modules",
        ".next",
        ".nuxt",
        ".turbo",
        ".parcel-cache",
        # Build artifacts (multi-language)
        "dist",
        "build",
        "out",
        ".gradle",
        ".cache",
        # Editor / IDE
        ".idea",
        ".vscode",
        # Coverage / test outputs
        "coverage",
        "htmlcov",
        ".nyc_output",
        # Repowise / data
        ".repowise",
        ".lancedb",
    }
)

# Safety net: bail if walk depth grows pathologically. Real-world repos
# rarely exceed depth 20; anything beyond strongly suggests a cycle.
_MAX_WALK_DEPTH = 64


def iter_glob(root: Path, pattern: str) -> Iterator[Path]:
    """Recursively yield paths under ``root`` whose basename matches ``pattern``.

    Mirrors :py:meth:`pathlib.Path.rglob` semantics — files *and*
    directories with a matching basename are yielded — but:
      - prunes well-known junk directories at every level,
      - never follows symlinks (``os.walk(followlinks=False)``),
      - detects junction / hard-link cycles via realpath tracking,
      - caps total depth as a final safety net.

    Args:
        root:    Directory to walk. May be a string or :class:`Path`.
        pattern: A :mod:`fnmatch`-style glob applied to the basename.
                 Examples: ``"*.go"``, ``"settings.py"``, ``"package.json"``,
                 ``"tsconfig*.json"``.
    """
    root_path = Path(root)
    if not root_path.exists():
        return

    try:
        root_real = os.path.realpath(root_path)
    except OSError:
        return
    base_depth = root_real.rstrip(os.sep).count(os.sep)
    visited_real: set[str] = {root_real}

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        # Skip junk directories at every level.
        dirnames[:] = [d for d in dirnames if d not in PRUNED_DIRS]

        # Drop any dir whose realpath we've already entered — catches
        # Windows junctions and any other cycle that os.walk can't detect
        # via inode (Windows doesn't expose real inodes for filesystems
        # outside of NTFS proper).
        pruned: list[str] = []
        for d in list(dirnames):
            child = os.path.join(dirpath, d)
            try:
                child_real = os.path.realpath(child)
            except OSError:
                pruned.append(d)
                continue
            if child_real in visited_real:
                pruned.append(d)
                continue
            visited_real.add(child_real)
        if pruned:
            dirnames[:] = [d for d in dirnames if d not in pruned]

        # Final safety net: refuse to descend beyond _MAX_WALK_DEPTH.
        cur_depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if cur_depth >= _MAX_WALK_DEPTH:
            log.warning(
                "dynamic_hints.walk_depth_exceeded",
                root=str(root_path),
                depth=cur_depth,
                at=dirpath,
            )
            dirnames[:] = []
            continue

        for name in filenames:
            if fnmatch.fnmatch(name, pattern):
                yield Path(dirpath) / name
        for name in dirnames:
            if fnmatch.fnmatch(name, pattern):
                yield Path(dirpath) / name
