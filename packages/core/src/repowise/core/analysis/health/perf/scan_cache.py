"""Content-keyed, disposable caches for the file-local performance scans.

Ingestion already hashes each parsed file, so a hit need not read its source.
Only the local facts are cached: cross-file model merging and performance
ranking still run every time. Entries are copied on access because merging
adds reverse relations to the raw model classes.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle
from repowise.core.ingestion.models import compute_content_hash
from repowise.core.ingestion.parser import grammar_tag_for

from ..source_fingerprint import grammar_choices, grammar_failed, source_fingerprint
from ..source_reader import SourceReader
from ..walk_cache import WALK_INPUTS

log = structlog.get_logger(__name__)
_T = TypeVar("_T")
_CACHE_VERSION = 1
SCAN_INPUTS = (
    *WALK_INPUTS,
    "analysis/health/perf/unbounded_reduction.py",
    "analysis/health/perf/reduction_idioms.py",
    "analysis/health/perf/orm_models.py",
)


def scan_fingerprint() -> str:
    """Include the scanners, their parsing helpers and installed grammars."""
    return source_fingerprint(SCAN_INPUTS, grammar_choices())


class PerfScanCache:
    """Sealed on-disk cache for one kind of file scan, with fresh values per hit."""

    def __init__(self, cache_dir: Path | str, kind: str) -> None:
        self._filename = f"health_{kind}_cache.pkl"
        self._path = Path(cache_dir) / self._filename
        self._entries: dict[str, bytes] = {}
        self._fresh: dict[str, bytes] = {}

    def load(self) -> None:
        """A missing, stale or unreadable cache simply makes the scans run."""
        self._entries.clear()
        self._fresh.clear()
        try:
            payload = load_sealed_pickle(self._path, domain=self._filename)
            if payload.get("version") == _CACHE_VERSION and payload.get("scanner") == scan_fingerprint():
                entries = payload.get("files")
                if isinstance(entries, dict):
                    self._entries = entries
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.debug("perf_scan_cache_load_failed", error=str(exc))

    def get(self, key: str) -> Any | None:
        """Return a copy so a caller cannot change the next run's input."""
        blob = self._fresh.get(key) or self._entries.get(key)
        if blob is None:
            return None
        try:
            value = pickle.loads(blob)  # repowise-security-ignore: pickle_loads
        except Exception:
            return None
        self._fresh[key] = blob
        return value

    def put(self, key: str, value: Any) -> None:
        """Keep successful scans only; a missing grammar must be retried."""
        if grammar_failed(key.partition(":")[0]):
            return
        try:
            self._fresh[key] = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            log.debug("perf_scan_cache_put_failed", error=str(exc))

    def save(self, *, keep_unused: bool = False) -> None:
        """Keep skipped files on a partial pass; age them out on a full pass."""
        files = {**self._entries, **self._fresh} if keep_unused else self._fresh
        try:
            dump_sealed_pickle(
                self._path,
                {"version": _CACHE_VERSION, "scanner": scan_fingerprint(), "files": files},
                domain=self._filename,
            )
        except Exception as exc:
            log.debug("perf_scan_cache_save_failed", error=str(exc))


def scan_cached(
    pf: Any,
    read_source: SourceReader,
    scan: Callable[[bytes], _T | None],
    cache: PerfScanCache | None,
) -> _T | None:
    """Scan on a miss, using ingestion's content hash when it is available.

    Callers without a hash still work: read their bytes and key on those.
    Unreadable sources and failed scans (``None``) never populate the cache.
    """
    info = pf.file_info
    digest = getattr(pf, "content_hash", "")
    source = None
    key = None
    if cache is not None:
        if not digest:
            source = read_source(info.abs_path)
            if source is None:
                return None
            digest = compute_content_hash(source)
        key = f"{grammar_tag_for(info.language, info.abs_path)}:{digest}"
        cached = cache.get(key)
        if cached is not None:
            return cached
    if source is None:
        source = read_source(info.abs_path)
    if source is None:
        return None
    result = scan(source)
    if cache is not None and key is not None and result is not None:
        cache.put(key, result)
    return result
