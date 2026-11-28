"""The repo's own Python under ``packages/``, read and parsed once per process.

The guard tests that police the source tree (no second copy of a shared
helper, every writer passes the same arguments, no vocabulary nothing emits)
each walked ``packages/`` and parsed all of it, about 2,600 files, once per
test. This holds one parse per process; under xdist each worker builds it the
first time one of its tests asks. Each caller still picks its own files from
the full list (installed ``node_modules`` aside, which no guard means to police).

The trees are shared between tests: read them, never mutate them. Holding them
costs about 0.9 GB per process; reading files on demand per test is the
fallback if that ever matters more than the time.
"""

from __future__ import annotations

import ast
import functools
import os
from dataclasses import dataclass
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2] / "packages"


@dataclass(frozen=True)
class SourceFile:
    path: Path
    # Repo-relative, POSIX separators: ``packages/core/src/...``.
    rel: str
    # ``None`` when the bytes are not UTF-8.
    text: str | None
    # ``None`` when the file is not UTF-8 or not valid Python.
    tree: ast.Module | None


@functools.cache
def python_sources() -> tuple[SourceFile, ...]:
    """Every ``*.py`` under ``packages/`` outside ``node_modules``, sorted by path.

    ``node_modules`` is pruned, not filtered: the UI packages' installed
    dependencies cost about 25 s to walk and hold no Python of ours.
    """
    paths = []
    for top, dirs, files in os.walk(PACKAGES):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        paths += [Path(top, name) for name in files if name.endswith(".py")]
    return tuple(_load(path) for path in sorted(paths))


def _load(path: Path) -> SourceFile:
    rel = path.relative_to(PACKAGES.parent).as_posix()
    try:
        # ``-sig``: Python runs a file that starts with a BOM, so a guard must
        # read it too; plain utf-8 keeps the BOM and the parse fails.
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return SourceFile(path, rel, None, None)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    return SourceFile(path, rel, text, tree)


def sources_under(*roots: Path) -> list[SourceFile]:
    """The files under any of *roots* (directories or single files)."""
    resolved = [root.resolve() for root in roots]
    return [
        src
        for src in python_sources()
        if any(src.path == root or root in src.path.parents for root in resolved)
    ]
