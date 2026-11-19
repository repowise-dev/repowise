"""Each file's scanned tokens, kept between dead-code runs.

The settle passes scan every indexed file's text for identifiers, path-shaped
tokens and module-path strings, several times a run, and an update repeated
all of it when one file changed. A scan is a pure function of the bytes and the
pattern, so its token set is cached under ``(kind, sha1 of the bytes)``; which
tokens a pass then asks about still depends on the run.

The fingerprint covers this module, the modules that define the patterns and
the Python version. Best-effort like the drift extraction cache: any load or
save error means a cold scan, and only entries used this run are kept.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

_CACHE_FILENAME = "dead_code_token_cache.pkl"
_HERE = Path(__file__).resolve().parent
# Every module whose code decides a scan's result: the patterns and blanking
# rules live in these.
_SCAN_SOURCES = (
    _HERE / "token_cache.py",
    _HERE / "name_occurrences.py",
    _HERE / "c_name_uses.py",
    _HERE / "module_strings.py",
)

Tokens = frozenset


@lru_cache(maxsize=1)
def _fingerprint() -> str | None:
    """Digest of what decides a scan besides the bytes; ``None`` disables the cache."""
    try:
        h = hashlib.sha256(sys.version.encode())
        for source in _SCAN_SOURCES:
            h.update(source.name.encode())
            h.update(source.read_bytes())
    except Exception:
        return None
    return h.hexdigest()


class TokenCache:
    """``(kind, sha1 of bytes) -> tokens``, persisted in *cache_dir* when it exists."""

    def __init__(self, cache_dir: Path | None) -> None:
        self._fingerprint = _fingerprint() if cache_dir is not None else None
        self._path = (
            cache_dir / _CACHE_FILENAME
            if cache_dir is not None and self._fingerprint is not None and cache_dir.is_dir()
            else None
        )
        self._stored: dict[tuple[str, bytes], Tokens] = {}
        self._fresh: dict[tuple[str, bytes], Tokens] = {}
        # This run's answers keyed by the bytes themselves: a ``bytes`` caches
        # its hash, so a repeat scan of one blob costs a lookup, not a digest.
        self._memo: dict[tuple[str, bytes], Tokens] = {}
        # One object per distinct token, so the pickle stores each spelling once.
        self._interned: dict[bytes, bytes] = {}
        if self._path is not None:
            self._load()

    def _load(self) -> None:
        try:
            # HMAC-verified before unpickling: the file lives inside the indexed repo.
            payload = load_sealed_pickle(self._path, domain=_CACHE_FILENAME)
            if payload.get("fingerprint") == self._fingerprint:
                self._stored = payload.get("entries", {})
        except FileNotFoundError:
            return
        except Exception as exc:
            log.debug("dead_code_token_cache_load_failed", error=str(exc))

    def tokens(self, kind: str, blob: bytes, scan: Callable[[], Iterable]) -> Tokens:
        """*scan*'s tokens of *blob*, scanned once per distinct bytes and kind."""
        memo_key = (kind, blob)
        value = self._memo.get(memo_key)
        if value is not None:
            return value
        key = (kind, hashlib.sha1(blob).digest())
        value = self._stored.get(key)
        if value is None:
            interned = self._interned
            value = frozenset(interned.setdefault(t, t) for t in scan())
        self._memo[memo_key] = value
        if self._path is not None:
            self._fresh[key] = value
        return value

    def save(self) -> None:
        """Persist the entries used this run; a cache without a directory keeps nothing."""
        if self._path is None or self._fresh == self._stored:
            return
        try:
            payload = {"fingerprint": self._fingerprint, "entries": self._fresh}
            dump_sealed_pickle(self._path, payload, domain=_CACHE_FILENAME)
        except Exception as exc:
            log.debug("dead_code_token_cache_save_failed", error=str(exc))


_ACTIVE: ContextVar[TokenCache | None] = ContextVar("dead_code_token_cache", default=None)


@contextmanager
def token_cache(cache_dir: Path | None) -> Iterator[TokenCache]:
    """Share one :class:`TokenCache` across the scans of one analysis, then save it."""
    cache = TokenCache(cache_dir)
    reset = _ACTIVE.set(cache)
    try:
        yield cache
    finally:
        _ACTIVE.reset(reset)
    cache.save()


def scanned(kind: str, blob: bytes, scan: Callable[[], Iterable]) -> Tokens:
    """*scan*'s tokens of *blob*, through the active cache when one is set."""
    cache = _ACTIVE.get()
    if cache is None:
        return frozenset(scan())
    return cache.tokens(kind, blob, scan)
