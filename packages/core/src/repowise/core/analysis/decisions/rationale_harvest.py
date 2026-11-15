"""Every tracked file's rationale comments, kept beside the index per git blob.

A question naming no file is answered from the comments of every tracked code
file. Reading them all at query time cost tens of seconds on a cold disk, and
again on every new HEAD. Each index write refreshes ``rationale_harvest.json``
instead, re-reading only the files whose blob changed, and a query reads only
the files whose blob changed since that write. Without the file (an index built
before it existed) the query reads every file, as it always did.

One entry per file serves both query modes: the marker mode takes the causal
rows, the plain mode takes every row of a production file.

Ceiling: a file edited in the working tree after the index write is served from
its stored rows until its blob changes. A file dirty at write time is not
stored, so the query reads it live.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from repowise.core.analysis.decisions.rationale_comments import (
    CODE_EXTENSIONS,
    harvest_file_rationale,
)
from repowise.core.code_origin import code_origin
from repowise.core.exclusion import build_exclude_spec, is_excluded

_log = logging.getLogger(__name__)

_FILENAME = "rationale_harvest.json"
_VERSION = 1
#: Modules whose code shapes a stored entry; an edit to any re-harvests every file.
#: Ceiling: code_origin's language registry lookups are not hashed.
HARVEST_INPUTS: tuple[str, ...] = (
    "analysis/decisions/rationale_comments.py",
    "analysis/decisions/rationale_harvest.py",
    "code_origin.py",
    "support_paths.py",
    "test_paths.py",
)
_MAX_FILE_LINES = 8000
_MAX_HARVEST_FILES = 20_000
# The marker mode's per-file cap, harvest_file_rationale's default.
_MARKER_ROWS_PER_FILE = 3
_GIT_TIMEOUT_S = 20

# (blob, production, rows); a row is [start, end, text, strength, causal].
Entry = tuple[str, bool, list[list[Any]]]


def read_repo_text(repo_root: Path, file_path: str) -> str | None:
    """Read a repo file's live text, refusing paths outside the root and giant files."""
    try:
        root = repo_root.resolve()
    except OSError:
        return None
    return _read_under(root, file_path)


def _read_under(root: Path, file_path: str) -> str | None:
    """:func:`read_repo_text` for an already resolved *root*."""
    try:
        abs_path = (root / file_path).resolve()
        abs_path.relative_to(root)
    except (ValueError, OSError):
        return None
    try:
        if abs_path.stat().st_size > _MAX_FILE_LINES * 400:
            return None
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _git(root: Path, *args: str) -> bytes | None:
    try:
        proc = subprocess.run(
            # stdin=DEVNULL: this runs inside a stdio MCP server whose stdin is the protocol.
            ["git", "--no-pager", *args],
            cwd=str(root),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _tracked_blobs(root: Path) -> list[tuple[str, str]] | None:
    """``(path, blob id)`` per tracked file in index order, or None outside git."""
    out = _git(root, "ls-files", "-s", "-z")
    if out is None:
        return None
    seen: dict[str, str] = {}
    for record in out.decode("utf-8", "replace").split("\0"):
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if path and len(fields) >= 2:
            seen.setdefault(path, fields[1])
    return list(seen.items())[:_MAX_HARVEST_FILES]


def _code_paths(root: Path, blobs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    spec = build_exclude_spec(root)
    return [
        (path, blob)
        for path, blob in blobs
        if "." in path
        and path.rsplit(".", 1)[-1].lower() in CODE_EXTENSIONS
        and not is_excluded(path, spec)
    ]


def _harvest_file(root: Path, path: str, blob: str) -> Entry | None:
    """The stored entry for one file under the resolved *root*, serving both modes."""
    text = _read_under(root, path)
    if text is None:
        return None
    ext = path.rsplit(".", 1)[-1].lower()
    production = code_origin(path, text) == "production"
    harvested = harvest_file_rationale(
        text, ext, max_per_file=_MAX_FILE_LINES, require_marker=False
    )
    # A non-production file serves only the marker mode, which takes causal rows.
    rows = [
        [h.start_line, h.end_line, h.text, h.strength, h.causal]
        for h in harvested
        if production or h.causal
    ]
    return blob, production, rows


def _live_rows(root: Path, path: str, plain: bool) -> list[dict]:
    """One file's served rows read now, harvesting only what *plain* serves."""
    text = _read_under(root, path)
    if text is None:
        return []
    ext = path.rsplit(".", 1)[-1].lower()
    if not plain:
        harvested = harvest_file_rationale(text, ext)
    elif code_origin(path, text) == "production":
        harvested = harvest_file_rationale(
            text, ext, max_per_file=_MAX_FILE_LINES, require_marker=False
        )
    else:
        return []
    return [{"path": path, "lines": [h.start_line, h.end_line], "comment": h.text} for h in harvested]


def _store_path(root: Path) -> Path:
    return root / ".repowise" / _FILENAME


def _fingerprint() -> str:
    from repowise.core.analysis.health.source_fingerprint import source_fingerprint

    # The harvest is regex-only; listing installed parsers costs a third of a second.
    return source_fingerprint(HARVEST_INPUTS, f"v{_VERSION}", parser_dependencies=False)


def _load(root: Path) -> dict[str, Entry]:
    """The stored entries, or ``{}`` when absent, unreadable or from other code."""
    try:
        data = json.loads(_store_path(root).read_text(encoding="utf-8"))
        if data.get("fingerprint") != _fingerprint():
            return {}
        return {path: (e[0], bool(e[1]), e[2]) for path, e in data["files"].items()}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        # Corrupt or foreign: harvest live.
        _log.debug("rationale harvest store unreadable: %s", exc)
        return {}


def _dirty_paths(root: Path) -> set[str]:
    # --relative: paths from *root*, as ls-files gives them when *root* is a subdirectory.
    out = _git(root, "diff", "--name-only", "--relative", "-z")
    return set(out.decode("utf-8", "replace").split("\0")) if out else set()


def _refreshed_entries(root: Path, blobs: list[tuple[str, str]]) -> tuple[dict, dict]:
    """``(stored, refreshed)``: unchanged blobs keep their entry, changed ones are read.

    A file dirty in the working tree is left out, so a query reads it live.
    """
    old = _load(root)
    dirty = _dirty_paths(root)
    files: dict[str, Entry] = {}
    for path, blob in _code_paths(root, blobs):
        if path in dirty:
            continue
        prev = old.get(path)
        entry = prev if prev and prev[0] == blob else _harvest_file(root, path, blob)
        if entry is not None:
            files[path] = entry
    return old, files


def refresh_rationale_store(repo_path: Path | str) -> None:
    """Bring ``rationale_harvest.json`` up to the working tree, reading changed blobs only.

    Runs after each committed index write. Best-effort: never raises.
    """
    try:
        root = Path(repo_path).resolve()
        blobs = _tracked_blobs(root) if (root / ".repowise").is_dir() else None
        if blobs is None:
            return
        old, files = _refreshed_entries(root, blobs)
        if files == old and _store_path(root).is_file():
            return
        from repowise.core.fsutils import atomic_write_text

        payload = {"fingerprint": _fingerprint(), "files": files}
        atomic_write_text(_store_path(root), json.dumps(payload, separators=(",", ":")))
    # Broad on purpose: the index is already committed, and a harvest bug must
    # not fail the write that called it. The query then harvests live.
    except Exception as exc:
        _log.debug("rationale harvest refresh failed: %s", exc)


def _served_rows(path: str, entry: Entry, plain: bool) -> list[dict]:
    _, production, rows = entry
    if plain:
        picked = rows if production else []
    else:
        picked = [r for r in rows if r[4]][:_MARKER_ROWS_PER_FILE]
    return [{"path": path, "lines": [r[0], r[1]], "comment": r[2]} for r in picked]


def harvest_repo_rationale(repo_root: Path, plain: bool = False) -> list[dict]:
    """Every tracked code file's rationale comments, strongest first per file.

    Marker mode keeps a file's strongest three comments that carry a causal
    marker, docstrings skipped: the bar a comment must clear to be served for a
    question naming no file. *plain* keeps every production comment, marked or
    not, for a store with no ruling. Stored entries serve unchanged blobs.
    """
    repo_root = repo_root.resolve()
    blobs = _tracked_blobs(repo_root)
    if blobs is None:
        return []
    stored = _load(repo_root)
    rows: list[dict] = []
    for path, blob in _code_paths(repo_root, blobs):
        entry = stored.get(path)
        if entry is not None and entry[0] == blob:
            rows.extend(_served_rows(path, entry, plain))
        else:
            rows.extend(_live_rows(repo_root, path, plain))
    return rows
