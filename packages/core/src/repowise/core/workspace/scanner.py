"""Workspace repo scanner — discover git repositories in a directory tree.

Pure filesystem module with no CLI, DB, or config dependencies.
Used by ``repowise init`` to detect multi-repo workspaces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredRepo:
    """A git repository found during workspace scanning."""

    path: Path  # Absolute, resolved path to repo root
    name: str  # Directory basename (e.g. "backend")
    alias: str  # Unique short name for workspace config
    has_repowise: bool  # True if .repowise/ already exists
    is_submodule: bool  # True if .git is a file (not a directory)


@dataclass
class ScanResult:
    """Result of scanning a directory for git repositories."""

    repos: list[DiscoveredRepo]
    root: Path  # The directory that was scanned (absolute)
    skipped_dirs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directories to never descend into during workspace scanning.
# Distinct from the ingestion traverser's blocklist — this is for
# top-level *repo* discovery, not file-level traversal within a repo.
_SCAN_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".cache",
        ".tox",
        "dist",
        "build",
        ".next",
        "target",
        ".gradle",
        "vendor",
        "coverage",
        "htmlcov",
        "site-packages",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

_MAX_SCAN_DEPTH: int = 3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_for_repos(
    root: Path,
    *,
    max_depth: int = _MAX_SCAN_DEPTH,
    include_submodules: bool = False,
) -> ScanResult:
    """Scan *root* for git repositories up to *max_depth* levels deep.

    Returns a :class:`ScanResult` with discovered repos sorted alphabetically
    by their path relative to *root*.

    - If *root* itself is a git repo, returns a single-element result.
    - Stops descending once a ``.git`` boundary is found (no nested repos).
    - Skips common junk directories (``node_modules``, ``.venv``, etc.).
    - Submodules (``.git`` is a file) are excluded unless *include_submodules* is True.
    """
    root = Path(root).resolve()

    # Check if root itself is a git repo
    root_is_repo = _is_git_repo(root)
    root_is_sub = _is_submodule(root) if root_is_repo else False

    # Walk the tree for sub-repos even if root is a git repo — a workspace
    # can be a git repo that contains other git repos (e.g. non-submodule
    # nested repos like backend/ and frontend/).
    found: list[tuple[Path, bool]] = []  # (abs_path, is_submodule)
    skipped: list[str] = []

    for dirpath_str, dirnames, _filenames in os.walk(root):
        dirpath = Path(dirpath_str)
        depth = len(dirpath.relative_to(root).parts)

        if depth >= max_depth:
            dirnames.clear()
            continue

        # Prune junk and hidden directories (in-place, so os.walk skips them)
        _prune_dirs(dirnames, dirpath, skipped)

        # Check remaining children for .git boundaries
        repos_at_this_level: list[str] = []
        for dirname in list(dirnames):
            child = dirpath / dirname
            if _is_git_repo(child):
                is_sub = _is_submodule(child)
                if not is_sub or include_submodules:
                    try:
                        resolved = child.resolve()
                        resolved.relative_to(root)
                        found.append((resolved, is_sub))
                    except (OSError, ValueError):
                        continue  # broken symlink or resolved outside root
                repos_at_this_level.append(dirname)

        # Don't descend into discovered repos
        for dirname in repos_at_this_level:
            dirnames.remove(dirname)

    # If root is a git repo and no sub-repos found → single-repo result
    # If root is a git repo AND sub-repos found → include root in the list
    if root_is_repo:
        if not found:
            # Pure single-repo — no sub-repos
            if root_is_sub and not include_submodules:
                return ScanResult(repos=[], root=root)
            aliases = _generate_aliases([(root, root)])
            repo = _make_repo(root, root, aliases[root], root_is_sub)
            return ScanResult(repos=[repo], root=root)
        # Root + sub-repos → workspace mode; add root to the list
        if not root_is_sub or include_submodules:
            found.insert(0, (root, root_is_sub))

    # Sort by relative path for deterministic ordering
    found.sort(key=lambda item: item[0].relative_to(root).as_posix())

    aliases = _generate_aliases([(p, root) for p, _ in found])
    repos = [_make_repo(p, root, aliases[p], is_sub) for p, is_sub in found]
    return ScanResult(repos=repos, root=root, skipped_dirs=skipped)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_git_repo(path: Path) -> bool:
    """Check if *path* contains a ``.git`` directory or file."""
    return (path / ".git").exists()


def _is_submodule(path: Path) -> bool:
    """A submodule has ``.git`` as a file pointing to the parent's modules dir."""
    return (path / ".git").is_file()


def _prune_dirs(
    dirnames: list[str],
    parent: Path,
    skipped: list[str],
) -> None:
    """Remove junk and hidden directories from *dirnames* in-place."""
    to_remove: list[str] = []
    for d in dirnames:
        if d == ".git":
            # Never descend into .git itself, but don't log as skipped
            to_remove.append(d)
        elif d in _SCAN_SKIP_DIRS:
            skipped.append(str(parent / d))
            to_remove.append(d)
        elif d.startswith(".") and d != ".repowise":
            # Skip hidden dirs (except .repowise which we check for has_repowise)
            to_remove.append(d)
    for d in to_remove:
        dirnames.remove(d)


def _generate_aliases(repo_root_pairs: list[tuple[Path, Path]]) -> dict[Path, str]:
    """Generate unique aliases from directory basenames.

    On collision, appends ``-2``, ``-3``, etc.
    *repo_root_pairs* is a list of ``(abs_repo_path, workspace_root)``.
    """
    alias_map: dict[Path, str] = {}
    used_aliases: dict[str, int] = {}  # alias → count of times used

    for repo_path, _root in repo_root_pairs:
        base = repo_path.name.lower()
        if base not in used_aliases:
            used_aliases[base] = 1
            alias_map[repo_path] = base
        else:
            used_aliases[base] += 1
            alias_map[repo_path] = f"{base}-{used_aliases[base]}"

    return alias_map


def _make_repo(
    abs_path: Path,
    root: Path,
    alias: str,
    is_submodule: bool,
) -> DiscoveredRepo:
    """Construct a :class:`DiscoveredRepo` from scan data."""
    return DiscoveredRepo(
        path=abs_path,
        name=abs_path.name,
        alias=alias,
        has_repowise=(abs_path / ".repowise").is_dir(),
        is_submodule=is_submodule,
    )
