"""Repository, workspace and ``.repowise/`` path resolution."""

from __future__ import annotations

from pathlib import Path

STATE_FILENAME = "state.json"
REPOWISE_DIR = ".repowise"

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_repo_path(path: str | None) -> Path:
    """Resolve the repository root path from a CLI argument.

    If *path* is ``None``, defaults to the current working directory.
    Always returns an absolute, resolved ``Path``.
    """
    if path is None:
        return Path.cwd().resolve()
    return Path(path).resolve()


def find_repowise_repo_root(start: Path | None = None) -> Path | None:
    """Walk upward from *start* looking for a repo with ``.repowise``."""

    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()
    for candidate in (current, *current.parents):
        if _same_path(candidate, home):
            return None
        if (candidate / REPOWISE_DIR).is_dir():
            return candidate
    return None


#: Set to ``0`` (or false/no/off) to keep a worktree from reading its main
#: checkout's index.
WORKTREE_FALLBACK_ENV = "REPOWISE_WORKTREE_FALLBACK"


def borrowed_index_base(checkout: Path) -> Path | None:
    """The main checkout whose index a linked worktree with none of its own reads.

    *checkout* is the worktree's top level. ``None`` unless it is a linked
    worktree of a main checkout that has ``.repowise/wiki.db``, or when
    :data:`WORKTREE_FALLBACK_ENV` turns the fallback off. The caller checks
    that *checkout* has no ``.repowise/`` of its own: a seeded worktree reads
    its own index. Read-only use: nothing that writes the index (update,
    init, coverage ingest) may run against the returned store.

    No subprocess: hooks call this on their hot path, and the ``git_head``
    import is paid only when *checkout* really has a ``.git`` file.
    """
    import os

    if os.environ.get(WORKTREE_FALLBACK_ENV, "").strip().lower() in ("0", "false", "no", "off"):
        return None
    if not (checkout / ".git").is_file():
        return None
    from repowise.core.git_head import linked_worktree_base

    base = linked_worktree_base(checkout)
    if base is None or not (base / REPOWISE_DIR / "wiki.db").is_file():
        return None
    return base


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) looking for ``.repowise-workspace.yaml``.

    Returns the directory containing the file, or ``None`` if not found.
    Delegates to :func:`repowise.core.workspace.config.find_workspace_root`.
    """
    from repowise.core.workspace.config import find_workspace_root as _find

    return _find(start)


def get_repowise_dir(repo_path: Path) -> Path:
    """Return the ``.repowise/`` directory for a given repo root."""
    return repo_path / REPOWISE_DIR


def user_global_dir() -> Path:
    """Return the user-global ``~/.repowise`` dir (created), for cross-repo state.

    Home to machine-wide, repo-independent artifacts: the cached web bundle, the
    PyPI update-check cache, and the last-seen release marker. Distinct from a
    repo's local ``.repowise/`` store.
    """
    d = Path.home() / REPOWISE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_repowise_dir(repo_path: Path) -> Path:
    """Create the ``.repowise/`` directory if it does not exist and return it."""
    d = get_repowise_dir(repo_path)
    d.mkdir(parents=True, exist_ok=True)
    return d
