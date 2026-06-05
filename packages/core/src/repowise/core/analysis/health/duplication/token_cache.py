"""Content-hash keyed cache for duplication token streams + windows.

Tokenizing every file (a full tree-sitter re-parse plus a pure-Python
leaf walk) and re-rolling 1M+ window hashes dominates the duplication
pass — and an incremental ``repowise update`` re-pays it for the whole
repo when a single file changed. Both outputs are pure functions of the
file *bytes* (given fixed window size and pinned hash constants), so they
cache safely by content hash.

Cached per file: the normalized token-kind sequence (all the verifier
ever compares), the non-blank line count, and the rolling-hash windows
as plain tuples (``WindowHash`` is rebuilt with the file's *current*
path, which makes hits rename-proof). The minified/token-cap/window-
budget gates in the detector stay live — they re-evaluate against the
cached lengths, so config changes apply to cached entries too.

Best-effort by design: any load/save error degrades to a full
re-tokenize. The cache file is rewritten with only the current file set
each run, so deleted files age out naturally.
"""

from __future__ import annotations

import contextlib
import os
import pickle
import sys
import tempfile
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_CACHE_VERSION = 1
_CACHE_FILENAME = "duplication_cache.pkl"


class DuplicationTokenCache:
    """Pickle-backed ``content_hash -> (kinds, nloc, windows)`` store."""

    def __init__(self, cache_dir: Path, window_tokens: int) -> None:
        self._path = Path(cache_dir) / _CACHE_FILENAME
        self._window_tokens = window_tokens
        self._entries: dict[str, tuple[list[str], int, list[tuple[int, int, int, int]]]] = {}
        self._fresh: dict[str, tuple[list[str], int, list[tuple[int, int, int, int]]]] = {}
        self.hits = 0
        self.misses = 0

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        try:
            with self._path.open("rb") as fh:
                payload = pickle.load(fh)
            if (
                payload.get("version") != _CACHE_VERSION
                or payload.get("window_tokens") != self._window_tokens
            ):
                return
            self._entries = payload.get("files", {})
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupt / unreadable cache -> full tokenize
            log.debug("duplication_cache_load_failed", error=str(exc))

    def save(self) -> None:
        """Atomically persist the entries used or created this run."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": _CACHE_VERSION,
                "window_tokens": self._window_tokens,
                "files": self._fresh,
            }
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=_CACHE_FILENAME, suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as fh:
                    pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
                os.replace(tmp_name, self._path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
        except Exception as exc:
            log.debug("duplication_cache_save_failed", error=str(exc))

    # -- access ------------------------------------------------------------

    def get(
        self, content_hash: str
    ) -> tuple[list[str], int, list[tuple[int, int, int, int]]] | None:
        entry = self._entries.get(content_hash)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        # Entries touched this run survive into the rewritten cache file.
        self._fresh[content_hash] = entry
        return entry

    def put(
        self,
        content_hash: str,
        kinds: list[str],
        nloc: int,
        windows: list[tuple[int, int, int, int]],
    ) -> None:
        # Interned kinds keep the pickle compact (each distinct kind is
        # memoized once by identity) and make later equality checks cheap.
        entry = ([sys.intern(k) for k in kinds], nloc, windows)
        self._entries[content_hash] = entry
        self._fresh[content_hash] = entry
