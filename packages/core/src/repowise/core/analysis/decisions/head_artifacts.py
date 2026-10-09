"""Mark a decision whose named files are all gone at HEAD, so it reads as stale.

A decision naming only code that no longer exists describes history, not the
repository. This checks every affected file and every backticked path in the
record's text against the tree at HEAD, after following renames through
:class:`~repowise.core.ingestion.git_indexer.RenameTrail`. Only when *all* of
them are absent is ``artifacts_gone`` set, which :func:`.lifecycle.effective_currency`
reads as ``stale``. It never retires the record: nothing replaced it.

Anything the check cannot judge counts as present: an absolute path, a file
git does not track but the working tree holds, a module split into a package
of the same name, or a shallow clone (a rename below the clone's window would
look like a deletion; ceiling: judging only records whose evidence commits
are inside the window would lift it).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.decisions.reverts import _git_out
from repowise.core.analysis.doc_drift.extractor import extract
from repowise.core.analysis.doc_drift.models import DriftKind
from repowise.core.analysis.doc_drift.resolver import RepoIndex
from repowise.core.git_refs import is_shallow, tracked_paths_at
from repowise.core.ingestion.git_indexer.records import RenameTrail, name_status_path

logger = structlog.get_logger(__name__)

__all__ = ["HeadTree", "apply_head_artifact_check", "artifact_paths"]

_TEXT_FIELDS = ("title", "context", "decision", "rationale")


def artifact_paths(rec: Any) -> list[str]:
    """The files *rec* names plus the backticked paths in its text, deduplicated."""
    try:
        files = [str(p) for p in json.loads(rec.affected_files_json or "[]") if p]
    except ValueError:
        files = []
    text = "\n".join(getattr(rec, f, "") or "" for f in _TEXT_FIELDS)
    tokens = [r.target for r in extract(text, "") if r.kind == DriftKind.PATH]
    return list(dict.fromkeys(p.strip().replace("\\", "/") for p in files + tokens if p.strip()))


class HeadTree:
    """The tracked tree at HEAD, with every rename in history followed forward."""

    def __init__(
        self,
        tracked: Iterable[str],
        renames: Iterable[tuple[str, str]] = (),
        root: Path | None = None,
    ) -> None:
        self._index = RepoIndex.build(frozenset(tracked), {})
        self._root = root
        self._trail = RenameTrail()
        self._moved_from: list[str] = []
        # Newest first, as RenameTrail expects.
        for old, new in renames:
            self._trail.record(old, new)
            self._moved_from.append(old)

    def _here(self, path: str) -> bool:
        return path in self._index.files or path in self._index.dirs

    def present(self, path: str) -> bool:
        """Whether *path* (or what it was renamed to) exists at HEAD."""
        if _absolute(path) or not path.strip("/"):
            return True  # not repo-relative, so not ours to call missing
        path = path.strip("/")
        return self._tracked(path) or self._on_disk(path) or self._moved(path)

    def _tracked(self, path: str) -> bool:
        if self._here(path) or self._here(self._trail.resolve(path)):
            return True
        # A bare file name that still exists somewhere.
        return "/" not in path and path in self._index.by_basename

    def _on_disk(self, path: str) -> bool:
        # Untracked but in the working tree: git's silence is not a deletion.
        return self._root is not None and os.path.exists(self._root / path)

    def _moved(self, path: str) -> bool:
        stem, dot, _ = path.rpartition(".")
        if dot and "/" not in path[len(stem) :] and stem in self._index.dirs:
            return True  # a module split into a package of the same name
        prefix = f"{path}/"
        # A directory whose files were all moved elsewhere.
        return any(
            old.startswith(prefix) and self._here(self._trail.resolve(old))
            for old in self._moved_from
        )

    def all_gone(self, paths: list[str]) -> bool:
        return bool(paths) and not any(self.present(p) for p in paths)


def _absolute(path: str) -> bool:
    return path.startswith("/") or (len(path) > 1 and path[1] == ":")


def _history_renames(repo_path: str) -> list[tuple[str, str]] | None:
    """``(old, new)`` for every rename reachable from HEAD, newest first; None on failure."""
    out = _git_out(
        repo_path,
        "log",
        "-M",
        "--diff-filter=R",
        "--name-status",
        "--relative",
        "--format=",
        "HEAD",
    )
    if out is None:
        return None
    pairs = []
    for line in out.splitlines():
        new, old = name_status_path(line)
        if old:
            pairs.append((old, new))
    return pairs


def _head_tree(root: Path, paths: Iterable[list[str]]) -> HeadTree | None:
    """The tree to judge against, or None when git cannot answer in full."""
    tracked = tracked_paths_at(str(root), "HEAD")  # the commit, not the index
    if not tracked:
        return None  # an empty tree would flag everything
    tree = HeadTree(tracked, root=root)
    # The history walk is paid only when something is missing before renames.
    if not any(tree.all_gone(p) for p in paths):
        return tree
    renames = _history_renames(str(root))
    # Without renames a moved file would read as deleted.
    return None if renames is None else HeadTree(tracked, renames, root)


async def apply_head_artifact_check(
    session: Any, repository_id: str, repo_path: Path | str | None
) -> dict[str, int]:
    """Set ``artifacts_gone`` on every record in *repository_id*. Idempotent."""
    from sqlalchemy import select

    from repowise.core.persistence.models import DecisionRecord

    result = {"gone": 0, "back": 0}
    if not repo_path or not Path(repo_path).exists() or is_shallow(str(repo_path)):
        return result
    query = select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    records = list((await session.execute(query)).scalars().all())
    paths = {rec.id: artifact_paths(rec) for rec in records}
    tree = _head_tree(Path(repo_path), paths.values())
    if tree is None:
        return result

    for rec in records:
        gone = tree.all_gone(paths[rec.id])
        if gone != bool(rec.artifacts_gone):
            rec.artifacts_gone = gone
            result["gone" if gone else "back"] += 1
    if any(result.values()):
        await session.flush()
        logger.info("decisions.head_artifacts", **result)
    return result
