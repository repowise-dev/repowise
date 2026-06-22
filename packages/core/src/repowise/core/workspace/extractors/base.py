"""Shared file-scanning primitives for contract extractors.

Both the HTTP and gRPC extractors walk a repository the same way and read each
candidate file as UTF-8. That traversal lives here once so the per-contract-type
orchestrators only declare *which* extensions they care about. Discovery is
delegated to the ingestion :class:`~repowise.core.ingestion.traverser.FileTraverser`
so the scanned file set respects ``.gitignore`` and nested-repo boundaries
(identical to the repo's actual index) instead of a raw ``os.walk`` that would
descend into sibling/vendored repos rooted under the workspace.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

# Path segments that mark a test/mock tree. A route handler or topic publisher
# that exists only under one of these is a fixture, not a real service contract,
# so it is excluded from contract extraction by default (configurable via the
# workspace ``contracts.exclude_globs``). Kept to unambiguous test-only dir names:
# singular ``test``/``spec``/``e2e`` are intentionally excluded because they
# double as legitimate product directories (an OpenAPI ``spec/``, a ``test/``
# feature). Test *files* under those dirs are still caught by the filename
# patterns below.
_TEST_DIR_SEGMENTS = frozenset({"tests", "__tests__", "__mocks__"})

# Filename patterns that mark a test file regardless of directory.
_TEST_FILE_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "*_test.go",
    "*.test.*",
    "*.spec.*",
    "*.e2e.*",
    "conftest.py",
)


def is_test_path(rel_path: str) -> bool:
    """True when *rel_path* (POSIX) lives in a test tree or is a test file."""
    parts = rel_path.split("/")
    if any(seg in _TEST_DIR_SEGMENTS for seg in parts[:-1]):
        return True
    name = parts[-1]
    return any(fnmatch(name, pat) for pat in _TEST_FILE_PATTERNS)


def make_exclude_predicate(
    extra_globs: tuple[str, ...] = (),
    *,
    exclude_tests: bool = True,
) -> Callable[[str], bool]:
    """Build a ``rel_path -> bool`` skip predicate for contract extraction.

    Skips the default test/spec trees (unless *exclude_tests* is False) plus any
    user-supplied ``extra_globs`` (matched against the full POSIX path and the
    bare filename).
    """

    def skip(rel_path: str) -> bool:
        if exclude_tests and is_test_path(rel_path):
            return True
        name = rel_path.rsplit("/", 1)[-1]
        return any(fnmatch(rel_path, g) or fnmatch(name, g) for g in extra_globs)

    return skip


@dataclass(frozen=True)
class ScanContext:
    """A single source file presented to a dialect.

    ``rel_path`` is POSIX-relative to the repo root; ``suffix`` is the
    lower-cased file extension (including the dot); ``content`` is the decoded
    file text. ``mounts`` is the repo-wide ``router-variable -> mount-prefix``
    map a provider dialect uses to recover cross-file route prefixes (see
    :mod:`.http.mounts`); empty for single-file extraction.
    """

    repo_alias: str
    rel_path: str
    suffix: str
    content: str
    mounts: Mapping[str, str] = field(default_factory=dict)


def iter_source_files(
    repo_root: Path,
    extensions: frozenset[str],
    exclude: Callable[[str], bool] | None = None,
) -> Iterator[tuple[str, str, str]]:
    """Yield ``(rel_path, suffix, content)`` for each scannable source file.

    File discovery is delegated to the ingestion :class:`FileTraverser`, the
    same gitignore- and nested-repo-aware walker the main index uses, rather
    than a raw ``os.walk``. This matters whenever a workspace repo's root
    physically contains *other* git repositories (sibling/vendored repos,
    benchmark clones, build worktrees) or large gitignored trees: a naive walk
    descends into all of them and scans hundreds of thousands of foreign files.
    That is not hypothetical: on a developer checkout whose root held nested
    repos, the old walk yielded **1.05M files** for a repo whose real source is
    ~2k, wedging contract extraction for tens of minutes. FileTraverser prunes
    nested git repos and honours ``.gitignore``, so the scanned set matches the
    repo's actual index.

    Only files whose lower-cased suffix is in *extensions* are yielded.
    Oversized, binary, generated, and ignored files are already excluded by the
    traverser. Unreadable files are silently skipped.
    """
    from repowise.core.ingestion.traverser import FileTraverser

    root = Path(repo_root).resolve()
    if not root.is_dir():
        return

    traverser = FileTraverser(root)
    for info in traverser.traverse():
        suffix = os.path.splitext(info.path)[1].lower()
        if suffix not in extensions:
            continue
        if exclude is not None and exclude(info.path):
            continue
        try:
            content = Path(info.abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield info.path, suffix, content
