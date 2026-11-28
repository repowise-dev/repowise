"""Small filesystem helpers shared across core and the CLI."""

from __future__ import annotations

import errno
import os
import secrets
import time
from collections.abc import Iterable
from pathlib import Path, PurePath
from typing import Any


def make_temp_file(
    directory: Path | str, *, prefix: str = "", suffix: str = ".tmp"
) -> tuple[int, str]:
    """``tempfile.mkstemp`` in *directory*, raising at once when it refuses new files.

    On Windows ``mkstemp`` reads a ``PermissionError`` as a name taken by a
    directory and tries the next name for as long as ``os.access`` calls the
    directory writable. ``os.access`` reads only the read-only bit, never the
    ACL, so in a directory that denies creating files (a Codex sandbox lets the
    agent write ``~/.claude.json`` but not new files beside it) it tries about
    2**31 names and the command hangs. Random names make a real clash rare, so
    a refusal here is a refusal.
    """
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_BINARY", 0)
    )
    for _attempt in range(100):
        path = os.path.join(directory, f"{prefix}{secrets.token_hex(8)}{suffix}")
        try:
            return os.open(path, flags, 0o600), path
        except FileExistsError:
            continue
    raise FileExistsError(errno.EEXIST, "No usable temporary file name found", str(directory))


def atomic_write_text(
    path: Path,
    content: str,
    *,
    newline: str | None = None,
    fsync: bool = False,
) -> None:
    """Write *content* to *path* atomically via a temp file + rename.

    Readers never observe a truncated or half-written file: the temp file is
    created in the destination directory (same filesystem, so the rename is
    atomic) and either replaces *path* wholesale or is cleaned up on failure.

    With *fsync*, the bytes are on the platter before the rename, so the file
    also survives a power loss rather than only a concurrent read. Off by
    default: it costs a disk round-trip, and most callers here are rewriting
    something they can regenerate.
    """
    parent = path.parent
    fd, tmp = make_temp_file(parent)
    try:
        with open(fd, "w", encoding="utf-8", newline=newline) as f:
            f.write(content)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def replace_with_retry(src: Path | str, dst: Path | str) -> None:
    """``os.replace``, retried while a reader briefly holds *dst* open.

    Windows refuses to replace a file another thread or process has open, and
    the config files written through this are read on every request, so a
    write can land mid-read. Gives up after about half a second.
    """
    for _attempt in range(50):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(0.01)
    os.replace(src, dst)


def relative_files(files: Iterable[str], repo_root: Any) -> list[str]:
    """Repo-relative POSIX paths, in first-seen order; outsiders dropped.

    Idempotent, and that is the point: a path already relative is returned as
    it stands rather than resolved again. Resolving it would make the answer
    depend on the process working directory, which is right only while that
    happens to be the repository root and silently drops every file
    otherwise, so a caller that stores relative paths and one reading rows
    stored absolute before it get the same answer from the same call.

    Compares as text rather than through :mod:`os.path`, because the store it
    serves is written by whichever machine ran the agent: a Windows path has
    to read as a path on any interpreter, not as a relative name that happens
    to begin with a drive letter. The root match is case-insensitive for the
    same reason, and to agree with the ``cwd`` scoping the miners already do;
    the cost is two directories under a case-sensitive filesystem that differ
    only in case, which the miner could not have told apart either.
    """
    import posixpath

    root = str(repo_root).replace("\\", "/").rstrip("/")
    prefix = root.lower() + "/"
    out: list[str] = []
    for f in files:
        if not f:
            continue
        path = posixpath.normpath(f.replace("\\", "/"))
        if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            if not path.lower().startswith(prefix):
                continue
            path = path[len(prefix) :]
        if path == ".." or path.startswith("../"):
            continue
        out.append(path)
    return list(dict.fromkeys(out))


def repo_relative(
    path: str | os.PathLike[str], root: str | os.PathLike[str], *, resolve: bool = False
) -> str | None:
    """POSIX spelling of *path* under *root*, or None when it lies outside.

    Lexical (``Path.relative_to``) by default, so both sides must already be
    in the same form. *resolve* resolves both first, which follows symlinks
    and reads a relative *path* against the working directory. *root* itself
    reads as ".". Separators are the host's: on POSIX a backslash stays a
    character.
    """
    try:
        if resolve:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        p = path if isinstance(path, PurePath) else Path(path)
        return p.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def strip_dir_prefix(path: str, base: str) -> str:
    """POSIX *path* relative to directory *base*; unchanged outside it or when *base* is empty."""
    if base and path.startswith(base + "/"):
        return path[len(base) + 1 :]
    return path


def comparable_path(path: str | None) -> str:
    """*path* with forward slashes and no leading "./"; None reads as ""."""
    cleaned = (path or "").replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned
