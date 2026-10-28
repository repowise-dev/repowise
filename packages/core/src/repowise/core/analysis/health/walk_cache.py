"""Content-keyed cache of the complexity walk, kept beside the index.

The walk over a file is a pure function of its bytes, its grammar and the
walker's code (and, where a repository configures them, its assertion
vocabulary and SQL dialect -- see :meth:`HealthWalkCache.key`), and on an
update most of the files the health pass walks did not change: the performance closure re-walks every file whose call path
reaches a changed sink, so the walk was two fifths of a one-file update.
This keeps each file's :class:`FileComplexity` under its content hash the way
the parse cache keeps parse trees, so an unchanged file's walk is a lookup.

Entries are stored as pickled bytes and unpickled on every hit. The pass
mutates the walk result it is handed (cross-function facts, promotions), and
a shared live object would carry one run's annotations into the next; a
fresh copy per hit cannot.

The walker's code is :data:`WALK_INPUTS`, fingerprinted rather than versioned
by hand, so a scoring change or an analyzer-stamp bump keeps every walk.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

from .source_fingerprint import PARSE_INPUTS, grammar_choices, grammar_failed, source_fingerprint

log = structlog.get_logger(__name__)

_CACHE_FILENAME = "health_walk_cache.pkl"
#: Bump when the payload's shape changes; walker changes key on the fingerprint.
_CACHE_VERSION = 2

#: Every module whose code runs inside ``walk_file`` or ``walk_sql_file``,
#: relative to ``repowise/core``.
WALK_INPUTS: tuple[str, ...] = (
    "analysis/health/complexity/*.py",
    "analysis/health/dataflow/**/*.py",
    "analysis/health/perf/dialects/*.py",
    "analysis/health/perf/io_boundaries.py",
    "analysis/health/perf/loop_facts.py",
    "analysis/health/perf/loop_lexicon.py",
    "analysis/health/perf/table_scale.py",
    "analysis/health/asserts/lexicon.py",
    "analysis/health/mocks/lexicon.py",
    "analysis/health/apex_markers.py",
    "analysis/health/sql_complexity.py",
    "ingestion/extractors/cobol.py",
    "ingestion/extractors/helpers.py",
    "ingestion/python_overload.py",
    "ingestion/cpp_export_macros.py",
    "ingestion/cpp_macro_facts.py",
    "ingestion/sql_parse.py",
    "ingestion/external_systems/io_kind.py",
    *PARSE_INPUTS,
)


def walker_fingerprint() -> str:
    """The stamp a cached walk must carry to be served."""
    return source_fingerprint(WALK_INPUTS, grammar_choices())


class HealthWalkCache:
    """``(grammar, content hash) -> pickled FileComplexity`` for one repository.

    ``load`` reads the previous run's entries; ``get`` and ``put`` serve the
    walk; ``save`` writes back the entries this run used or created, so a file
    that left the repository ages out with it.
    """

    def __init__(self, cache_dir: Path | str) -> None:
        self._path = Path(cache_dir) / _CACHE_FILENAME
        self._entries: dict[str, bytes] = {}
        self._fresh: dict[str, bytes] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(grammar: str, content_hash: str, vocabulary: str = "", sql_dialect: str = "") -> str:
        """The cache key, optionally qualified by a configured vocabulary or dialect.

        *grammar* is the tree-sitter grammar the walk used, which for ``.tsx``
        is not the file's language tag. Keying on the language instead would
        serve a ``.tsx`` walk for a byte-identical ``.ts`` file.

        A repository that configures an assertion vocabulary breaks the "bytes
        plus grammar plus walker code" purity this cache rests on, so the
        vocabulary joins the key and an edit to it simply misses and ages out.
        Empty is the overwhelmingly common case and keys exactly as before, so
        no existing cache is invalidated by this parameter existing. A ``.sql``
        file's configured dialect joins the key the same way.
        """
        if sql_dialect:
            return f"{grammar}:{content_hash}:{vocabulary}:{sql_dialect}"
        if not vocabulary:
            return f"{grammar}:{content_hash}"
        return f"{grammar}:{content_hash}:{vocabulary}"

    def load(self) -> None:
        try:
            payload = load_sealed_pickle(self._path, domain=_CACHE_FILENAME)
            if (
                payload.get("version") != _CACHE_VERSION
                or payload.get("walker") != walker_fingerprint()
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
            # Our own dumps, or an entry of the file load_sealed_pickle verified.
            value = pickle.loads(blob)  # repowise-security-ignore: pickle_loads
        except Exception:
            self.misses += 1
            return None
        self.hits += 1
        self._fresh[key] = blob
        return value

    def put(self, key: str, value: Any) -> None:
        # A walk whose grammar did not load is empty for want of the grammar,
        # not because of the file; the key's first term is that grammar.
        if grammar_failed(key.partition(":")[0]):
            return
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
                    "walker": walker_fingerprint(),
                    "files": self._fresh,
                },
                domain=_CACHE_FILENAME,
            )
        except Exception as exc:
            log.debug("health_walk_cache_save_failed", error=str(exc))
