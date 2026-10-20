"""Read a checkout's HEAD commit without spawning git.

``repowise update`` stores this SHA on the repository row, and the MCP
freshness check reads the live HEAD back, on every index and every tool
call. Both go through :func:`read_head_commit` so the value written is the
value compared: a linked worktree, whose ``.git`` is a ``gitdir:`` file
rather than a directory, records its own commit instead of keeping the
main checkout's forever.

No ``git`` subprocess and no GitPython. The callers are hot paths.
"""

from __future__ import annotations

from pathlib import Path


def resolve_git_dir(local_path: str | Path) -> Path | None:
    """The git directory a checkout's ``.git`` names, or ``None``.

    A normal checkout's ``.git`` is that directory. A linked worktree or a
    submodule stores a file, ``gitdir: <path>``; a relative path is resolved
    against the checkout (the directory that holds the file). The returned
    path is not required to exist — a dangling pointer still has a parent,
    which is how the overview remote lookup climbs out of
    ``.git/worktrees/<name>`` — so callers that need a readable directory
    check :meth:`~pathlib.Path.is_dir` themselves.

    ``None`` for a missing ``.git``, a file that is not a ``gitdir:``
    pointer, or an empty path.
    """
    root = Path(local_path)
    git_path = root / ".git"
    if git_path.is_dir():
        return git_path
    if not git_path.is_file():
        return None
    try:
        pointer = git_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    raw = pointer.split(":", 1)[1].strip()
    if not raw:
        return None
    resolved = Path(raw)
    try:
        if not resolved.is_absolute():
            resolved = (root / resolved).resolve()
    except OSError:
        return None
    return resolved


def read_head_commit(local_path: str | Path | None) -> str | None:
    """The checkout's HEAD SHA, or ``None`` when it cannot be resolved.

    Reads ``HEAD`` from :func:`resolve_git_dir`. A detached HEAD is the SHA
    already in that file. A symbolic ``ref:`` is resolved against the common
    directory — ``commondir`` (relative to the gitdir) when the file exists,
    otherwise the gitdir itself, which is a normal checkout and a submodule.
    The loose ref is tried first, then ``packed-refs`` (``#`` comments and
    ``^`` peeled lines skipped). One ref is followed, never a chain.

    Unknown layouts and unresolvable refs return ``None`` rather than a
    guess. An empty *local_path* returns ``None`` without touching the
    filesystem.
    """
    if not local_path:
        return None
    try:
        git_dir = resolve_git_dir(local_path)
        if git_dir is None or not git_dir.is_dir():
            return None
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref: "):
        return _resolve_symbolic_ref(git_dir, head[5:].strip())
    return head or None


def _common_dir(git_dir: Path) -> Path:
    """Where shared refs live for *git_dir*.

    A linked worktree records this in ``commondir`` (``../..`` back to the
    main ``.git``). The path is relative to the gitdir. No file, or an empty
    one, means the gitdir is its own common dir.
    """
    try:
        raw = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return git_dir
    if not raw:
        return git_dir
    path = Path(raw)
    if path.is_absolute():
        return path
    return git_dir / path


def _resolve_symbolic_ref(git_dir: Path, ref_rel: str) -> str | None:
    """The SHA *ref_rel* names under *git_dir*'s common dir, or ``None``.

    An empty loose ref is an answer (``None``), not a miss: packed-refs is
    only the fallback when the loose file cannot be read.
    """
    common = _common_dir(git_dir)
    try:
        return (common / ref_rel).read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    try:
        packed = (common / "packed-refs").read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in packed.splitlines():
        if raw.startswith("#") or raw.startswith("^"):
            continue
        sha, _, name = raw.partition(" ")
        if name.strip() == ref_rel:
            return sha.strip() or None
    return None
