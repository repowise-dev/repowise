"""Each document's extracted references, cached across runs.

Extraction is a pure function of a document's path and text, and an update
re-ran it for every document in the repository when one file changed. Its
result is cached under ``(path, text hash)``. Resolution, which depends on the
rest of the tree, always runs.

The fingerprint covers the extractor modules' source and the Python version.
Ceiling: a behavior change in a helper outside those modules needs one of them
touched. Best-effort like the framework scan cache: any load or save error
means a cold extraction, and only entries used this run are kept.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

_CACHE_FILENAME = "doc_drift_extract_cache.pkl"
_HERE = Path(__file__).resolve().parent
# Every module whose code decides an extraction result.
_EXTRACTOR_SOURCES = (
    _HERE / "extractor.py",
    _HERE / "models.py",
    _HERE / "constants.py",
    _HERE / "extract_cache.py",
)

Extracted = tuple[list[Any], list[Any]]


@lru_cache(maxsize=1)
def extract_fingerprint() -> str | None:
    """Digest of what decides an extraction besides the text; ``None`` disables the cache."""
    try:
        h = hashlib.sha256(sys.version.encode())
        for source in _EXTRACTOR_SOURCES:
            h.update(source.name.encode())
            h.update(source.read_bytes())
    except Exception:
        return None
    return h.hexdigest()


class ExtractCache:
    """``(path, text hash) -> (references, suppressed)``, persisted when given a directory."""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self._fingerprint = extract_fingerprint() if cache_dir is not None else None
        self._path = Path(cache_dir) / _CACHE_FILENAME if self._fingerprint is not None else None
        self._entries: dict[tuple[str, str], Extracted] = {}
        self._fresh: dict[tuple[str, str], Extracted] = {}
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
            log.debug("doc_drift_extract_cache_load_failed", error=str(exc))

    def lookup(self, path: str, text: str, compute: Callable[[], Extracted]) -> Extracted:
        """*compute*'s result for *path* with *text*, from the cache when the text is unchanged.

        Callers must not mutate the result: a hit is shared, not copied.
        """
        if self._path is None:
            return compute()
        key = (path, hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest())
        value = self._entries.get(key)
        if value is None:
            value = compute()
        self._fresh[key] = value
        return value

    def save(self) -> None:
        """Persist the entries used this run; a cache without a directory keeps nothing."""
        if self._path is None or self._fresh == self._entries:
            return
        try:
            payload = {"fingerprint": self._fingerprint, "entries": self._fresh}
            dump_sealed_pickle(self._path, payload, domain=_CACHE_FILENAME)
        except Exception as exc:
            log.debug("doc_drift_extract_cache_save_failed", error=str(exc))
