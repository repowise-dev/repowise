"""Content-hash keyed cache for duplication token streams + windows.

Tokenizing every file (a full tree-sitter re-parse plus a pure-Python
leaf walk) and re-rolling 1M+ window hashes dominates the duplication
pass — and an incremental ``repowise update`` re-pays it for the whole
repo when a single file changed. Both outputs are pure functions of the
file *bytes* and the grammar it is read with (given fixed window size and
pinned hash constants), so they cache safely by the key the detector builds
from the two. The grammar is a second term only for a file whose extension
picks a grammar its language tag does not, which today means ``.tsx``.

Cached per file: the normalized token-kind sequence and the parallel raw
identifier names (all the verifier ever compares), the non-blank line
count, and the rolling-hash windows as plain tuples (``WindowHash`` is rebuilt with the file's *current*
path, which makes hits rename-proof). The minified/token-cap/window-
budget gates in the detector stay live — they re-evaluate against the
cached lengths, so config changes apply to cached entries too.

Best-effort by design: any load/save error degrades to a full
re-tokenize. Each run keeps only the entries it used or created, so deleted
files age out naturally. A run that kept most of the base file writes only
what differs from it (entries added, hashes dropped) to a small delta file
beside it; one whose difference outgrows :data:`_COMPACT_SHARE` of the base
rewrites the base and removes the delta. An update that touched a few files
of a large repository then writes kilobytes, not the whole cache.

Entries key on the tokenizing code (:data:`TOKEN_INPUTS`) by fingerprint, so a
scoring change or an analyzer-stamp bump keeps them.
"""

from __future__ import annotations

import contextlib
import secrets
import sys
from pathlib import Path

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

from ..source_fingerprint import PARSE_INPUTS, grammar_choices, source_fingerprint

log = structlog.get_logger(__name__)

#: Bump when the payload's shape changes; tokenizer changes key on the fingerprint.
_CACHE_VERSION = 2
_CACHE_FILENAME = "duplication_cache.pkl"
#: Entries added and hashes dropped since the base file was written.
_DELTA_FILENAME = "duplication_cache_delta.pkl"
#: Bump when the delta payload's shape changes.
_DELTA_VERSION = 1
#: The base is rewritten once the delta holds more changes than this share of
#: its entries. The delta is rewritten whole each run, so this bounds both
#: that write and how much the base can lag behind the live set.
_COMPACT_SHARE = 0.1

#: Every module whose code shapes an entry, relative to ``repowise/core``.
TOKEN_INPUTS: tuple[str, ...] = (
    "analysis/health/duplication/token_entry.py",
    "analysis/health/duplication/tokenizer.py",
    "analysis/health/duplication/rabin_karp.py",
    *PARSE_INPUTS,
)


def tokenizer_fingerprint() -> str:
    """The stamp cached entries must carry to be served.

    The import kinds the tokenizer drops come from ingestion's language
    configs, so their values join the code.
    """
    from .tokenizer import import_kinds_signature

    return source_fingerprint(TOKEN_INPUTS, f"{grammar_choices()}{import_kinds_signature()}")


def persisted_bytes(cache_dir: Path) -> int:
    """Bytes the cache holds on disk; raises ``OSError`` when there is no base."""
    size = (Path(cache_dir) / _CACHE_FILENAME).stat().st_size
    with contextlib.suppress(OSError):
        size += (Path(cache_dir) / _DELTA_FILENAME).stat().st_size
    return size


# (kinds, identifier names, nloc, window tuples)
_Entry = tuple[list[str], list[str], int, list[tuple[int, int, int, int]]]


class DuplicationTokenCache:
    """Pickle-backed ``content_hash -> (kinds, names, nloc, windows)`` store."""

    def __init__(self, cache_dir: Path, window_tokens: int) -> None:
        self._path = Path(cache_dir) / _CACHE_FILENAME
        self._delta_path = Path(cache_dir) / _DELTA_FILENAME
        # The base file this cache loaded or last wrote: its id, which a delta
        # must name to apply, and its keys, which a delta is taken against.
        self._base_id: str | None = None
        self._base_keys: frozenset[str] = frozenset()
        # (added keys, removed keys) of the delta on disk.
        self._delta_shape: tuple[frozenset[str], frozenset[str]] = (frozenset(), frozenset())
        self._window_tokens = window_tokens
        self._entries: dict[str, _Entry] = {}
        self._fresh: dict[str, _Entry] = {}
        self.hits = 0
        self.misses = 0

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        try:
            payload = load_sealed_pickle(self._path, domain=_CACHE_FILENAME)
            if (
                payload.get("version") != _CACHE_VERSION
                or payload.get("window_tokens") != self._window_tokens
                or payload.get("tokenizer") != tokenizer_fingerprint()
            ):
                return
            entries = payload.get("files", {})
            base_id = payload.get("base_id")  # absent in files written before deltas
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupt / unsigned / unreadable -> full tokenize
            log.debug("duplication_cache_load_failed", error=str(exc))
            return
        try:
            delta = load_sealed_pickle(self._delta_path, domain=_DELTA_FILENAME)
        except FileNotFoundError:
            delta = None
        except Exception as exc:
            # The newest state is unknown: start cold rather than serve a base
            # whose delta was lost. The next save rewrites the base.
            log.debug("duplication_cache_delta_load_failed", error=str(exc))
            return
        if delta is not None:
            if (
                base_id is None
                or delta.get("version") != _DELTA_VERSION
                or delta.get("base_id") != base_id
            ):
                return  # a delta from another base: start cold, as above
            added: dict[str, _Entry] = delta["added"]
            removed: set[str] = delta["removed"]
        else:
            added, removed = {}, set()
        self._base_id = base_id
        self._base_keys = frozenset(entries)
        self._delta_shape = (frozenset(added), frozenset(removed))
        for content_hash in removed:
            entries.pop(content_hash, None)
        entries.update(added)
        self._entries = entries

    def save(self) -> None:
        """Atomically persist the entries used or created this run.

        Writes only the difference from the base file when it is small,
        otherwise rewrites the base and drops the delta.
        """
        try:
            if self._base_id is not None:
                fresh = self._fresh
                base_keys = self._base_keys
                added = {k: v for k, v in fresh.items() if k not in base_keys}
                removed = {k for k in base_keys if k not in fresh}
                if len(added) + len(removed) <= _COMPACT_SHARE * len(base_keys):
                    if self._delta_shape != (frozenset(added), frozenset(removed)):
                        # Entries are pure functions of their key, so equal
                        # key sets mean the delta on disk already holds this.
                        dump_sealed_pickle(
                            self._delta_path,
                            {
                                "version": _DELTA_VERSION,
                                "base_id": self._base_id,
                                "added": added,
                                "removed": removed,
                            },
                            domain=_DELTA_FILENAME,
                        )
                        self._delta_shape = (frozenset(added), frozenset(removed))
                    return
            base_id = secrets.token_hex(8)
            payload = {
                "version": _CACHE_VERSION,
                "window_tokens": self._window_tokens,
                "tokenizer": tokenizer_fingerprint(),
                "base_id": base_id,
                "files": self._fresh,
            }
            dump_sealed_pickle(self._path, payload, domain=_CACHE_FILENAME)
            # A delta left behind names the old base, so it fails the next load.
            with contextlib.suppress(FileNotFoundError):
                self._delta_path.unlink()
            self._base_id = base_id
            self._base_keys = frozenset(self._fresh)
            self._delta_shape = (frozenset(), frozenset())
        except Exception as exc:
            log.debug("duplication_cache_save_failed", error=str(exc))

    def release_memory(self) -> None:
        """Drop cached representations after a full scan has persisted them.

        Callers retain the kind lists they still need for collision
        verification. The cache-owned window tuples are otherwise a second
        repo-sized copy of the live ``WindowHash`` population.
        """
        self._entries.clear()
        self._fresh.clear()

    # -- access ------------------------------------------------------------

    def get(self, content_hash: str) -> _Entry | None:
        entry = self._entries.get(content_hash)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        # Entries touched this run survive into the rewritten cache file.
        self._fresh[content_hash] = entry
        return entry

    def entry(self, content_hash: str) -> _Entry | None:
        """Read an entry without touching hit/miss stats or freshness.

        The incremental pair-splice path reads unchanged files' cached
        tuples through this so its lookups don't skew the cache-hit
        telemetry that ``get`` feeds.
        """
        return self._entries.get(content_hash)

    def retain(self, content_hash: str) -> bool:
        """Mark an entry as live so ``save`` keeps it.

        ``save`` rewrites the cache with only the entries touched this
        run; an incremental run that never reads unchanged files would
        otherwise evict them. Returns False when the hash is unknown.
        """
        entry = self._entries.get(content_hash)
        if entry is None:
            return False
        self._fresh[content_hash] = entry
        return True

    def put(
        self,
        content_hash: str,
        kinds: list[str],
        names: list[str],
        nloc: int,
        windows: list[tuple[int, int, int, int]],
    ) -> None:
        # Intern in place so the detector and cache share one kind list rather
        # than holding two repo-sized lists of references during a full scan.
        # Identity reuse also keeps the pickle compact and comparisons cheap.
        for index, kind in enumerate(kinds):
            kinds[index] = sys.intern(kind)
        entry = (kinds, names, nloc, windows)
        self._entries[content_hash] = entry
        self._fresh[content_hash] = entry
