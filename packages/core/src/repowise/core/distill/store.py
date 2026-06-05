"""OmissionStore — durable stash of content dropped by distillation.

Lives in its own SQLite sidecar (``.repowise/omissions/omissions.db``, WAL)
rather than wiki.db so hook-time writes never contend with indexing. Rows are
keyed by a 12-hex truncated SHA-256 of the content, the same ref embedded in
omission markers, so ``expand`` resolves markers from any surface against one
store. Durable across sessions by design (an agent resuming work can still
expand yesterday's markers); pruning is TTL + size-cap based, applied
opportunistically on write.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
import zlib
from pathlib import Path

import structlog

from repowise.core.distill import tracking
from repowise.core.distill.markers import REF_LENGTH, is_valid_ref

logger = structlog.get_logger(__name__)

OMISSIONS_DIRNAME = "omissions"
OMISSIONS_DB_FILENAME = "omissions.db"

#: Rows older than this are pruned.
DEFAULT_TTL_DAYS = 7
#: Compressed-content size cap; oldest rows pruned first when exceeded.
DEFAULT_MAX_MB = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS omissions (
    ref TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    original_tokens INTEGER NOT NULL,
    kept_tokens INTEGER NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS savings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    filter TEXT NOT NULL,
    source TEXT NOT NULL,
    command TEXT,
    raw_tokens INTEGER NOT NULL,
    distilled_tokens INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_omissions_created ON omissions(created_at);
CREATE INDEX IF NOT EXISTS idx_savings_created ON savings(created_at);
"""


def default_store_path(start: Path | None = None) -> Path:
    """Resolve the omissions DB path for the repo containing *start*.

    Walks upward looking for an existing ``.repowise/`` directory (stopping at
    the user's home directory) and falls back to ``~/.repowise/`` — the same
    fallback convention as the wiki database. Never creates ``.repowise/`` in
    a repo that has not opted in via ``repowise init``.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()
    for candidate in (current, *current.parents):
        if candidate == home:
            break
        if (candidate / ".repowise").is_dir():
            return candidate / ".repowise" / OMISSIONS_DIRNAME / OMISSIONS_DB_FILENAME
    return home / ".repowise" / OMISSIONS_DIRNAME / OMISSIONS_DB_FILENAME


def content_ref(content: str) -> str:
    """Stable 12-hex ref for *content* (truncated SHA-256)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:REF_LENGTH]


class OmissionStore:
    """Synchronous SQLite store for omitted content and the savings ledger.

    Synchronous on purpose: callers are CLI commands and (later) hook scripts
    where an asyncio loop is pure overhead.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        ttl_days: float = DEFAULT_TTL_DAYS,
        max_mb: float = DEFAULT_MAX_MB,
    ) -> None:
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.max_mb = max_mb
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def open_default(cls, start: Path | None = None) -> OmissionStore:
        """Open the store for the repo containing *start* (default: cwd)."""
        return cls(default_store_path(start))

    # -- omissions ---------------------------------------------------------

    def put(
        self,
        content: str,
        *,
        source: str,
        original_tokens: int,
        kept_tokens: int,
    ) -> str:
        """Store *content* and return its marker ref. Idempotent per content."""
        ref = content_ref(content)
        blob = zlib.compress(content.encode("utf-8"))
        self._conn.execute(
            """
            INSERT INTO omissions
                (ref, content, source, created_at, original_tokens, kept_tokens)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ref) DO UPDATE SET created_at = excluded.created_at
            """,
            (ref, blob, source, time.time(), original_tokens, kept_tokens),
        )
        self._conn.commit()
        self.prune()
        return ref

    def get(self, ref: str, *, query: str | None = None) -> str | None:
        """Return the stored content for *ref*, or None when unknown/expired.

        With *query*, returns only the lines matching it (regex when it
        compiles, plain substring otherwise) — cheap search-within-original.
        """
        record = self.get_record(ref, query=query)
        return record["content"] if record is not None else None

    def get_record(self, ref: str, *, query: str | None = None) -> dict | None:
        """Like :meth:`get` but with the row's provenance metadata.

        Returns ``{content, source, created_at, original_tokens, kept_tokens}``
        or ``None`` when the ref is unknown/expired. *query* filters the
        content lines exactly as in :meth:`get`.
        """
        if not is_valid_ref(ref):
            return None
        row = self._conn.execute(
            "SELECT content, source, created_at, original_tokens, kept_tokens "
            "FROM omissions WHERE ref = ?",
            (ref,),
        ).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE omissions SET access_count = access_count + 1 WHERE ref = ?", (ref,)
        )
        self._conn.commit()
        content = zlib.decompress(row[0]).decode("utf-8")
        if query is not None:
            content = _filter_lines(content, query)
        return {
            "content": content,
            "source": row[1],
            "created_at": row[2],
            "original_tokens": row[3],
            "kept_tokens": row[4],
        }

    def prune(self) -> None:
        """Drop rows past TTL, then oldest-first until under the size cap."""
        cutoff = time.time() - self.ttl_days * 86400
        self._conn.execute("DELETE FROM omissions WHERE created_at < ?", (cutoff,))
        max_bytes = int(self.max_mb * 1024 * 1024)
        while True:
            total, rows = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(content)), 0), COUNT(*) FROM omissions"
            ).fetchone()
            # Never evict the last row: it may be the one just written, and a
            # marker must not dangle the moment it is rendered.
            if total <= max_bytes or rows <= 1:
                break
            self._conn.execute(
                """
                DELETE FROM omissions WHERE ref IN (
                    SELECT ref FROM omissions ORDER BY created_at ASC
                    LIMIT MIN(16, (SELECT COUNT(*) - 1 FROM omissions))
                )
                """
            )
        self._conn.commit()

    # -- savings ledger ----------------------------------------------------

    def record_saving(
        self,
        *,
        filter_name: str,
        source: str,
        command: str | None,
        raw_tokens: int,
        distilled_tokens: int,
    ) -> None:
        """Append one distillation event to the savings ledger."""
        tracking.record_saving(
            self._conn,
            filter_name=filter_name,
            source=source,
            command=command,
            raw_tokens=raw_tokens,
            distilled_tokens=distilled_tokens,
        )

    def savings_summary(self, *, since: float | None = None) -> dict:
        """Aggregate ledger totals, overall and per filter."""
        return tracking.savings_summary(self._conn, since=since)

    def savings_rollup(self, *, by: str = "filter", since: float | None = None) -> list[dict]:
        """Grouped ledger totals (see :func:`tracking.savings_rollup`)."""
        return tracking.savings_rollup(self._conn, by=by, since=since)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> OmissionStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _filter_lines(content: str, query: str) -> str:
    """Lines of *content* matching *query* (regex if valid, else substring)."""
    import re

    try:
        pattern = re.compile(query)
        matcher = pattern.search
    except re.error:
        matcher = lambda line: query in line  # noqa: E731
    matched = [line for line in content.splitlines() if matcher(line)]
    return "\n".join(matched)
