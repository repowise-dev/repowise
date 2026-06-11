"""Pruned filesystem walking — the ONE way to scan a repo tree.

Every repo-wide file-discovery scan in repowise MUST go through this module.
``Path.rglob`` descends into ``node_modules``, ``.venv``, ``__pycache__`` and
— critically — **nested git repositories** (a checkout that physically
contains sibling/vendored repos). That has two failure modes we have shipped
and fixed more than once:

1. **Performance** — an unpruned walk over a directory that holds 30+ sibling
   repos (each with its own ``node_modules`` / ``.venv``) turns a <2s scan
   into minutes, while holding the GIL and starving concurrent phases.
2. **Correctness** — manifests from a *different* repo (``hugo/go.mod``,
   ``eShop/*.sln``) leak into the current repo's resolver context.

History: this logic first shipped privately in ``dynamic_hints/_walk.py``
(perf incident: 5-10 min stalls per extractor), then again in
``resolvers/ts_workspace.py`` (PR #368: 365s → 0.02s), while ~20 other
``rglob`` call sites stayed unpruned. This module is the single shared
implementation; ``tests/unit/ingestion/test_fs_walk.py`` carries a guard
test that fails on any new direct ``rglob`` call in ``packages/core``.

Guarantees:
  - Junk directories are pruned **at traversal time** (``dirnames[:] =``),
    never post-hoc.
  - Nested git repos (a subdirectory with its own ``.git`` dir *or* file —
    worktrees and submodules use a ``.git`` file) are pruned by default.
    Detection is free: ``.git`` appears in ``os.walk``'s own listings.
  - Symlinks are never followed; junction/hard-link cycles are detected via
    realpath tracking (Windows ``os.walk`` can't see them via inodes).
  - Depth is capped as a final safety net.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

__all__ = ["PRUNED_DIRS", "PRUNED_DIRS_DERIVED", "WalkSnapshot", "iter_glob", "walk_repo"]

# Directory basenames that can NEVER hold first-party source or manifests:
# VCS metadata, package/venv caches, and tool caches. Pruned at every level
# of the walk. Keep this list strictly to never-source names: anything a
# real project could plausibly use as a source directory (``build``,
# ``out``, ``dist``, ``coverage`` — coveragepy's main package literally
# lives in ``coverage/``) belongs in :data:`PRUNED_DIRS_DERIVED` instead,
# and ambiguous names (``bin``, ``obj``, ``lib``, ``vendor``, ``target``)
# stay out of both — callers post-filter (dotnet skips ``bin``/``obj``,
# go skips ``vendor``).
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
        ".yarn",
        ".pnpm-store",
        # Tool caches
        ".gradle",
        ".cache",
        # Editor / IDE
        ".idea",
        ".vscode",
        # Coverage / test outputs (unambiguous names only)
        "htmlcov",
        ".nyc_output",
        # Repowise / data
        ".repowise",
        ".lancedb",
    }
)

# PRUNED_DIRS plus common *derived-output* names that occasionally double as
# real source dirs. Use for scans where derived trees would only add noise
# (dynamic hints' settings/router extraction); do NOT use for manifest
# discovery, where a module legitimately rooted at ``build/`` or
# ``coverage/`` must still be found.
PRUNED_DIRS_DERIVED: frozenset[str] = PRUNED_DIRS | frozenset({"dist", "build", "out", "coverage"})

# Safety net: bail if walk depth grows pathologically. Real-world repos
# rarely exceed depth 20; anything beyond strongly suggests a cycle.
_MAX_WALK_DEPTH = 64


def walk_repo(
    root: Path | str,
    *,
    prune_dirs: frozenset[str] = PRUNED_DIRS,
    prune_nested_git: bool = True,
    max_depth: int = _MAX_WALK_DEPTH,
) -> Iterator[tuple[Path, list[str], list[str]]]:
    """``os.walk`` over *root* with pruning. Yields ``(dirpath, dirnames, filenames)``.

    Use this (instead of :func:`iter_glob`) when one pass should collect
    several kinds of files — match against ``filenames`` yourself.

    Args:
        root:             Directory to walk.
        prune_dirs:       Directory *basenames* skipped at every level.
        prune_nested_git: Skip any non-root directory that contains its own
                          ``.git`` entry (dir or file) — i.e. another repo.
        max_depth:        Hard depth cap (cycle safety net).

    ``dirnames`` is yielded *after* pruning; callers may prune further by
    mutating it in place, exactly like ``os.walk``.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return

    try:
        root_real = os.path.realpath(root_path)
    except OSError:
        return
    base_depth = root_real.rstrip(os.sep).count(os.sep)
    visited_real: set[str] = {root_real}

    is_root = True
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        # Nested git repo? ``.git`` shows up in this dir's own listing — no
        # extra stat calls needed. The walk root itself is exempt (it IS the
        # repo being scanned); top-down os.walk always yields it first.
        if is_root:
            is_root = False
        elif prune_nested_git and (".git" in dirnames or ".git" in filenames):
            dirnames[:] = []
            continue

        # Skip junk directories at every level.
        dirnames[:] = [d for d in dirnames if d not in prune_dirs]

        # Drop any dir whose realpath we've already entered — catches
        # Windows junctions and other cycles ``os.walk`` can't detect via
        # inode (Windows doesn't expose real inodes outside NTFS proper).
        cycle_pruned: list[str] = []
        for d in dirnames:
            child = os.path.join(dirpath, d)
            try:
                child_real = os.path.realpath(child)
            except OSError:
                cycle_pruned.append(d)
                continue
            if child_real in visited_real:
                cycle_pruned.append(d)
                continue
            visited_real.add(child_real)
        if cycle_pruned:
            dirnames[:] = [d for d in dirnames if d not in cycle_pruned]

        # Final safety net: refuse to descend beyond max_depth.
        cur_depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if cur_depth >= max_depth:
            log.warning(
                "fs_walk.depth_exceeded",
                root=str(root_path),
                depth=cur_depth,
                at=dirpath,
            )
            dirnames[:] = []
            continue

        yield Path(dirpath), dirnames, filenames


def _matches(rel_posix: str, basename: str, pattern: str) -> bool:
    """``rglob`` matching semantics for one already-walked entry.

    Patterns without ``/`` match the basename (``"*.go"``, ``"go.mod"``).
    Patterns with ``/`` match the *tail* of the root-relative posix path,
    mirroring ``Path.rglob`` == ``glob("**/" + pattern)``:
    ``"META-INF/services"``, ``"META-INF/spring/*.imports"``.
    """
    if "/" not in pattern:
        return fnmatch.fnmatch(basename, pattern)
    return fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(rel_posix, "*/" + pattern)


class WalkSnapshot:
    """One pruned walk, replayed for many :func:`iter_glob`-style queries.

    Callers that issue several ``iter_glob`` queries against the same tree
    (the dynamic-hint extractors run ~40 of them) pay one filesystem walk
    here and answer every query from memory. Replay preserves
    :func:`iter_glob` semantics and yield order exactly: entries are stored
    in walk order, files before directories per directory, and ``/``-tail
    patterns match against paths relative to the *query* root.

    Queries rooted below the snapshot root are served from the snapshot's
    subtree (os.walk pre-order keeps a subtree's entries in the same
    relative order a direct walk of that subtree would produce). A query
    root outside the snapshot tree falls back to a live walk.
    """

    __slots__ = ("_entries", "_prune_dirs", "_prune_nested_git", "root")

    def __init__(
        self,
        root: Path | str,
        *,
        prune_dirs: frozenset[str] = PRUNED_DIRS,
        prune_nested_git: bool = True,
    ) -> None:
        self.root = Path(root)
        self._prune_dirs = prune_dirs
        self._prune_nested_git = prune_nested_git
        # (dirpath, root-relative posix dir ('' for the root), dirnames, filenames)
        self._entries: list[tuple[Path, str, list[str], list[str]]] = []
        for dirpath, dirnames, filenames in walk_repo(
            self.root, prune_dirs=prune_dirs, prune_nested_git=prune_nested_git
        ):
            rel = dirpath.relative_to(self.root).as_posix()
            self._entries.append(
                (dirpath, "" if rel == "." else rel, list(dirnames), list(filenames))
            )

    def iter_glob(self, root: Path | str, patterns: str | Iterable[str]) -> Iterator[Path]:
        """Replay of ``iter_glob(root, patterns)`` against the snapshot."""
        query_root = Path(root)
        pats = (patterns,) if isinstance(patterns, str) else tuple(patterns)

        sub_rel: str | None
        if query_root == self.root:
            sub_rel = None
        else:
            try:
                sub_rel = query_root.relative_to(self.root).as_posix()
            except ValueError:
                # Outside the snapshot tree: serve live with the same pruning.
                yield from iter_glob(
                    query_root,
                    pats,
                    prune_dirs=self._prune_dirs,
                    prune_nested_git=self._prune_nested_git,
                )
                return

        for dirpath, rel_dir, dirnames, filenames in self._entries:
            if sub_rel is None:
                q_rel = rel_dir
            elif rel_dir == sub_rel:
                q_rel = ""
            elif rel_dir.startswith(sub_rel + "/"):
                q_rel = rel_dir[len(sub_rel) + 1 :]
            else:
                continue
            prefix = "" if q_rel == "" else q_rel + "/"
            for name in filenames:
                if any(_matches(prefix + name, name, p) for p in pats):
                    yield dirpath / name
            for name in dirnames:
                if any(_matches(prefix + name, name, p) for p in pats):
                    yield dirpath / name


def iter_glob(
    root: Path | str,
    patterns: str | Iterable[str],
    *,
    prune_dirs: frozenset[str] = PRUNED_DIRS,
    prune_nested_git: bool = True,
) -> Iterator[Path]:
    """Pruned drop-in for ``root.rglob(pattern)``.

    Mirrors ``rglob`` semantics — files *and* directories whose name matches
    are yielded — but skips junk directories, nested git repos, symlinks,
    and cycles (see :func:`walk_repo`).

    Accepts one pattern or several (one walk, any-match). Patterns may
    contain ``/`` to match a relative-path tail (``"META-INF/services"``).
    """
    root_path = Path(root)
    pats = (patterns,) if isinstance(patterns, str) else tuple(patterns)

    for dirpath, dirnames, filenames in walk_repo(
        root_path, prune_dirs=prune_dirs, prune_nested_git=prune_nested_git
    ):
        rel_dir = dirpath.relative_to(root_path).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        for name in filenames:
            if any(_matches(prefix + name, name, p) for p in pats):
                yield dirpath / name
        for name in dirnames:
            if any(_matches(prefix + name, name, p) for p in pats):
                yield dirpath / name
