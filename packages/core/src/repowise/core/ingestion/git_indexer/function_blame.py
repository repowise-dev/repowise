"""Per-line blame index for function-level git signals.

``BlameIndex`` is an in-memory map ``line_no -> (commit_sha, author_unix_time)``
materialised once per file from a single ``git blame --line-porcelain`` call.
Two biomarkers consume it:

* ``function_hotspot`` — projects each function's line range into the set of
  distinct shas touching those lines.
* ``code_age_volatility`` — uses per-line author timestamps to compute median
  age and recent-modification counts over a function's line range.

Sharing one blame invocation across both biomarkers is critical to the 30s
analysis budget; running ``git blame`` twice on the same file doubles the
dominant per-file cost.

The dataclass is in-memory only — it never round-trips through the database.
The indexer attaches it under ``git_meta["blame_index"]`` so the health
engine can pick it up when building each ``FileContext``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from ._constants import _MAX_BLAME_SIZE_BYTES

logger = structlog.get_logger(__name__)

__all__ = [
    "BlameIndex",
    "build_blame_index",
    "distinct_commits_in_range",
    "median_author_time_in_range",
    "owner_in_range",
    "recent_commits_in_range",
]

# Files with fewer than this many total commits have no useful signal — the
# per-function mod counts cannot exceed file mod counts, and noise dominates.
_MIN_COMMITS_FOR_BLAME = 5


@dataclass
class BlameIndex:
    """``line_no (1-indexed) -> (commit_sha, author_unix_time)``.

    Empty index (``lines == {}``) is the documented "no signal" outcome —
    consumers must no-op cleanly when they see it.

    ``authors`` carries the ``sha -> (author_name, author_email)`` mapping
    extracted from the same porcelain pass. This lets the file-level
    ownership computation share one ``git blame`` invocation with the
    function-level biomarkers instead of running blame twice per file.
    """

    lines: dict[int, tuple[str, int]] = field(default_factory=dict)
    authors: dict[str, tuple[str, str]] = field(default_factory=dict)


def ownership_from_blame(idx: BlameIndex) -> tuple[str | None, str | None, float | None]:
    """Derive primary owner from a :class:`BlameIndex`.

    Returns ``(name, email, share)`` where ``share`` is the fraction of
    blamed lines authored by the top author. ``None``s when the index is
    empty (no signal).
    """
    if not idx.lines:
        return None, None, None
    from collections import Counter

    counts: Counter[str] = Counter()
    for sha, _ts in idx.lines.values():
        name = idx.authors.get(sha, ("unknown", ""))[0]
        counts[name] += 1
    if not counts:
        return None, None, None
    total = sum(counts.values())
    top_name, top_count = counts.most_common(1)[0]
    top_email = ""
    for sha, (name, email) in idx.authors.items():
        if name == top_name and email:
            top_email = email
            break
    return top_name, top_email, top_count / total if total else None


def owner_in_range(
    idx: BlameIndex, start_line: int, end_line: int
) -> tuple[str | None, str | None, float | None]:
    """Top blame author over ``[start_line, end_line]`` (inclusive).

    Returns ``(name, email, share)`` where ``share`` is the fraction of blamed
    lines in the range authored by the top author. ``None``s when the range has
    no blame coverage. Scoped variant of :func:`ownership_from_blame`, reused by
    the per-function blame rollup.
    """
    if not idx.lines or start_line > end_line:
        return None, None, None
    from collections import Counter

    counts: Counter[str] = Counter()
    for ln in range(start_line, end_line + 1):
        entry = idx.lines.get(ln)
        if entry is None:
            continue
        name = idx.authors.get(entry[0], ("unknown", ""))[0]
        counts[name] += 1
    if not counts:
        return None, None, None
    total = sum(counts.values())
    top_name, top_count = counts.most_common(1)[0]
    top_email = ""
    for _sha, (name, email) in idx.authors.items():
        if name == top_name and email:
            top_email = email
            break
    return top_name, top_email or None, (top_count / total if total else None)


def _parse_porcelain(
    raw: str,
) -> tuple[dict[int, tuple[str, int]], dict[str, tuple[str, str]]]:
    """Parse ``git blame --line-porcelain`` output into the line index.

    Porcelain format per line block::

        <sha> <orig-line> <final-line> [<num-lines>]
        author Name
        author-time 1700000000
        ... other headers ...
        \t<source line>
    """
    out: dict[int, tuple[str, int]] = {}
    authors: dict[str, tuple[str, str]] = {}
    current_sha: str | None = None
    current_final: int | None = None
    current_author_time: int = 0
    current_author_name: str | None = None
    current_author_email: str | None = None
    # Cache author-time per sha — porcelain only emits headers the first
    # time a sha appears; subsequent blocks repeat sha + line numbers only.
    sha_author_time: dict[str, int] = {}

    for line in raw.splitlines():
        if not line:
            continue
        if line.startswith("\t"):
            if current_sha is not None and current_final is not None:
                t = current_author_time or sha_author_time.get(current_sha, 0)
                out[current_final] = (current_sha, t)
                if current_sha not in authors and current_author_name:
                    authors[current_sha] = (
                        current_author_name,
                        (current_author_email or "").strip("<>"),
                    )
            current_sha = None
            current_final = None
            current_author_time = 0
            current_author_name = None
            current_author_email = None
            continue
        if line.startswith("author-time "):
            try:
                current_author_time = int(line.split(" ", 1)[1])
            except (ValueError, IndexError):
                current_author_time = 0
            if current_sha is not None and current_author_time:
                sha_author_time[current_sha] = current_author_time
            continue
        if line.startswith("author "):
            current_author_name = line[len("author ") :].strip() or "unknown"
            continue
        if line.startswith("author-mail "):
            current_author_email = line[len("author-mail ") :].strip()
            continue
        # Header lines are space-separated; only the very first header of a
        # block starts with a 40-char hex sha. Other headers start with
        # alphabetic keywords (author, committer, summary, previous, filename).
        head, _, rest = line.partition(" ")
        if len(head) == 40 and all(c in "0123456789abcdef" for c in head):
            parts = line.split(" ")
            current_sha = parts[0]
            if len(parts) >= 3:
                try:
                    current_final = int(parts[2])
                except ValueError:
                    current_final = None
            current_author_time = sha_author_time.get(current_sha, 0)
    return out, authors


def build_blame_index(
    repo: Any,
    file_path: str,
    *,
    repo_path: Path | None = None,
    commit_count_total: int = 0,
) -> BlameIndex:
    """Build a :class:`BlameIndex` for *file_path*.

    Skip / no-op rules (the same ones that gate ownership blame):

    * file size exceeds :data:`_MAX_BLAME_SIZE_BYTES`
    * ``commit_count_total < _MIN_COMMITS_FOR_BLAME`` — too sparse for signal
    * any subprocess error — returns an empty index, never raises

    Returns an empty :class:`BlameIndex` on any skip path. Callers must
    treat empty indexes as the "no signal" outcome and produce zero findings.
    """
    if commit_count_total and commit_count_total < _MIN_COMMITS_FOR_BLAME:
        return BlameIndex()
    if repo_path is not None:
        try:
            size = (repo_path / file_path).stat().st_size
            if size > _MAX_BLAME_SIZE_BYTES:
                return BlameIndex()
        except OSError:
            return BlameIndex()
    try:
        # ``repo.git.blame`` invokes the git binary directly (shell=False).
        # ``--line-porcelain`` repeats headers on every line which makes the
        # parser robust to out-of-order line emission from --incremental.
        raw = repo.git.blame("--line-porcelain", "HEAD", "--", file_path)
    except Exception as exc:  # noqa: BLE001 — blame is best-effort
        logger.debug(
            "blame_index_failed",
            path=file_path,
            error=str(exc),
        )
        return BlameIndex()
    if not raw:
        return BlameIndex()
    lines, authors = _parse_porcelain(raw)
    return BlameIndex(lines=lines, authors=authors)


def distinct_commits_in_range(idx: BlameIndex, start_line: int, end_line: int) -> set[str]:
    """Set of commit shas touching ``[start_line, end_line]`` (inclusive)."""
    if not idx.lines or start_line > end_line:
        return set()
    out: set[str] = set()
    for ln in range(start_line, end_line + 1):
        entry = idx.lines.get(ln)
        if entry is not None:
            out.add(entry[0])
    return out


def median_author_time_in_range(idx: BlameIndex, start_line: int, end_line: int) -> int | None:
    """Median ``author_time`` (unix seconds) over the given line range.

    Returns ``None`` when no blame entries cover the range.
    """
    if not idx.lines or start_line > end_line:
        return None
    times = [
        idx.lines[ln][1]
        for ln in range(start_line, end_line + 1)
        if ln in idx.lines and idx.lines[ln][1] > 0
    ]
    if not times:
        return None
    times.sort()
    n = len(times)
    mid = n // 2
    if n % 2:
        return times[mid]
    return (times[mid - 1] + times[mid]) // 2


def recent_commits_in_range(
    idx: BlameIndex,
    start_line: int,
    end_line: int,
    *,
    since_unix_ts: int,
) -> set[str]:
    """Distinct shas touching the range whose ``author_time >= since_unix_ts``."""
    if not idx.lines or start_line > end_line:
        return set()
    out: set[str] = set()
    for ln in range(start_line, end_line + 1):
        entry = idx.lines.get(ln)
        if entry is None:
            continue
        sha, ts = entry
        if ts >= since_unix_ts:
            out.add(sha)
    return out
