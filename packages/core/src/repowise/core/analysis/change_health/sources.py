"""Where the bytes of a revision come from.

The comparison engine depends only on :class:`RevisionSource`, so a hosted
GitHub or artifact adapter can be added without touching matching or
attribution. The local adapter reads Git object content directly and never
checks out, resets, or writes to the caller's tree.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..changed_lines import FileDiff, parse_unified_diff

GIT_TIMEOUT_SECONDS = 120

#: Git's rename similarity threshold, as a percentage.
_RENAME_SIMILARITY = 50

#: The empty tree, for diffing a root commit that has no parent.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass(frozen=True, slots=True)
class FileChange:
    """One path's transition across the change."""

    head_path: str | None
    base_path: str | None
    status: str  # added | modified | deleted | renamed
    diff: FileDiff | None = None

    @property
    def is_new(self) -> bool:
        return self.status == "added"

    @property
    def is_rename(self) -> bool:
        return self.status == "renamed"

    @property
    def added_lines(self) -> set[int]:
        return self.diff.new_lines if self.diff else set()


@dataclass(slots=True)
class RevisionPair:
    """The two sides of a change and the paths that differ between them."""

    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str
    working_tree: bool
    changes: list[FileChange] = field(default_factory=list)

    @property
    def head_paths(self) -> list[str]:
        return [c.head_path for c in self.changes if c.head_path]

    def rename_map(self) -> dict[str, str]:
        """``{base_path: head_path}`` for renamed files."""
        return {
            c.base_path: c.head_path
            for c in self.changes
            if c.is_rename and c.base_path and c.head_path
        }


class RevisionSource(Protocol):
    """Reads revision content and change shape without mutating a checkout."""

    def resolve(self, revspec: str | None) -> RevisionPair:
        """Identify both sides and the paths that differ."""
        ...

    def read(self, sha: str, paths: list[str]) -> dict[str, bytes]:
        """Bulk-read *paths* as they exist at *sha*. Missing paths are absent."""
        ...

    def read_working_tree(self, paths: list[str]) -> dict[str, bytes]:
        """Bulk-read *paths* from the uncommitted tree."""
        ...


def _git(args: list[str], repo_path: str, *, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if check and proc.returncode != 0:
        raise ValueError((proc.stderr or "").strip() or f"git {args[0]} failed")
    return proc.stdout


def _status_word(code: str) -> str:
    return {"A": "added", "D": "deleted", "R": "renamed", "M": "modified"}.get(code[:1], "modified")


class GitRevisionSource:
    """Local adapter over a Git checkout."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path

    # -- resolution ---------------------------------------------------------

    def resolve(self, revspec: str | None) -> RevisionPair:
        if not revspec:
            return self._resolve_working_tree()
        if ".." in revspec:
            return self._resolve_range(revspec)
        return self._resolve_commit(revspec)

    def _sha(self, ref: str) -> str:
        out = _git(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            self.repo_path,
            check=False,
        ).strip()
        if not out:
            raise ValueError(f"Could not resolve {ref!r} to a commit.")
        return out

    def _resolve_working_tree(self) -> RevisionPair:
        head = self._sha("HEAD")
        return RevisionPair(
            "HEAD",
            "WORKTREE",
            head,
            "",
            True,
            self._changes(["diff", f"-M{_RENAME_SIMILARITY}%", "HEAD"]),
        )

    def _resolve_commit(self, ref: str) -> RevisionPair:
        head = self._sha(ref)
        parent = _git(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}^"],
            self.repo_path,
            check=False,
        ).strip()
        base = parent or _EMPTY_TREE
        return RevisionPair(
            f"{ref}^" if parent else _EMPTY_TREE,
            ref,
            base,
            head,
            False,
            self._changes(["diff", f"-M{_RENAME_SIMILARITY}%", base, head]),
        )

    def _resolve_range(self, revspec: str) -> RevisionPair:
        base, _, head = revspec.partition("..")
        three_dot = head.startswith(".")
        head = head.lstrip(".") or "HEAD"
        base = base or "HEAD"
        base_sha = self._sha(base)
        head_sha = self._sha(head)
        if three_dot:
            base_sha = (
                _git(["merge-base", base, head], self.repo_path, check=False).strip() or base_sha
            )
        return RevisionPair(
            base,
            head,
            base_sha,
            head_sha,
            False,
            self._changes(["diff", f"-M{_RENAME_SIMILARITY}%", base_sha, head_sha]),
        )

    # -- change shape -------------------------------------------------------

    def _changes(self, diff_args: list[str]) -> list[FileChange]:
        name_status = _git([*diff_args, "--name-status", "-z"], self.repo_path)
        diffs = parse_unified_diff(_git([*diff_args, "--unified=0", "--format="], self.repo_path))
        changes: list[FileChange] = []
        for code, base_path, head_path in _iter_name_status(name_status):
            status = _status_word(code)
            key = head_path or base_path
            changes.append(
                FileChange(
                    head_path=None if status == "deleted" else head_path,
                    base_path=None if status == "added" else base_path,
                    status=status,
                    diff=diffs.get(key) if key else None,
                )
            )
        return changes

    # -- content ------------------------------------------------------------

    def read(self, sha: str, paths: list[str]) -> dict[str, bytes]:
        """Read every path at *sha* in one ``git cat-file --batch`` pass."""
        if not paths:
            return {}
        return _cat_file_batch(self.repo_path, sha, paths)

    def read_working_tree(self, paths: list[str]) -> dict[str, bytes]:
        root = Path(self.repo_path)
        out: dict[str, bytes] = {}
        for path in paths:
            try:
                out[path] = (root / path).read_bytes()
            except OSError:
                continue
        return out


def _iter_name_status(raw: str) -> Iterator[tuple[str, str, str]]:
    """Yield ``(code, base_path, head_path)`` from ``--name-status -z`` output."""
    fields = [f for f in raw.split("\0") if f != ""]
    i = 0
    while i < len(fields):
        code = fields[i]
        if code.startswith("R") and i + 2 < len(fields):
            yield code, fields[i + 1], fields[i + 2]
            i += 3
        elif i + 1 < len(fields):
            yield code, fields[i + 1], fields[i + 1]
            i += 2
        else:
            break


def _cat_file_batch(repo_path: str, sha: str, paths: list[str]) -> dict[str, bytes]:
    """One batch read; an object missing at *sha* is omitted rather than raising."""
    specs = "\n".join(f"{sha}:{path}" for path in paths) + "\n"
    proc = subprocess.run(
        ["git", "-C", repo_path, "cat-file", "--batch"],
        input=specs.encode("utf-8"),
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        return {}
    out: dict[str, bytes] = {}
    data, cursor = proc.stdout, 0
    for path in paths:
        newline = data.find(b"\n", cursor)
        if newline == -1:
            break
        header = data[cursor:newline]
        # A missing object echoes the whole spec back: "<sha>:<path> missing".
        # Match the suffix, not the field count, because a path may hold spaces.
        if header.endswith(b" missing"):
            cursor = newline + 1
            continue
        fields = header.decode("utf-8", "replace").split()
        if len(fields) < 3 or not fields[2].isdigit():
            break  # unparseable header: stop rather than misread the stream
        size = int(fields[2])
        start = newline + 1
        out[path] = data[start : start + size]
        cursor = start + size + 1  # trailing newline
    return out
