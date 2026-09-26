"""Which repository files decision mining reads, and how it reads them."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from repowise.core.fs_walk import PRUNED_DIRS, walk_repo

# fs_walk's never-source set plus the two derived-output names the decision
# walks have always skipped: a bundled ``dist``/``build`` copy of a source file
# would double every marker it contains.
_SKIP_DIRS = PRUNED_DIRS | {"dist", "build"}

_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".bmp",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".rar",
        ".7z",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".dylib",
        ".exe",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".lance",
        ".lock",
    }
)


def tracked_files(repo_path: Path) -> set[Path] | None:
    """Resolved paths git tracks under ``repo_path``, or ``None``.

    ``None`` means "no git scoping available" (not a git repo, git missing,
    or the command failed) — callers then fall back to walking the tree.
    Restricting to tracked files keeps untracked / gitignored / git-excluded
    working directories (``local-stash/``, vendored dumps, scratch folders)
    out of the harvest: their comments are not part of the indexed codebase
    and must not become decision records.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "ls-files", "-z"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    out = proc.stdout.decode("utf-8", errors="replace")
    tracked: set[Path] = set()
    for rel in out.split("\0"):
        if not rel:
            continue
        try:
            tracked.add((repo_path / rel).resolve())
        except OSError:
            continue
    return tracked or None


def iter_source_files(repo_path: Path) -> Iterator[Path]:
    """Yield source files under repo_path, skipping irrelevant dirs.

    Walks via :func:`walk_repo`, which prunes junk subtrees and nested git
    repos (separate codebases that must not contribute decisions to the
    parent) without descending into them. When the repo is a git checkout,
    the walk is further restricted to git-tracked files so untracked /
    excluded working directories never contribute decisions; gitless
    indexes fall back to the full walk.
    """
    tracked = tracked_files(repo_path)

    for dirpath, dirnames, filenames in walk_repo(repo_path, prune_dirs=_SKIP_DIRS):
        # Skip setuptools build metadata: PKG-INFO embeds the README
        # verbatim, so example marker lines in docs become spurious
        # decisions. Same risk for *.dist-info from wheels.
        dirnames[:] = [d for d in dirnames if not d.endswith((".egg-info", ".dist-info"))]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() in _BINARY_EXTENSIONS:
                continue
            if tracked is not None and not _is_tracked(fpath, tracked):
                continue
            yield fpath


def _is_tracked(fpath: Path, tracked: set[Path]) -> bool:
    try:
        return fpath.resolve() in tracked
    except OSError:
        return False


def extract_leading_prose(repo_path: Path, file_path: str) -> str:
    """Return the leading module docstring / header comment block of a file."""
    p = repo_path / file_path
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return ""
    prose: list[str] = []
    in_doc = False
    for line in text.splitlines()[:120]:
        s = line.strip()
        if not in_doc and (s.startswith('"""') or s.startswith("'''")):
            quote = s[:3]
            inner = s.strip("\"' ")
            if inner:
                prose.append(inner)
            # Single-line docstring closes on the same line.
            if s.count(quote) < 2:
                in_doc = True
            continue
        if in_doc:
            if s.endswith('"""') or s.endswith("'''"):
                in_doc = False
                inner = s.strip("\"' ")
                if inner:
                    prose.append(inner)
            else:
                prose.append(s)
            continue
        if s.startswith(("#", "//", "*", "/*", "--")):
            cleaned = re.sub(r"^\s*(?:#|//|--|/\*|\*/|\*)\s?", "", s)
            if cleaned:
                prose.append(cleaned)
    return "\n".join(prose).strip()[:2000]
