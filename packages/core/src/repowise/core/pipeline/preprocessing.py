"""Scope automatic working-tree refreshes to plugin-owned generated files."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def preprocessed_changes(
    detector: Any, paths: Iterable[str], previous_paths: Iterable[str]
) -> tuple[list[Any], list[str]]:
    managed = set(paths)
    previous = set(previous_paths)
    if not managed and not previous:
        return [], []
    diffs = [
        diff
        for diff in detector.get_working_tree_changes()
        if diff.path in managed or diff.path in previous
    ]
    dirty = {diff.path for diff in diffs}
    missing = {path for path in managed if not (detector.repo_path / path).is_file()}
    diffs += detector.stale_working_tree_diffs(previous | missing, dirty)
    return diffs, sorted(dirty)
