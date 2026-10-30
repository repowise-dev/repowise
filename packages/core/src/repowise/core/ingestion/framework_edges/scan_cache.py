"""Per-file framework scan results, cached across runs.

Some framework handlers read every test or code file's text (an ``ast`` walk
for path strings, a tokenize pass for fixture requests, a word search for
Alembic), and an update re-ran them for the whole repository when one file
changed. Each of those scans is a pure function of one file's path and bytes,
so its result is cached under ``(scan, path, content hash)``, the parse
cache's key. Anything that depends on the rest of the repository (the path
set, the graph) is applied after the lookup and never cached.

The fingerprint covers the scanner modules' source, the parser schema version
and the Python version (``ast`` and ``tokenize`` output differs between
versions). Ceiling: a behavior change in a helper outside those modules needs
a ``PARSER_SCHEMA_VERSION`` bump. Best-effort like the parse cache: any load
or save error means a cold scan, and only entries used this run are kept.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

_CACHE_FILENAME = "framework_scan_cache.pkl"
_INGESTION_DIR = Path(__file__).resolve().parent.parent
# Every module whose code decides a cached scan result.
_SCANNER_SOURCES = (
    _INGESTION_DIR / "framework_edges" / "test_path_strings.py",
    _INGESTION_DIR / "framework_edges" / "pytest_edges.py",
    _INGESTION_DIR / "framework_edges" / "alembic_runner.py",
    _INGESTION_DIR / "languages" / "python_strings.py",
    _INGESTION_DIR / "source_text.py",
    _INGESTION_DIR.parent / "pyparse.py",
)

T = TypeVar("T")


@lru_cache(maxsize=1)
def scan_fingerprint() -> str | None:
    """Digest of what decides a scan besides the file bytes; ``None`` disables the cache."""
    try:
        from repowise.core.upgrade.version import PARSER_SCHEMA_VERSION

        h = hashlib.sha256(f"{sys.version}|parser-schema:{PARSER_SCHEMA_VERSION}".encode())
        for source in _SCANNER_SOURCES:
            h.update(source.name.encode())
            h.update(source.read_bytes())
    except Exception:
        return None
    return h.hexdigest()


class ScanCache:
    """``(scan, path, content hash) -> result`` store, persisted when given a directory."""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self._fingerprint = scan_fingerprint() if cache_dir is not None else None
        self._path = (
            Path(cache_dir) / _CACHE_FILENAME if self._fingerprint is not None else None
        )
        self._entries: dict[tuple[str, str, str], Any] = {}
        self._fresh: dict[tuple[str, str, str], Any] = {}
        if self._path is not None:
            self._load()

    def _load(self) -> None:
        try:
            # HMAC-verified before unpickling: the file lives inside the indexed repo.
            payload = load_sealed_pickle(self._path, domain=_CACHE_FILENAME)
            if payload.get("fingerprint") == self._fingerprint:
                self._entries = payload.get("entries", {})
        except FileNotFoundError:
            return
        except Exception as exc:
            log.debug("framework_scan_cache_load_failed", error=str(exc))

    def lookup(self, scan: str, parsed: Any, compute: Callable[[], T]) -> T:
        """*compute*'s result for *parsed*'s bytes, from the cache when they are unchanged.

        Callers must not mutate the result: a hit is shared, not copied.
        """
        content_hash = getattr(parsed, "content_hash", "")
        if not content_hash:
            return compute()
        key = (scan, parsed.file_info.path, content_hash)
        if key in self._fresh:
            return self._fresh[key]
        value = self._entries[key] if key in self._entries else compute()
        self._fresh[key] = value
        return value

    def save(self) -> None:
        """Persist the entries used this run; a cache without a directory keeps nothing."""
        if self._path is None:
            return
        try:
            payload = {"fingerprint": self._fingerprint, "entries": self._fresh}
            dump_sealed_pickle(self._path, payload, domain=_CACHE_FILENAME)
        except Exception as exc:
            log.debug("framework_scan_cache_save_failed", error=str(exc))


def scans_of(ctx: Any) -> ScanCache:
    """The run's scan cache on *ctx*, or a throwaway one for a caller that set none."""
    scans = getattr(ctx, "framework_scans", None)
    return scans if scans is not None else ScanCache()
