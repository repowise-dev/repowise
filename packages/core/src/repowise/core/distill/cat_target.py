"""Which file a ``cat`` command reads, and what the index holds for it.

Shared by the ``file_read`` distill filter and the rewrite hook's gate, so the
two cannot disagree about the target. Stdlib only and import-cheap: the hook
imports it on the shell hot path.
"""

from __future__ import annotations

import os
import re
import shlex
import sqlite3
from urllib.parse import quote

_GIT_BASH_DRIVE_RE = re.compile(r"^/([a-zA-Z])/")

#: Outputs under this size pass through.
MIN_OUTPUT_CHARS = 5_000
#: Files shorter than this pass through: reading them whole is fine.
MIN_FILE_LINES = 300


def resolve_cat_path(command: str, cwd: str) -> str | None:
    """Absolute path of the one file ``cat`` reads in *command*, or None."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return None
    base = _native(cwd)
    if len(words) == 5 and words[0] == "cd" and words[2] in ("&&", ";"):
        base = os.path.join(cwd, _native(words[1]))
        words = words[3:]
    if len(words) != 2 or words[0] != "cat" or words[1].startswith("-"):
        return None
    return os.path.normpath(os.path.join(base, _native(words[1])))


def _native(path: str) -> str:
    """A Git Bash ``/c/...`` path as ``C:/...`` on Windows; others unchanged."""
    if os.name == "nt":
        return _GIT_BASH_DRIVE_RE.sub(lambda m: m.group(1).upper() + ":/", path)
    return path


def locate_index(path: str) -> tuple[str, str] | None:
    """(index db path, repo-relative posix path) for *path*, or None."""
    current = os.path.dirname(path)
    for _ in range(40):
        db_path = os.path.join(current, ".repowise", "wiki.db")
        if os.path.isfile(db_path):
            return db_path, os.path.relpath(path, current).replace(os.sep, "/")
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def indexed_symbols(db_path: str, rel: str) -> list[tuple[str, str, int, int]]:
    """(name, kind, start_line, end_line) rows for one indexed file; [] on any error."""
    try:
        con = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=0.5)
        try:
            rows = con.execute(
                "SELECT name, kind, start_line, end_line FROM wiki_symbols WHERE file_path = ?",
                (rel,),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [
        (str(name), str(kind or ""), start, end)
        for name, kind, start, end in rows
        if isinstance(start, int) and isinstance(end, int) and 0 < start <= end
    ]


def _ro_uri(db_path: str) -> str:
    """A read-only sqlite URI for *db_path*, with ``#``, ``?`` and ``%`` escaped."""
    return "file:" + quote(db_path.replace(os.sep, "/"), safe="/:") + "?mode=ro"


def index_mtime(db_path: str) -> float:
    """When the index last changed: the newest of the db and its WAL; 0 on error."""
    times = []
    for path in (db_path, db_path + "-wal"):
        try:
            times.append(os.path.getmtime(path))
        except OSError:
            continue
    return max(times, default=0.0)


def has_indexed_symbols(db_path: str, rel: str) -> bool:
    """True when the index holds at least one symbol for *rel*; False on any error."""
    try:
        con = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=0.2)
        try:
            row = con.execute(
                "SELECT 1 FROM wiki_symbols WHERE file_path = ? LIMIT 1", (rel,)
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return row is not None
