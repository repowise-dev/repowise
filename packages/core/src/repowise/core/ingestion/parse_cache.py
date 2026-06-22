"""Content-hash keyed cache for ParsedFile results.

Parsing is the repo-proportional term of every ingest: ``repowise update``
re-parses the whole repo when one file changed, and large-repo inits pay
minutes of tree-sitter time. A ParsedFile is a pure function of the file
*bytes* and the parser itself, so it caches safely keyed by
``(relative path, content hash)`` under a parser fingerprint (the compiled
``.scm`` query sources + ``PARSER_SCHEMA_VERSION``) that invalidates the whole
cache when extraction rules change. The fingerprint excludes the package
version on purpose so unrelated releases keep the cache warm.

Entries are stored as pickle *bytes* snapshotted at parse time — downstream
graph builds mutate ParsedFile in place (``Import.resolved_file``,
``NamedBinding.source_file`` are back-filled during resolution), so a hit
must deserialize a fresh object graph rather than share one. The path is
part of the key because symbol IDs embed it; a rename is a miss and
re-parses (producing IDs for the new path). On a hit the *current*
traversal ``FileInfo`` is rebound so git hash / mtime stay fresh.

Best-effort by design: any load/save/deserialize error degrades to a full
parse. The cache file is rewritten with only the entries touched this run,
so deleted files age out naturally.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import pickle
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from .models import FileInfo, ParsedFile

log = structlog.get_logger(__name__)

_CACHE_VERSION = 1
_CACHE_FILENAME = "parse_cache.pkl"

__all__ = ["ParseCache", "parser_fingerprint"]


@lru_cache(maxsize=1)
def parser_fingerprint() -> str:
    """Fingerprint of everything that determines a ParsedFile besides bytes.

    Hashes every ``.scm`` query source (extraction rules) plus
    ``PARSER_SCHEMA_VERSION`` (Python-side extraction logic). Deliberately
    *not* the package ``__version__``: an unrelated release must not churn a
    user's whole parse cache. The schema version is bumped only when parser /
    extractor behaviour actually changes, so a mismatch still invalidates the
    cache when correctness demands it. See :mod:`repowise.core.upgrade.version`.
    """
    h = hashlib.sha256()
    h.update(f"cache-version:{_CACHE_VERSION}".encode())
    try:
        from repowise.core.upgrade.version import PARSER_SCHEMA_VERSION

        h.update(f"parser-schema:{PARSER_SCHEMA_VERSION}".encode())
    except Exception:
        h.update(b"parser-schema:unknown")
    queries_dir = Path(__file__).parent / "queries"
    try:
        for scm in sorted(queries_dir.glob("*.scm")):
            h.update(scm.name.encode())
            h.update(scm.read_bytes())
    except Exception:
        h.update(b"queries:unreadable")
    return h.hexdigest()


class ParseCache:
    """Pickle-backed ``(path, content_hash) -> ParsedFile-bytes`` store."""

    def __init__(self, cache_dir: Path | str) -> None:
        self._path = Path(cache_dir) / _CACHE_FILENAME
        self._fingerprint = parser_fingerprint()
        self._entries: dict[tuple[str, str], bytes] = {}
        self._fresh: dict[tuple[str, str], bytes] = {}
        self.hits = 0
        self.misses = 0

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        try:
            with self._path.open("rb") as fh:
                payload = pickle.load(fh)
            if (
                payload.get("version") != _CACHE_VERSION
                or payload.get("fingerprint") != self._fingerprint
            ):
                return
            self._entries = payload.get("files", {})
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupt / unreadable cache -> full parse
            log.debug("parse_cache_load_failed", error=str(exc))

    def save(self) -> None:
        """Atomically persist the entries used or created this run."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": _CACHE_VERSION,
                "fingerprint": self._fingerprint,
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
            log.debug("parse_cache_save_failed", error=str(exc))

    # -- access ------------------------------------------------------------

    def get(self, file_info: FileInfo, content_hash: str) -> ParsedFile | None:
        """Return a fresh ParsedFile for *content_hash* or None on miss.

        Hits deserialize a new object graph (downstream resolution mutates
        ParsedFile in place) and rebind the current *file_info* so traversal
        metadata (git hash, mtime) stays current. *content_hash* is
        :func:`compute_content_hash` of the raw bytes — callers compute it
        once and reuse it for the matching :meth:`put`.
        """
        key = (file_info.path, content_hash)
        blob = self._entries.get(key)
        if blob is None:
            self.misses += 1
            return None
        try:
            parsed: Any = pickle.loads(blob)
        except Exception as exc:  # corrupt entry -> re-parse this file
            log.debug("parse_cache_entry_corrupt", path=file_info.path, error=str(exc))
            self.misses += 1
            return None
        self.hits += 1
        self._fresh[key] = blob
        parsed.file_info = file_info
        return parsed

    def put(self, parsed: ParsedFile, content_hash: str) -> None:
        """Snapshot *parsed* (call before any graph build mutates it)."""
        try:
            blob = pickle.dumps(parsed, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            log.debug("parse_cache_put_failed", path=parsed.file_info.path, error=str(exc))
            return
        key = (parsed.file_info.path, content_hash)
        self._entries[key] = blob
        self._fresh[key] = blob
