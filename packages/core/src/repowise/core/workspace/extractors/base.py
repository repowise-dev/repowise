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
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


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
        try:
            content = Path(info.abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield info.path, suffix, content
