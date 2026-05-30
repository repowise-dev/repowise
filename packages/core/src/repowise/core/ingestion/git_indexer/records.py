"""Lightweight data records and path helpers shared across git-index tiers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ._constants import _CODE_EXTENSIONS

__all__ = [
    "_FIELD_SEP",
    "_LOG_FORMAT",
    "_RECORD_SEP",
    "GitIndexSummary",
    "_CommitRec",
    "_extract_rename_paths",
    "_parse_commit_record",
    "_should_skip_index",
]

# Git log record/field separators (NUL byte + US 0x1f) — chosen so they can't
# appear in any commit metadata. Subjects/bodies can legally contain anything
# else (including tabs and newlines), so printable separators are unsafe.
# These are the REAL bytes git emits in its output and are used to split it.
_RECORD_SEP = "\x00"
_FIELD_SEP = "\x1f"

# Shared log format for both the batched repo-wide index and the per-file
# ``--follow`` fallback. NOTE: the format ARG uses git's literal ``%x00`` /
# ``%x1f`` escape text — NOT the real bytes — because a real NUL in a subprocess
# argument raises ``ValueError: embedded null character``. Git expands these
# escapes to the real :data:`_RECORD_SEP` / :data:`_FIELD_SEP` bytes in its
# output, which is what :func:`_parse_commit_record` then splits on. The body
# (``%b``) is captured last because it is the only multi-line field; everything
# after the 6th field separator is "body + numstat" and is disentangled there.
_LOG_FORMAT = "%x00%H%x1f%an%x1f%ae%x1f%ct%x1f%P%x1f%s%x1f%b"

# A git ``--numstat`` line: ``<added>\t<deleted>\t<path>`` where added/deleted
# are decimal counts or ``-`` for binary files. Used to split the trailing
# numstat block away from the (possibly multi-line) commit body.
_NUMSTAT_RE = re.compile(r"^(?:\d+|-)\t(?:\d+|-)\t")


@dataclass
class _CommitRec:
    """Lightweight commit record parsed from ``git log --numstat``."""

    sha: str
    author_name: str
    author_email: str
    ts: int  # unix epoch
    is_merge: bool
    subject: str
    body: str = ""
    added: int = 0
    deleted: int = 0


def _parse_commit_record(record: str) -> tuple[dict, list[str]] | None:
    """Parse one ``\\x00``-delimited git-log record into header + numstat lines.

    A record (after the leading record separator is stripped) looks like::

        <sha>\\x1f<an>\\x1f<ae>\\x1f<ct>\\x1f<parents>\\x1f<subject>\\x1f<body>\\n<numstat...>

    The body is multi-line, so the trailing ``--numstat`` block is identified
    by the first line matching :data:`_NUMSTAT_RE`; everything before it is
    body. Returns ``(header, numstat_lines)`` or ``None`` if the record is
    malformed (too few fields). ``header`` keys mirror :class:`_CommitRec`
    fields except ``added``/``deleted`` (the caller attributes churn).
    """
    parts = record.split(_FIELD_SEP)
    if len(parts) < 7:
        return None
    sha, an, ae, ct, parents, subject = parts[:6]
    # %b cannot contain the field separator, so the body + numstat tail is the
    # 7th field exactly (join is defensive against an unexpected stray sep).
    body_and_numstat = _FIELD_SEP.join(parts[6:])

    body_lines: list[str] = []
    numstat_lines: list[str] = []
    in_numstat = False
    for line in body_and_numstat.split("\n"):
        if not in_numstat and _NUMSTAT_RE.match(line):
            in_numstat = True
        if in_numstat:
            numstat_lines.append(line)
        else:
            body_lines.append(line)

    try:
        ts = int(ct)
    except ValueError:
        ts = 0

    header = {
        "sha": sha,
        "author_name": an or "unknown",
        "author_email": ae,
        "ts": ts,
        "is_merge": len(parents.split()) > 1,
        "subject": subject,
        "body": "\n".join(body_lines).strip(),
    }
    return header, numstat_lines


@dataclass
class GitIndexSummary:
    files_indexed: int
    hotspots: int
    stable_files: int
    duration_seconds: float = 0.0
    # Per-commit rows collected during the repo-wide commit-index walk, ready
    # for ``upsert_git_commits_bulk``. Empty in rename-tracking mode (which uses
    # the per-file walk instead of the batched commit index).
    commit_rows: list[dict] = field(default_factory=list)


_RENAME_RE = re.compile(r"\{(.+?) => (.+?)\}")


def _extract_rename_paths(stat_path: str, known_paths: set[str]) -> tuple[str | None, str | None]:
    """Extract old/new paths from a git numstat rename line and add to *known_paths*.

    Git ``--numstat`` with ``--follow`` emits rename lines like::

        10\t5\t{old => new}/shared_suffix
        10\t5\told_dir/{old_name => new_name}.py

    This helper parses both forms, adds both expanded paths to *known_paths*,
    and returns ``(old_path, new_path)`` so the caller can attribute churn to
    the correct file.  Returns ``(None, None)`` if the pattern is not found.
    """
    m = _RENAME_RE.search(stat_path)
    if m:
        prefix = stat_path[: m.start()]
        suffix = stat_path[m.end() :]
        old_path = prefix + m.group(1) + suffix
        new_path = prefix + m.group(2) + suffix
        known_paths.add(old_path)
        known_paths.add(new_path)
        return old_path, new_path
    return None, None


def _should_skip_index(file_path: str) -> bool:
    """Return True for files where per-file git indexing should be skipped.

    Uses an allowlist: only files with known source-code extensions are indexed.
    Everything else (data, config, markup, dotfiles, binaries) is skipped.
    """
    return Path(file_path).suffix.lower() not in _CODE_EXTENSIONS
