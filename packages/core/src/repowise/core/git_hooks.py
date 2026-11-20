"""Where git looks for this repository's hooks, and whether the sync hook is there.

Read-only: ``repowise.cli.hooks`` writes the hooks and imports these, so the
CLI, the server and the MCP server answer "is sync on" from the same reads.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: The opening marker of the post-commit block ``repowise hook install`` writes.
SYNC_HOOK_MARKER = "# repowise-hook-start"


def git_root(path: Path) -> Path | None:
    """Walk up to find .git directory."""
    current = path.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def hooks_dir(repo_path: Path) -> Path | None:
    """Resolve the real hooks directory for *repo_path*.

    A normal repo keeps ``.git/hooks`` under ``.git``, but a git **worktree**
    stores ``.git`` as a *file* (``gitdir: <path>``) pointing at the shared
    metadata dir — so ``root / ".git" / "hooks"`` is ``NotADirectoryError``
    territory. Ask git for the real hooks path so both layouts work, and fall
    back to the ``.git/hooks`` heuristic when git is unavailable or not a repo.

    ``git rev-parse --git-path hooks`` also honours ``core.hooksPath``, which
    husky and lefthook both set. A *global* ``core.hooksPath`` (say
    ``~/.githooks``) resolves outside any repo, so the hook is shared:
    ``status`` then reports installed from every repo, and ``uninstall`` run
    in one repo removes it for all of them. Following the config is still
    correct, because that directory is genuinely where git looks, and the
    hook body is repo-scoped at runtime -- it resolves its own ``$ROOT`` and
    returns early unless ``$ROOT/.repowise`` exists -- so a shared hook is
    inert in repos with no index rather than wrong.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            p = Path(result.stdout.strip())
            if not p.is_absolute():
                p = repo_path / p
            return husky_user_hook_dir(p)
    except Exception:
        pass
    root = git_root(repo_path)
    if root is None:
        return None
    return root / ".git" / "hooks"


def husky_user_hook_dir(hooks_dir: Path) -> Path:
    """Redirect husky's generated shim directory to its user-hook directory.

    husky points ``core.hooksPath`` at ``.husky/_``, which it regenerates on
    every install and gitignores wholesale (``.husky/_/.gitignore`` is ``*``), so
    a hook written there is deleted by the next ``npm install``. husky's shim
    dispatches to ``.husky/<hook-name>`` one level up, which is the committed,
    durable location.

    Dispatch does not depend on which user hooks already exist. husky writes a
    shim for a fixed list of all 14 git hook names on every install, regardless
    of what is in ``.husky/`` (husky 9.1.7 ``index.js``: the ``l`` array includes
    ``post-commit``, and ``l.forEach`` writes each one unconditionally), and each
    shim sources ``_/h``, which execs ``.husky/<name>`` when that file exists and
    exits 0 when it does not. So a hook written here is picked up immediately
    rather than waiting for the next ``npm install``.

    The exception is a checkout where husky has never run: ``.husky/_`` does not
    exist, ``core.hooksPath`` points at a missing directory, and git therefore
    runs no hooks at all -- husky's own included. Writing to ``.husky/`` is still
    the right destination, since it survives, but nothing fires until husky is
    installed. :func:`husky_pending_reason` reports that so it is visible at
    install time instead of looking like a working hook.
    """
    if hooks_dir.name != "_":
        return hooks_dir
    # Only remap a directory that really is husky's, not any directory named
    # "_". The generated helpers are the strongest signal, but they are absent
    # in a fresh worktree where husky has not been installed yet; there the
    # parent being a ``.husky`` directory is what identifies the layout.
    is_husky = any((hooks_dir / marker).exists() for marker in ("h", "husky.sh"))
    if not (is_husky or hooks_dir.parent.name == ".husky"):
        return hooks_dir
    return hooks_dir.parent


def sync_hook_installed(repo_path: Path) -> bool | None:
    """Whether the post-commit hook carries the sync block; ``None`` outside a git repo.

    One git call and one file read. Says nothing about whether the hook can
    run (``repowise hook status`` and doctor say that).
    """
    if git_root(repo_path) is None:
        return None
    found = hooks_dir(repo_path)
    if found is None:
        return None
    try:
        return SYNC_HOOK_MARKER in (found / "post-commit").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError):
        return None
