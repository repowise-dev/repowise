"""Splicing a duplication state the repo-wide window budget cut short.

A full run collects files in order and stops at the first one whose windows
would overrun the budget; it never reads the files after it. A splice
reproduces that run only when the cut falls on the same file: every unchanged
file keeps its side of the cut, the stop file is unchanged, and the edited
prefix still leaves too little room for the stop file's windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BudgetCut:
    """Where collection stopped: the stop file, and the paths after it."""

    stop: Any
    unread: frozenset[str]


def cut_of_run(paths: list[str], files_considered: int, budget_hit: bool) -> tuple[str | None, set[str]]:
    """The stop path and unread paths of a full run over *paths* in order.

    Collection counts the stop file as considered before it breaks, so it is
    the last considered path.
    """
    if not budget_hit or files_considered < 1:
        return None, set()
    return paths[files_considered - 1], set(paths[files_considered:])


def splice_scope(
    index: Any, parsed_files: list[Any], changed: set[str]
) -> tuple[list[Any], BudgetCut | None] | None:
    """The files a splice reads and the cut it must keep; ``None`` when the cut moved.

    An uncut state reads every file. For a cut state, an edited or new file
    after the stop is not read, as the full run would not read it.
    """
    stop_path = index.budget_stop
    if stop_path is None:
        return parsed_files, None
    paths = [pf.file_info.path for pf in parsed_files]
    if stop_path not in paths or stop_path in changed:
        return None
    pos = paths.index(stop_path)
    known = set(index.files) | index.nonsurvivors
    for i, path in enumerate(paths):
        if path in changed or path not in known:
            continue
        if (i > pos) != (path in index.unread):
            return None
    return parsed_files[:pos], BudgetCut(parsed_files[pos], frozenset(paths[pos + 1 :]))


def cut_holds(prefix_windows: int, stop_windows: int, stop_alone_overflows: bool, limit: int) -> bool:
    """Whether a run whose prefix holds *prefix_windows* still stops at the stop file."""
    if prefix_windows > limit:
        return False
    return stop_alone_overflows or prefix_windows + stop_windows > limit
