"""Process isolation for repository-scale duplicate detection.

The full scan creates millions of short-lived Python objects.  CPython frees
them when the scan finishes, but allocator fragmentation can keep gigabytes of
pages committed in the indexing process.  Running only large scans in a child
lets the operating system reclaim those pages before wiki generation starts.
"""

from __future__ import annotations

import multiprocessing
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.cancellation import check_cancelled

from ..source_reader import disk_source_reader
from .detector import (
    DEFAULT_MIN_LINES,
    DEFAULT_WINDOW_TOKENS,
    DuplicationReport,
    detect_clones,
)
from .limits import DuplicationLimits

# Process startup is needless overhead for the small repositories common in
# unit tests and editor workflows. Repositories above this size are where the
# detector's transient arenas become material compared with the base process.
_ISOLATION_FILE_THRESHOLD = 1_000


@dataclass(frozen=True, slots=True)
class _FileInfo:
    path: str
    abs_path: str
    language: str


@dataclass(frozen=True, slots=True)
class _ParsedFile:
    file_info: _FileInfo


def _worker(
    sender: Any,
    files: list[tuple[str, str, str]],
    git_meta_map: dict[str, dict[str, Any]],
    window_tokens: int,
    min_lines: int,
    limits: DuplicationLimits | None,
    cache_dir: Path | None,
    changed_files: set[str] | None,
) -> None:
    """Run in a fresh interpreter and return only the compact report."""
    try:
        parsed_files = [_ParsedFile(_FileInfo(*file_info)) for file_info in files]
        report = detect_clones(
            parsed_files,
            git_meta_map,
            window_tokens=window_tokens,
            min_lines=min_lines,
            limits=limits,
            cache_dir=cache_dir,
            changed_files=changed_files,
        )
        sender.send((True, report))
    except BaseException:
        sender.send((False, traceback.format_exc()))
    finally:
        sender.close()


def detect_clones_with_isolation(
    parsed_files: list[Any],
    git_meta_map: dict[str, dict[str, Any]] | None = None,
    *,
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
    min_lines: int = DEFAULT_MIN_LINES,
    limits: DuplicationLimits | None = None,
    cache_dir: Path | None = None,
    changed_files: set[str] | None = None,
    source_reader: Any | None = None,
    isolation_file_threshold: int = _ISOLATION_FILE_THRESHOLD,
) -> DuplicationReport:
    """Detect clones, isolating repository-scale transient allocations.

    Only the three file-info strings used by the detector cross the process
    boundary. The full parsed AST/model graph remains in the parent, and the
    child returns the comparatively small final report.

    Isolation is taken only for a working-tree read, which is the case it was
    measured on and the only one whose reader survives a spawn. Any other
    *source_reader* runs the detector in process; see the comment below.
    """
    # A reader that is not the working tree cannot cross the spawn boundary.
    # ``MappingSourceReader`` holds every file's bytes, so shipping it would
    # send the exact memory this isolation exists to avoid; and letting the
    # worker fall back to disk would report working-tree findings as the
    # revision's, which that reader documents as the thing it must not do.
    # Those callers (the base side of a diff, a historical commit) analyse a
    # changed-file set, not a repository, so the bound is not what they need.
    reader_is_working_tree = source_reader is None or source_reader is disk_source_reader

    if len(parsed_files) < isolation_file_threshold or not reader_is_working_tree:
        return detect_clones(
            parsed_files,
            git_meta_map,
            window_tokens=window_tokens,
            min_lines=min_lines,
            limits=limits,
            cache_dir=cache_dir,
            changed_files=changed_files,
            source_reader=source_reader,
        )

    files = [
        (pf.file_info.path, str(pf.file_info.abs_path), pf.file_info.language)
        for pf in parsed_files
    ]
    # Clone weighting only reads this field. Avoid serializing the much larger
    # Git metadata records into the worker.
    compact_git_meta = {
        path: {"co_change_partners_json": meta.get("co_change_partners_json")}
        for path, meta in (git_meta_map or {}).items()
        if meta.get("co_change_partners_json")
    }

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker,
        args=(
            sender,
            files,
            compact_git_meta,
            window_tokens,
            min_lines,
            limits,
            cache_dir,
            changed_files,
        ),
        name="repowise-duplication",
    )
    try:
        process.start()
    except BaseException:
        receiver.close()
        sender.close()
        raise
    sender.close()

    try:
        while not receiver.poll(0.1):
            check_cancelled()
            if not process.is_alive():
                process.join()
                raise RuntimeError(
                    f"duplication worker exited without a result (exit code {process.exitcode})"
                )
        try:
            succeeded, result = receiver.recv()
        except EOFError as exc:
            raise RuntimeError(
                f"duplication worker closed without a result (exit code {process.exitcode})"
            ) from exc
        process.join()
        if not succeeded:
            raise RuntimeError(f"duplication worker failed:\n{result}")
        return result
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
