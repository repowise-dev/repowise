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

import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Checkout:
    """A git working tree: its root and the git directory it shares."""

    root: Path
    common_dir: Path


def find_checkout(path: str | Path) -> Checkout | None:
    """The git checkout that holds *path*, or ``None``.

    Walks up to the nearest ``.git``. A linked worktree counts only when its
    repository registered it (see :func:`_registered_worktree`), so a
    hand-written pointer cannot claim a repository's history. Submodules are
    not registered worktrees and do not resolve. Paths come back resolved,
    so case and separators compare equal under :func:`os.path.normcase`.
    """
    try:
        start = Path(path).resolve()
    except (OSError, ValueError):
        return None
    for candidate in (start, *start.parents):
        if not (candidate / ".git").exists():
            continue
        git_dir = resolve_git_dir(candidate)
        if git_dir is None or not git_dir.is_dir():
            return None
        common = _common_dir(git_dir)
        linked = (candidate / ".git").is_file() or common != git_dir
        if linked and not _registered_worktree(git_dir, common, candidate):
            return None
        try:
            return Checkout(candidate, common.resolve())
        except OSError:
            return None
    return None


def _registered_worktree(git_dir: Path, common: Path, root: Path) -> bool:
    """Whether *root* is a worktree its repository registered.

    Git keeps each linked worktree's state in ``<common>/worktrees/<name>``,
    whose ``gitdir`` file names the worktree's ``.git``. Both halves are
    checked, so neither a pointer file nor a copied git dir can claim a
    repository by itself.
    """
    try:
        registered = same_path(git_dir.parent, common / "worktrees")
    except (OSError, ValueError):
        return False
    return registered and _points_back(git_dir, root)


def _points_back(git_dir: Path, root: Path) -> bool:
    """Whether a linked worktree's git dir names *root*'s ``.git`` as its own."""
    try:
        raw = (git_dir / "gitdir").read_text(encoding="utf-8").strip()
        named = Path(raw) if Path(raw).is_absolute() else git_dir / raw
        return same_path(named.resolve(), (root / ".git").resolve())
    except (OSError, ValueError):
        return False


def same_path(a: str | Path, b: str | Path) -> bool:
    """Equal paths after resolving, ignoring case where the OS does."""
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


@dataclass(frozen=True)
class CheckoutMatch:
    """How a requested path relates to an indexed directory of the same repository.

    ``checkout`` is the requested path's counterpart of the indexed directory:
    the indexed directory itself, or the same place in another worktree.
    ``same_checkout`` is True when it is the indexed directory. ``depth`` ranks
    nested indexed directories; the deepest one that holds the path wins.
    """

    checkout: Path
    same_checkout: bool
    depth: int


def match_checkout(requested: str | Path, indexed: str | Path) -> CheckoutMatch | None:
    """Match *requested* to the indexed directory *indexed*, or ``None``.

    A match needs the same git repository (one common git dir), never a name:
    the path is inside *indexed*, or at the same place inside another worktree
    of it. Outside git, only a path inside *indexed* matches.
    """
    try:
        wanted = Path(requested).resolve()
        base = Path(indexed).resolve()
    except (OSError, ValueError):
        return None
    wanted_co, base_co = find_checkout(wanted), find_checkout(base)
    if wanted_co is None or base_co is None:
        if wanted_co is None and base_co is None and _within(wanted, base):
            return CheckoutMatch(base, True, len(base.parts))
        return None
    if os.path.normcase(str(wanted_co.common_dir)) != os.path.normcase(str(base_co.common_dir)):
        return None
    # Both come from one resolve, so their case already agrees.
    inner = base.relative_to(base_co.root)
    if not _within(wanted, wanted_co.root / inner):
        return None
    same = same_path(wanted_co.root, base_co.root)
    return CheckoutMatch(base if same else wanted_co.root / inner, same, len(inner.parts))


def _relative(path: Path, root: Path) -> Path | None:
    try:
        return Path(os.path.normcase(str(path))).relative_to(os.path.normcase(str(root)))
    except ValueError:
        return None


def _within(path: Path, root: Path) -> bool:
    return _relative(path, root) is not None
