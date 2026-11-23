"""The change's changed lines, scoped to the files the score counted."""

from __future__ import annotations

import asyncio
import subprocess
from functools import partial

from repowise.core.analysis.git_cli import path_filter


def _filter_changed(
    changed: dict[str, set[int]],
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    include_paths: tuple[str, ...] = (),
) -> dict[str, set[int]]:
    """Restrict changed-line files to the score's counted universe.

    ``changed_lines`` applies no suffix/include/exclude filtering, so without
    this a change scored ``nf == 0`` under a filter could still surface impacted
    tests for the filtered-out files. Uses the score's own path rule.
    """
    counts = path_filter(extensions, exclude_patterns, include_paths)
    return {path: lines for path, lines in changed.items() if counts(path)}


async def _changed_in_scope(
    repo_path: str,
    revspec: str | None,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    include_paths: tuple[str, ...] = (),
    *,
    working_tree: bool = False,
) -> tuple[dict[str, set[int]], tuple[str, str] | None]:
    """The change's changed lines, restricted to the score's counted universe.

    Returns ``(changed, error)`` where *error* is a ``(status, summary)`` pair
    for the degraded paths, so the callers below can report the same reason
    without each re-reading git. Shared because ``changed_lines`` shells out and
    two blocks want the same answer.
    """
    changed, error, _label = await _changed_with_label(
        repo_path, revspec, extensions, exclude_patterns, include_paths, working_tree=working_tree
    )
    return changed, error


async def _changed_with_label(
    repo_path: str,
    revspec: str | None,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    include_paths: tuple[str, ...] = (),
    *,
    working_tree: bool = False,
    base: str | None = None,
) -> tuple[dict[str, set[int]], tuple[str, str] | None, str | None]:
    """:func:`_changed_in_scope` plus the label ``changed_lines`` gave what it read.

    *base* widens a working-tree read to the merge-base with it, untracked
    files included, and falls back to the plain working tree (label and all)
    when the base does not resolve.
    """
    from repowise.core.analysis.changed_lines import changed_lines

    label = None
    try:
        changed, label = await asyncio.to_thread(
            partial(
                changed_lines,
                repo_path,
                revspec or "HEAD",
                working_tree=working_tree,
                base=base,
                skip_untracked=exclude_patterns,
            )
        )
    except ValueError as exc:
        return {}, ("unknown", f"Could not read changed lines: {exc}"), label
    except (subprocess.SubprocessError, OSError):
        return {}, ("unknown", "Could not read changed lines from git."), label

    changed = _filter_changed(changed, extensions, exclude_patterns, include_paths)
    if not changed:
        return {}, ("no_source_line_changes", "No changed source lines to map to tests."), label
    return changed, None, label


async def _push_change(
    repo_path: str,
    revspec: str | None,
    working_tree: bool,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    scored: tuple[dict[str, set[int]], tuple[str, str] | None],
    include_paths: tuple[str, ...] = (),
) -> tuple[dict[str, set[int]], tuple[str, str] | None, str | None, str | None]:
    """``(changed, error, label, revspec)`` the patch-coverage block measures.

    An explicit *revspec* is measured as given (*scored*, the lines the score
    read). Without one an agent is checking its work before a push, so the
    patch is everything the push brings, the branch's commits and any
    uncommitted edits, and the self-check does not shrink to the last commit
    once it lands: ``<base>...working tree`` when dirty, ``<base>...HEAD``
    when clean. No resolvable base keeps what was scored.
    """
    changed, error = scored
    if revspec is not None:
        return changed, error, None, revspec
    base = await asyncio.to_thread(_push_base, repo_path)
    if not working_tree and base == "HEAD":
        return changed, error, None, revspec
    branch = None if working_tree else f"{base}...HEAD"
    found, found_error, label = await _changed_with_label(
        repo_path,
        branch,
        extensions,
        exclude_patterns,
        include_paths,
        working_tree=working_tree,
        base=base,
    )
    if found_error is not None and branch is not None:
        # No merge-base with the base (a shallow clone): measure the commit as scored.
        return changed, error, None, revspec
    return found, found_error, label, branch


def _push_base(repo_path: str) -> str:
    """The branch a push of this checkout is reviewed against, else ``HEAD``.

    The base ``repowise coverage check`` would diff from in CI, so an agent's
    self-check and the pull request's gate measure the same lines.
    """
    from repowise.core.analysis.change_risk.features import split_revspec
    from repowise.core.ci.base import BaseNotFoundError, default_revspec

    try:
        parts = split_revspec(default_revspec(repo_path))
    except (BaseNotFoundError, subprocess.SubprocessError, OSError):
        return "HEAD"
    return parts[0] if parts else "HEAD"


def _change_base(
    repo_path: str, revspec: str | None, label: str | None, working_tree: bool
) -> str | None:
    """The commit the measured change starts from, ``None`` when git cannot name it.

    A working-tree change starts at the merge-base with its push base when the
    label names one (``<base>...working tree``), else at ``HEAD``.
    """
    from repowise.core import git_refs
    from repowise.core.analysis.change_risk.features import split_revspec

    if not working_tree:
        return git_refs.change_base(repo_path, revspec or "HEAD") or None
    parts = split_revspec(label or "")
    if parts is None:
        return git_refs.resolve(repo_path, "HEAD") or None
    return git_refs.change_base(repo_path, f"{parts[0]}...HEAD") or None
