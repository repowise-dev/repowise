"""Content-keyed cache of the complexity walk, kept beside the index.

The walk over a file is a pure function of its bytes, its language and the
walker's version, and on an update most of the files the health pass walks
did not change: the performance closure re-walks every file whose call path
reaches a changed sink, so the walk was two fifths of a one-file update.
This keeps each file's :class:`FileComplexity` under its content hash the way
the parse cache keeps parse trees, so an unchanged file's walk is a lookup.

Entries are stored as pickled bytes and unpickled on every hit. The pass
mutates the walk result it is handed (cross-function facts, promotions), and
a shared live object would carry one run's annotations into the next; a
fresh copy per hit cannot.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

_CACHE_FILENAME = "health_walk_cache.pkl"
#: Bump when the walker's output for the same bytes changes.
_CACHE_VERSION = 1


class HealthWalkCache:
    """``(language, content hash) -> pickled FileComplexity`` for one repository.

    ``load`` reads the previous run's entries; ``get`` and ``put`` serve the
    walk; ``save`` writes back the entries this run used or created, so a file
    that left the repository ages out with it.
    """

    def __init__(self, cache_dir: Path | str, analyzer_version: int) -> None:
        self._path = Path(cache_dir) / _CACHE_FILENAME
        self._analyzer_version = analyzer_version
        self._entries: dict[str, bytes] = {}
        self._fresh: dict[str, bytes] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(language: str, content_hash: str) -> str:
        return f"{language}:{content_hash}"

    def load(self) -> None:
        try:
            payload = load_sealed_pickle(self._path, domain=_CACHE_FILENAME)
            if (
                payload.get("version") != _CACHE_VERSION
                or payload.get("analyzer_version") != self._analyzer_version
            ):
                return
            entries = payload.get("files")
            if isinstance(entries, dict):
                self._entries = entries
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupt, unsigned or unreadable: walk everything
            log.debug("health_walk_cache_load_failed", error=str(exc))

    def get(self, key: str) -> Any | None:
        blob = self._fresh.get(key) or self._entries.get(key)
        if blob is None:
            self.misses += 1
            return None
        try:
            value = pickle.loads(blob)
        except Exception:
            self.misses += 1
            return None
        self.hits += 1
        self._fresh[key] = blob
        return value

    def put(self, key: str, value: Any) -> None:
        try:
            self._fresh[key] = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:  # an unpicklable walk result is simply not cached
            log.debug("health_walk_cache_put_failed", error=str(exc))

    def save(self) -> None:
        """Atomically persist the entries used or created this run."""
        if not self._fresh:
            return
        try:
            dump_sealed_pickle(
                self._path,
                {
                    "version": _CACHE_VERSION,
                    "analyzer_version": self._analyzer_version,
                    "files": self._fresh,
                },
                domain=_CACHE_FILENAME,
            )
        except Exception as exc:
            log.debug("health_walk_cache_save_failed", error=str(exc))
