"""A ``pg_stat_statements`` export as ``psql --csv`` writes it: production statement statistics.

One SELECT, run against the production database by whoever may read it, gives
a file ``repowise evidence add`` reads with no connector::

    psql "$DATABASE_URL" --csv -c "select now() as captured_at, queryid, query,
      calls, total_exec_time, mean_exec_time, rows, stats_since
      from pg_stat_statements" > statements.csv

The first line is the header ``psql --csv`` writes (column names); every
other line is one statement. Read by name, so any column order and any
extra column works: ``calls`` and ``total_exec_time`` (``total_time``
before PostgreSQL 13) are required, plus ``queryid`` or ``query``.
``mean_exec_time`` (``mean_time``), ``rows``, ``stats_since`` (PostgreSQL
17) and ``captured_at`` (``now()`` in the same SELECT) are read when present.

Each row becomes a ``statement_stats`` entry. The statement's id is
``queryid`` as 16 hex digits, or a digest of the text when the export left
``queryid`` out. Rows of one statement for several users or databases add up
to one entry (their mean is the summed time over the summed calls). The
text goes through the v1 reader, which keeps it short and masks every value
(``format_v1.statement_text``); ``pg_stat_statements`` has already replaced
constants with ``$n``. Planning time is not read: the totals are execution
time. The window starts at the earliest ``stats_since`` and ends at
``captured_at``; either is unknown when the export left it out, since the
counters are cumulative since the last reset.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from datetime import datetime
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError, statement_text

_TOTAL = ("total_exec_time", "total_time")
_MEAN = ("mean_exec_time", "mean_time")
_HINT = (
    'export it with psql --csv -c "select queryid, query, calls, total_exec_time, '
    'mean_exec_time, rows from pg_stat_statements"'
)


def _header(head: bytes) -> list[str] | None:
    line = head.lstrip(b"\xef\xbb\xbf").split(b"\n", 1)[0].rstrip(b"\r")
    try:
        text = line.decode("ascii")
    except UnicodeDecodeError:
        return None
    return [name.strip().strip('"').lower() for name in text.split(",")]


def _claims(columns: list[str]) -> bool:
    names = set(columns)
    return (
        "calls" in names
        and any(name in names for name in _TOTAL)
        and ("queryid" in names or "query" in names)
    )


def sniff(head: bytes) -> bool:
    columns = _header(head)
    return columns is not None and _claims(columns)


def _number(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _first(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    return next((row[name] for name in names if row.get(name) not in (None, "")), None)


def _fingerprint(row: dict[str, str]) -> str | None:
    """``queryid`` as 16 hex digits (two's complement), else a digest of the text."""
    raw = (row.get("queryid") or "").strip()
    if raw:
        try:
            return f"{int(raw) & 0xFFFFFFFFFFFFFFFF:016x}"
        except ValueError:
            return None
    text = statement_text(_readable_query(row))
    if text is None:
        return None
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _instant(value: str | None) -> str | None:
    """A ``timestamptz`` as psql prints it (``2026-11-26 09:00:00.12+00``) in ISO 8601."""
    if value is None or not value.strip():
        return None
    text = value.strip().replace(" ", "T", 1)
    # psql prints a whole-hour offset as ``+00``; ISO 8601 wants ``+00:00``.
    if len(text) > 3 and text[-3] in "+-" and text[-2:].isdigit():
        text += ":00"
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    return at.isoformat() if at.tzinfo is not None else None


def _reader(data: bytes) -> csv.DictReader[str]:
    """The export's rows by lower-case column name, or the refusal."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
    reader = csv.DictReader(io.StringIO(text)) if text is not None else None
    columns = [name.strip().lower() for name in (reader.fieldnames or [])] if reader else []
    if reader is None or not _claims(columns):
        raise EvidenceFormatError(
            "the file is not a pg_stat_statements CSV: its header needs calls, "
            "total_exec_time and queryid or query",
            _HINT,
        )
    reader.fieldnames = columns
    return reader


#: What ``pg_stat_statements`` shows for a statement another role ran.
_HIDDEN_QUERY = "<insufficient privilege>"


def _readable_query(row: dict[str, str]) -> str | None:
    query = row.get("query")
    if query is None or not query.strip() or query.strip() == _HIDDEN_QUERY:
        return None
    return query


def _add_row(totals: dict[str, dict[str, Any]], row: dict[str, str]) -> bool:
    """Fold *row* into its statement's entry; False when the row cannot be read."""
    fp = _fingerprint(row)
    calls = _number(row.get("calls"))
    if fp is None or calls is None or calls != int(calls) or calls < 0:
        return False
    entry = totals.setdefault(
        fp,
        {
            "statement_fp": fp,
            "query": None,
            "calls": 0,
            "total_ms": None,
            "rows": None,
            "_mean": _number(_first(row, _MEAN)),
            "_parts": 0,
            "_timed_calls": 0,
        },
    )
    entry["calls"] += int(calls)
    entry["_parts"] += 1
    # The first row that shows the text: another role's row reads as a placeholder.
    if entry["query"] is None:
        entry["query"] = _readable_query(row)
    total = _number(_first(row, _TOTAL))
    if total is not None:
        entry["total_ms"] = (entry["total_ms"] or 0.0) + total
        entry["_timed_calls"] += int(calls)
    rows = _number(row.get("rows"))
    if rows is not None and rows == int(rows):
        entry["rows"] = (entry["rows"] or 0) + int(rows)
    return True


def _finished(entry: dict[str, Any]) -> dict[str, Any]:
    """*entry* with its mean: the export's own for one row, else the summed
    time over the calls it covers (rows of several users and databases)."""
    parts, mean, timed = entry.pop("_parts"), entry.pop("_mean"), entry.pop("_timed_calls")
    if parts > 1 or mean is None:
        total = entry["total_ms"]
        mean = total / timed if total is not None and timed else None
    entry["mean_ms"] = mean
    return entry


def _window(starts: list[str], ends: list[str]) -> dict[str, Any] | None:
    """The earliest ``stats_since`` to the latest ``captured_at``; None when unknown.

    A start after the end (clocks that disagree, an edited file) says nothing
    about the window, so it is left unknown rather than refused.
    """
    # Text times compare in order only with one offset; sort parsed ones.
    start = min(starts, key=datetime.fromisoformat) if starts else None
    end = max(ends, key=datetime.fromisoformat) if ends else None
    if start and end and datetime.fromisoformat(start) > datetime.fromisoformat(end):
        return None
    return {"start": start, "end": end} if start or end else None


def to_v1(data: bytes) -> dict[str, Any]:
    """The whole export as a v1 ``statement_stats`` document; entries are checked by the v1 parser."""
    totals: dict[str, dict[str, Any]] = {}
    skipped = 0
    starts: list[str] = []
    ends: list[str] = []
    try:
        for row in _reader(data):
            if not _add_row(totals, row):
                skipped += 1
                continue
            if since := _instant(row.get("stats_since")):
                starts.append(since)
            if captured := _instant(row.get("captured_at")):
                ends.append(captured)
    except csv.Error as exc:
        # A field past the csv module's limit, or a malformed line.
        raise EvidenceFormatError(
            f"the pg_stat_statements CSV cannot be read: {exc}",
            "export it again with psql --csv; select left(query, 1000) as query to keep "
            "the text short",
        ) from None
    document: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "kind": "statement_stats",
        "tool": {"name": "pg_stat_statements"},
        "statements": [_finished(entry) for entry in totals.values()],
    }
    if skipped:
        document["skipped"] = {"unreadable_row": skipped}
    if (window := _window(starts, ends)) is not None:
        document["window"] = window
    return document


__all__ = ["sniff", "to_v1"]
