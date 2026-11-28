"""The content baseline: what ``update`` diffs against when there is no commit.

``last_sync_commit`` lets ``update`` diff two commits. A plain directory, an
exported tree or a workspace of another version control system has no commit
to record, so the index records each walked file's content fingerprint instead
(``{path: fingerprint}`` in ``.repowise/content_baseline.json``). The next
update walks and hashes again; the paths whose fingerprint moved, appeared or
vanished are its change set.

The snapshot is taken before a run reads the files and saved only after the
run has persisted, so an edit racing the run, or a run that fails, leaves the
baseline older than the index: the next update detects those files again and
never misses them. It is kept only while there is no commit: the first update
that finds one records ``last_sync_commit`` and drops the file.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "advance_baseline",
    "drop_baseline",
    "load_baseline",
    "save_baseline",
    "snapshot",
    "snapshot_digest",
]

_FILENAME = "content_baseline.json"
_VERSION = 1


def snapshot(
    repo_path: Path,
    *,
    exclude_patterns: list[str] | None = None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
) -> dict[str, str]:
    """``{path: fingerprint}`` for every file the index walk would read now.

    Reads and hashes every walked file on each call. Mtimes are not trusted
    (an export or a sync may stamp a file with its source's time); a size and
    mtime pre-check is the upgrade path if hashing shows up on a very large tree.
    """
    from .document_extractors import source_fingerprint
    from .traverser import FileTraverser

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )

    def _fingerprint(fi) -> tuple[str, str] | None:
        try:
            return fi.path, source_fingerprint(fi, Path(fi.abs_path).read_bytes())
        except OSError:
            # Unreadable now: absent from the snapshot, so it reads as deleted.
            return None

    workers = min(32, max(4, (os.cpu_count() or 4) * 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pairs = pool.map(_fingerprint, list(traverser.traverse()))
        return dict(pair for pair in pairs if pair is not None)


def snapshot_digest(files: dict[str, str]) -> str:
    """One id for a snapshot: what a per-commit stamp keys on when there is no commit."""
    canonical = json.dumps(files, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8", "surrogatepass"))
    return f"content:{digest.hexdigest()}"


def _path(repo_path: Path) -> Path:
    return Path(repo_path) / ".repowise" / _FILENAME


def load_baseline(repo_path: Path) -> dict[str, str] | None:
    """The saved baseline, or ``None`` when there is none or it cannot be read."""
    try:
        payload = json.loads(_path(repo_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("content_baseline_unreadable", error=str(exc))
        return None
    if not isinstance(payload, dict) or payload.get("version") != _VERSION:
        return None
    files = payload.get("files")
    return files if isinstance(files, dict) else None


def save_baseline(repo_path: Path, files: dict[str, str]) -> None:
    from repowise.core.fsutils import atomic_write_text

    atomic_write_text(
        _path(repo_path),
        json.dumps({"version": _VERSION, "files": files}, separators=(",", ":"), sort_keys=True),
    )


def drop_baseline(repo_path: Path) -> None:
    _path(repo_path).unlink(missing_ok=True)


def advance_baseline(
    repo_path: Path, head: str | None, files: dict[str, str] | None, *, clean: bool
) -> None:
    """Move the baseline once an update has saved its state.

    *files* is the run's snapshot, ``None`` for a run that diffed commits. A
    run that did not persist everything keeps the old baseline, so the next
    update detects its files again; a clean run with a commit drops it, since
    ``last_sync_commit`` now names that commit.
    """
    if files is None or not clean:
        return
    if head:
        drop_baseline(repo_path)
    else:
        save_baseline(repo_path, files)
