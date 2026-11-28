"""The fix walk's per-commit log records, kept between runs so an update reads only new commits.

The prior-defect window is a topological range (``prior_sha..head``), which on
a busy repository with long-lived branches holds tens of thousands of commits,
and ``git log -M --name-status`` over all of them costs minutes. A commit's
``--name-status`` rows never change, so the walk keeps what it read of each
commit here: for a fix, its time, prose and paths; for any other commit, only
its rename rows, which are all the rename trail needs from it. The next walk
asks git only for the range's order (``%H``, no diff), reads the commits the
cache lacks with ``--no-walk``, and replays the same loop over the records in
that order, so the result is the full walk's.

Keyed on :func:`..git_commit_index._record_context` (settings that change the
rendered paths or the rename rows) and a fingerprint of the code that parses
the log and names a fix: either changing discards every record, so the cache
can only save time, never change a count.

Ceiling: shallow clones skip the cache, because a boundary commit's rows
depend on where the clone is cut. Deepening the clone first lifts it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_CACHE_NAME = "fix_walk_cache.json"
_CACHE_VERSION = 1


def _code_fingerprint() -> str:
    """Hash of the code a record's content depends on besides the commit."""
    from . import _constants, fix_rule, prior_defects, records

    digest = hashlib.sha256()
    for module in (fix_rule, prior_defects, records, _constants):
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()


def _rows_ok(rows: Any) -> bool:
    return isinstance(rows, list) and all(
        isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) for row in rows
    )


def _well_formed(record: Any) -> bool:
    """Whether *record* has the shape the replay reads; a cache with any other is dropped."""
    if isinstance(record, dict):
        return (
            isinstance(record.get("t"), int)
            and isinstance(record.get("s"), str)
            and isinstance(record.get("b"), str)
            and _rows_ok(record.get("r"))
        )
    return _rows_ok(record)


class FixWalkCache:
    """``sha -> record`` for the commits of the last fix walk.

    A record is ``[ts, subject, body, rows]`` for a fix commit and ``rows`` (its
    rename rows only, possibly empty) for any other; ``rows`` are
    ``[path, renamed_from]`` pairs in log order.
    """

    def __init__(self, path: Path | None, key: dict[str, str], records: dict[str, Any]) -> None:
        self._path = path
        self._key = key
        self.records = records

    @classmethod
    def load(cls, cache_dir: Path | None, repo: Any, *, writable: bool = True) -> FixWalkCache | None:
        """The cache under *cache_dir*; ``None`` when this repository cannot use one.

        ``writable=False`` reads it but leaves the file as it was.
        """
        if cache_dir is None:
            return None
        from ..git_commit_index import _record_context, _shallow_commits

        try:
            if _shallow_commits(repo):
                return None
            key = {
                "context": _record_context(repo, repo.head.commit.hexsha),
                "code": _code_fingerprint(),
            }
        except Exception as exc:
            logger.debug("fix_walk_cache_key_failed", error=str(exc))
            return None
        path = Path(cache_dir) / _CACHE_NAME
        records: dict[str, Any] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == _CACHE_VERSION and data.get("key") == key:
                    records = dict(data["records"])
                    if not all(map(_well_formed, records.values())):
                        records = {}
            except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                logger.debug("fix_walk_cache_read_failed", error=str(exc))
        return cls(path if writable else None, key, records)

    def save(self, order: list[str]) -> None:
        """Keep the records of *order* (the walk just replayed) and write them."""
        if self._path is None:
            return
        try:
            from repowise.core.fsutils import atomic_write_text

            kept = {sha: self.records[sha] for sha in order if sha in self.records}
            atomic_write_text(
                self._path,
                json.dumps({"version": _CACHE_VERSION, "key": self._key, "records": kept}),
            )
        except (OSError, TypeError, ValueError) as exc:
            logger.debug("fix_walk_cache_write_failed", error=str(exc))
