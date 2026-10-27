"""Incremental transcript cursors: never re-read what was already mined.

Transcripts are append-only JSONL, so a byte offset at a line boundary is a
complete resume point. :class:`CursorStore` keeps one ``{offset, mtime}``
record per transcript file in a small JSON sidecar;
:func:`iter_new_events` seeks to the stored offset and yields only events
from lines appended since, advancing the cursor as each complete line is
consumed (a partially consumed iterator leaves the cursor at the last line
it actually delivered).

Safety rules:
- a file shorter than its stored offset was truncated or replaced: restart
  from zero;
- a trailing line without a newline is a write in progress: leave it for
  the next pass;
- an unreadable or corrupt sidecar degrades to an empty store, never an
  error.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from repowise.core.sessions.adapters.base import HarnessAdapter, RawPrefilter
from repowise.core.sessions.events import Event


class CursorStore:
    """Per-file resume offsets, persisted as one JSON sidecar.

    Load happens on construction, mutation is in memory, and nothing hits
    disk until :meth:`save`; callers batch one save per scan.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._cursors: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            key: value
            for key, value in data.items()
            if isinstance(value, dict) and isinstance(value.get("offset"), int)
        }

    def get(self, file: Path) -> dict[str, Any] | None:
        return self._cursors.get(str(file))

    def advance(self, file: Path, *, offset: int, mtime: float) -> None:
        self._cursors[str(file)] = {"offset": offset, "mtime": mtime}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._cursors, indent=0, sort_keys=True), encoding="utf-8")


def iter_new_events(
    adapter: HarnessAdapter,
    path: Path,
    store: CursorStore,
    *,
    prefilter: RawPrefilter | None = None,
) -> Iterator[Event]:
    """Events appended to *path* since the stored cursor, advancing it.

    The file is read in binary so offsets are exact bytes; each line decodes
    tolerantly before hitting the adapter's shared read loop. ``OSError``
    propagates, matching :meth:`HarnessAdapter.iter_events`.
    """
    stat = path.stat()
    cursor = store.get(path)
    start = 0
    if cursor is not None and 0 <= cursor["offset"] <= stat.st_size:
        start = cursor["offset"]
    if start == stat.st_size and cursor is not None and cursor.get("mtime") == stat.st_mtime:
        return  # nothing appended since the last pass

    with path.open("rb") as fh:
        if start:
            fh.seek(start)
        lines = _cursored_lines(fh, path, store, start=start, mtime=stat.st_mtime)
        yield from adapter.events_from_lines(lines, prefilter=prefilter, path=path)


def _cursored_lines(
    fh: Iterator[bytes], path: Path, store: CursorStore, *, start: int, mtime: float
) -> Iterator[str]:
    """Decoded lines from *fh*, advancing the cursor as each is handed over.

    The cursor moves when a complete line is read, not when an event is
    yielded for it, so a line the prefilter drops is still consumed exactly
    once. A partially consumed iterator therefore leaves the cursor at the
    last line it actually read.
    """
    offset = start
    for raw_bytes in fh:
        if not raw_bytes.endswith(b"\n"):
            break  # write in progress; next pass picks it up
        offset += len(raw_bytes)
        store.advance(path, offset=offset, mtime=mtime)
        yield raw_bytes.decode("utf-8", errors="replace")
