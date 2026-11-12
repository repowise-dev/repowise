"""Per-file scan results of the C and C++ hint extractors, kept between runs.

Both extractors read every C/C++ file twice over with loose regexes (the
function definitions it holds, then the names it wires at runtime), which on a
large C++ repository costs about a minute on every update even when ten files
changed. What a file yields depends only on its text and its path, so the
result is kept here keyed by both, and an update rescans only the files whose
text differs. The cross-file join that turns names into edges still runs over
every file, so the edges are the ones a full scan produces.

Keyed on a fingerprint of the code a scan result depends on: changing it
discards every entry, so the cache can only save time, never change an edge.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_CACHE_NAME = "dynamic_hint_scans.json"
_CACHE_VERSION = 1


def _code_fingerprint() -> str:
    """Hash of the code a file's scan result depends on besides its text and path."""
    from ..languages.specs import cpp as cpp_spec
    from . import c, cpp

    digest = hashlib.sha256()
    for module in (c, cpp, cpp_spec):
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()


class ScanCache:
    """``extractor name -> path -> [text digest, scan result]``."""

    def __init__(self, path: Path, fingerprint: str, entries: dict[str, dict[str, list]]) -> None:
        self._path = path
        self._fingerprint = fingerprint
        self._old = entries
        # Only what this run scanned or reused is written back, so a deleted
        # or no longer scanned file leaves the cache with the next save.
        self._fresh: dict[str, dict[str, list]] = {}

    @classmethod
    def load(cls, cache_dir: Path | str | None) -> ScanCache | None:
        """The cache under *cache_dir*; ``None`` when there is no place to keep one."""
        if cache_dir is None:
            return None
        try:
            fingerprint = _code_fingerprint()
        except (OSError, TypeError):
            # Sources not on disk (e.g. a zipped install): run without a cache.
            return None
        path = Path(cache_dir) / _CACHE_NAME
        entries: dict[str, dict[str, list]] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == _CACHE_VERSION and data.get("code") == fingerprint:
                    entries = {
                        str(name): {
                            str(rel): entry
                            for rel, entry in files.items()
                            if isinstance(entry, list) and len(entry) == 2
                        }
                        for name, files in data["entries"].items()
                    }
            except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                logger.debug("dynamic_hint_scan_cache_read_failed", error=str(exc))
        return cls(path, fingerprint, entries)

    def scan(self, name: str, rel: str, text: str, scan: Callable[[str, str], Any]) -> Any:
        """``scan(text, rel)``, reused from the last run when *rel* holds the same text."""
        digest = _digest(text)
        entry = self._old.get(name, {}).get(rel)
        if entry is None or entry[0] != digest:
            entry = [digest, scan(text, rel)]
        # Each extractor runs on its own thread and writes only its own map.
        self._fresh.setdefault(name, {})[rel] = entry
        return entry[1]

    def save(self) -> None:
        try:
            from repowise.core.fsutils import atomic_write_text

            atomic_write_text(
                self._path,
                json.dumps(
                    {"version": _CACHE_VERSION, "code": self._fingerprint, "entries": self._fresh}
                ),
            )
        except (OSError, TypeError, ValueError) as exc:
            logger.debug("dynamic_hint_scan_cache_write_failed", error=str(exc))
