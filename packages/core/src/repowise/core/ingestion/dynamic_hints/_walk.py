"""Pruned filesystem walk for dynamic-hint extractors.

The implementation now lives in :mod:`repowise.core.fs_walk` — the single
shared pruned walk used by every repo-wide scan (resolvers, external
systems, tsconfig discovery, dynamic hints). This module re-exports it so
existing extractor imports keep working.

Behavior note vs the historical private copy: the shared walk also prunes
**nested git repositories** (a subdirectory with its own ``.git``), which
is what dynamic hints want too — settings/router files from a vendored or
sibling checkout are not this repo's hints.

Dynamic hints keep the *derived-output* prune set (``dist``/``build``/
``out``/``coverage``), matching the historical list here: hint extraction
reads source files, and derived copies of them only add noise.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from repowise.core.fs_walk import PRUNED_DIRS_DERIVED as PRUNED_DIRS
from repowise.core.fs_walk import iter_glob as _shared_iter_glob

__all__ = ["PRUNED_DIRS", "iter_glob"]


def iter_glob(root: Path, pattern: str) -> Iterator[Path]:
    """Historical dynamic-hints walk: shared iter_glob + derived-dirs pruning."""
    return _shared_iter_glob(root, pattern, prune_dirs=PRUNED_DIRS)
