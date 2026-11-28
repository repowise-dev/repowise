"""Native Rabin-Karp duplication detection over tree-sitter tokens."""

import contextlib
from pathlib import Path

from .detector import (
    DEFAULT_MIN_LINES,
    DEFAULT_WINDOW_TOKENS,
    ClonePair,
    DuplicationReport,
    detect_clones,
)
from .limits import DuplicationDiagnostics, DuplicationLimits, looks_minified
from .pair_index import _INDEX_FILENAME
from .token_cache import _CACHE_FILENAME, _DELTA_FILENAME
from .tokenizer import Token, tokenize_file, tokenize_tree

#: What the duplication pass keeps under ``.repowise`` between runs.
CLONE_CACHE_FILES: tuple[str, ...] = (_CACHE_FILENAME, _DELTA_FILENAME, _INDEX_FILENAME)


def drop_clone_caches(cache_dir: Path) -> None:
    """Delete the duplication pass's cache files in *cache_dir*, if any.

    Best-effort: a file another process holds open stays until the next pass.
    """
    for name in CLONE_CACHE_FILES:
        with contextlib.suppress(OSError):
            (cache_dir / name).unlink(missing_ok=True)


__all__ = [
    "CLONE_CACHE_FILES",
    "DEFAULT_MIN_LINES",
    "DEFAULT_WINDOW_TOKENS",
    "ClonePair",
    "DuplicationDiagnostics",
    "DuplicationLimits",
    "DuplicationReport",
    "Token",
    "detect_clones",
    "drop_clone_caches",
    "looks_minified",
    "tokenize_file",
    "tokenize_tree",
]
