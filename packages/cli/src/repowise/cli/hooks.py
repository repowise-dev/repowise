"""Git hook management for repowise auto-sync and the opt-in security check.

Installs/uninstalls a post-commit hook that runs ``repowise update`` in the
background after every commit, keeping the wiki in sync automatically, and on
request a pre-commit block that runs ``repowise security check --staged``.

Each block uses start/end markers so it can safely coexist with other hooks
in the same file (a lint hook, another tool's index hook).
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import subprocess
from pathlib import Path

_HOOK_MARKER = "# repowise-hook-start"
_HOOK_MARKER_END = "# repowise-hook-end"

# Fingerprints of pre-marker legacy hook bodies. When ``install`` is called
# over the top of a file containing these, we strip the legacy block rather
# than appending a second copy. The legacy block was unreachable due to a
# trailing ``exit 0`` and on Windows would fail every commit because
# ``uv run repowise update`` rebuilt the venv from a fresh resolve.
#
# DO NOT add fingerprints from *marker-bracketed* old hook bodies here —
# those are handled by ``_replace_marker_block`` instead. The strip path
# below walks until it finds ``exit 0``, which marker-bracketed bodies
# don't have, so a fingerprint match against them would over-delete and
# clobber unrelated hook content past the marker.
_LEGACY_HOOK_FINGERPRINTS = (
    "[repowise] Triggering incremental wiki update",
    "/tmp/repowise-update.log",
)

# Post-commit hook contract. Works cross-platform because git always runs
# hooks under a POSIX shell (``/bin/sh`` on Linux/macOS, git-bash on
# Windows), so the same script body is correct everywhere — no platform
# detection needed.
#
# Two responsibilities:
#
#   1. Drop a ``.update.queued`` marker *before* backgrounding the update.
#      Closes the race window where the agent-side augment hook would
#      otherwise warn "wiki is stale" during the ~1–5 second start-up of
#      ``repowise update`` (Python import, DB open) before the real lock
#      file lands on disk. The marker is read by ``augment_cmd`` and
#      treated identically to a held lock.
#
#   2. Capture the update's output to ``.repowise/.update.log``. Previously
#      the hook piped to ``/dev/null``, which made silent failures
#      impossible to diagnose — the symptom was just "state never moves".
#      The log is appended; ``update_cmd`` truncates it on its next run
#      via ``rotate_update_log_if_needed`` so it can't grow without bound.
#
# The outer ``{ ... } &`` brace group ensures the queued marker is written
# synchronously (so the augment hook sees it on the *next* tool call after
# the commit) before the heavy update spawns into the background.
#
# Two gates keep a default-on hook boring. It fires only when
# ``.repowise/state.json`` exists, which is the precondition ``repowise
# update`` itself checks, so a dry-run directory or a deleted store does not
# fail on every commit forever. And when ``repowise`` is not on PATH it looks
# only in the repo's own ``.venv``; the old ``uv run`` fallback resolved
# whatever project ``uv`` found above the repo, on every commit, in repos
# that were not Python projects at all.
_HOOK_SCRIPT = """\
# repowise-hook-start
# Auto-syncs repowise wiki after each commit (background, non-blocking).
# Installed by: repowise hook install
{
  ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
  [ -f "$ROOT/.repowise/state.json" ] || exit 0
  HEAD=$(git rev-parse HEAD 2>/dev/null) || HEAD=""
  TS=$(date +%s 2>/dev/null) || TS=""
  if [ -n "$TS" ]; then
    printf '{"target_commit":"%s","queued_at":%s}\\n' "$HEAD" "$TS" \\
      > "$ROOT/.repowise/.update.queued" 2>/dev/null || true
  fi
  LOG="$ROOT/.repowise/.update.log"
  {
    printf '\\n--- post-commit hook fired at %s for HEAD %s ---\\n' \\
      "$(date 2>/dev/null)" "$HEAD"
  } >> "$LOG" 2>/dev/null || true
  (
    cd "$ROOT" || exit 1
    if command -v repowise >/dev/null 2>&1; then
      repowise update >> "$LOG" 2>&1
    elif [ -x "$ROOT/.venv/bin/repowise" ]; then
      "$ROOT/.venv/bin/repowise" update >> "$LOG" 2>&1
    elif [ -x "$ROOT/.venv/Scripts/repowise.exe" ]; then
      "$ROOT/.venv/Scripts/repowise.exe" update >> "$LOG" 2>&1
    fi
  ) &
} >/dev/null 2>&1
# repowise-hook-end
"""

_SECURITY_MARKER = "# repowise-security-hook-start"
_SECURITY_MARKER_END = "# repowise-security-hook-end"

# Pre-commit contract: only exit 1 (the gate failed) blocks the commit. Exit 2
# (the check could not run) and a missing ``repowise`` let it through, because
# a broken tool must not stop anyone committing. ``|| RW_STATUS=$?`` keeps a
# ``sh -e`` hook alive to read the code, and there is no ``exit 0`` at the end,
# so the user's own pre-commit lines after the block still run.
_SECURITY_HOOK_SCRIPT = """\
# repowise-security-hook-start
# Blocks a commit whose staged lines add a security finding.
# Installed by: repowise hook install --security (skip once: git commit --no-verify)
RW_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || RW_ROOT=""
RW_BIN=""
if command -v repowise >/dev/null 2>&1; then
  RW_BIN=repowise
elif [ -x "$RW_ROOT/.venv/bin/repowise" ]; then
  RW_BIN="$RW_ROOT/.venv/bin/repowise"
elif [ -x "$RW_ROOT/.venv/Scripts/repowise.exe" ]; then
  RW_BIN="$RW_ROOT/.venv/Scripts/repowise.exe"
fi
if [ -n "$RW_BIN" ]; then
  RW_STATUS=0
  "$RW_BIN" security check --staged || RW_STATUS=$?
  if [ "$RW_STATUS" -eq 1 ]; then
    echo "repowise: commit blocked by the security check (git commit --no-verify skips it)." >&2
    exit 1
  elif [ "$RW_STATUS" -ne 0 ]; then
    echo "repowise: the security check could not run (exit $RW_STATUS); commit allowed." >&2
  fi
else
  echo "repowise: repowise not found; security check skipped, commit allowed." >&2
fi
# repowise-security-hook-end
"""


def _write_hook(hook_path: Path, content: str) -> None:
    """Write a hook script with LF endings and make it executable.

    sh reads ``then\\r`` as a syntax error, and a pre-commit hook that errors
    blocks every commit.
    """
    hook_path.write_text(content, encoding="utf-8", newline="\n")
    # Make executable (no-op on Windows but harmless)
    with contextlib.suppress(OSError):
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _git_root(path: Path) -> Path | None:
    """Walk up to find .git directory."""
    current = path.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _hooks_dir(repo_path: Path) -> Path | None:
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
            return _husky_user_hook_dir(p)
    except Exception:
        pass
    root = _git_root(repo_path)
    if root is None:
        return None
    return root / ".git" / "hooks"


def hook_path(repo_path: Path) -> Path | None:
    """Where the post-commit hook lives for *repo_path*, or ``None``.

    The same resolution :func:`install` and :func:`uninstall` use, exposed so
    the uninstall inventory can name the path without reimplementing the
    worktree / ``core.hooksPath`` / husky logic.
    """
    hooks_dir = _hooks_dir(repo_path)
    if hooks_dir is None:
        return None
    return hooks_dir / "post-commit"


def marker_present(path: Path) -> bool:
    """Whether *path* exists and holds our marker block."""
    try:
        return path.is_file() and _HOOK_MARKER in path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # An unreadable hook is not ours to claim. The inventory must keep
        # printing when a file cannot be read; ``uninstall`` will say so when
        # its turn comes.
        return False


def _is_shell_hook(content: str) -> bool:
    """Whether an existing hook file can take an appended POSIX sh block.

    A hook with no shebang, or a sh, bash, dash, zsh or ksh one, can. A hook
    written for node or python cannot, and appending shell to it would break
    the user's own hook on every commit after this one.
    """
    first = content.split("\n", 1)[0].strip()
    if not first.startswith("#!"):
        return True
    return re.search(r"\b(sh|bash|dash|zsh|ksh)\b", first) is not None


def _husky_user_hook_dir(hooks_dir: Path) -> Path:
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


def husky_pending_reason(hooks_dir: Path) -> str | None:
    """Why a hook in *hooks_dir* will not run yet, or None if it will.

    Only one case: the husky user-hook directory in a checkout where husky has
    not been installed, so ``core.hooksPath`` points at a ``_`` that is not there
    and git runs nothing. Silent in every other layout.
    """
    if hooks_dir.name != ".husky":
        return None
    if (hooks_dir / "_" / "h").exists():
        return None
    return (
        "husky is not set up in this checkout (no .husky/_), so git runs no hooks "
        "here yet -- run your package manager's install to activate it"
    )


def _strip_legacy_block(content: str) -> tuple[str, bool]:
    """Remove a pre-marker repowise hook body from *content*.

    Older versions of repowise wrote a hook body without start/end markers
    that ended in ``exit 0``, which made the marker block (when later
    appended) unreachable. We detect those by fingerprint and excise the
    surrounding shell block. Returns the cleaned content and whether
    anything was stripped.
    """
    if not any(fp in content for fp in _LEGACY_HOOK_FINGERPRINTS):
        return content, False

    lines = content.splitlines()
    # The legacy block always starts with a shell comment that mentions the
    # hook's purpose and ends at the explicit ``exit 0`` line below the
    # backgrounded subshell. Walk forward until we see ``exit 0`` and drop
    # everything from the first fingerprint line up to and including it.
    start = None
    for i, line in enumerate(lines):
        if any(fp in line for fp in _LEGACY_HOOK_FINGERPRINTS):
            # Walk back to the nearest comment header so we drop the whole
            # block, not just the inner echo line.
            start = i
            for j in range(i - 1, -1, -1):
                stripped = lines[j].strip()
                if stripped.startswith("# post-commit hook") or stripped.startswith("# Auto-syncs"):
                    start = j
                    break
                if not stripped or stripped.startswith("#!"):
                    break
            break

    if start is None:
        return content, False

    end = start
    for k in range(start, len(lines)):
        if lines[k].strip() == "exit 0":
            end = k
            break
        end = k

    cleaned = "\n".join(lines[:start] + lines[end + 1 :]).rstrip() + "\n"
    return cleaned, True


def _marker_block_re(start: str, end: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(start)}.*?{re.escape(end)}\n?", flags=re.DOTALL)


def _replace_marker_block(
    content: str,
    new_block: str,
    start: str = _HOOK_MARKER,
    end: str = _HOOK_MARKER_END,
) -> tuple[str, bool]:
    """Replace an existing repowise marker block in place. Returns (content, replaced).

    Used when the hook is being upgraded: the marker is present but the
    body differs from the current ``_HOOK_SCRIPT``. We must not just bail
    with "already installed" — the user expects ``repowise hook install``
    to ship the latest hook script after a repowise upgrade.

    Only edits the marker-bracketed region; everything before ``_HOOK_MARKER``
    and after ``_HOOK_MARKER_END`` (other tools' hooks) is preserved.

    Implementation note: we use a *callable* repl with ``re.sub`` rather than
    passing the new block as a string. ``re.sub`` processes backslash
    escapes in string repls — so a literal ``\\n`` inside the hook script
    (e.g. a ``printf '%s\\n'`` format) would silently become a real newline,
    breaking the shell quoting of the printf format string. A callable
    bypasses escape processing entirely.
    """
    pattern = _marker_block_re(start, end)
    if not pattern.search(content):
        return content, False
    replacement_text = new_block.rstrip() + "\n"
    new_content = pattern.sub(lambda _m: replacement_text, content, count=1)
    return new_content, new_content != content


def install(repo_path: Path) -> str:
    """Install a repowise post-commit hook in the repo's .git/hooks/.

    Behaves correctly under upgrades: when the marker block exists but its
    body is older than ``_HOOK_SCRIPT``, the block is replaced in place
    (preserving any unrelated hook content around it). Pre-marker legacy
    bodies are stripped before the new block is installed. Returns a
    human-readable status message describing what changed.
    """
    root = _git_root(repo_path)
    if root is None:
        return "not a git repository"

    hooks_dir = _hooks_dir(repo_path)
    if hooks_dir is None:
        return "not a git repository"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    pending = husky_pending_reason(hooks_dir)

    def _annotate(state: str) -> str:
        return f"{state} ({pending})" if pending else state

    migrated_legacy = False
    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8")
        if not _is_shell_hook(content):
            return "not installed: the existing post-commit hook is not a shell script"
        content, migrated_legacy = _strip_legacy_block(content)
        if migrated_legacy:
            _write_hook(hook_path, content)

        if _HOOK_MARKER in content:
            # Marker block present. Decide whether to leave alone or upgrade.
            current_block = _HOOK_SCRIPT.rstrip() + "\n"
            if current_block in content:
                state = "migrated legacy hook" if migrated_legacy else "already installed"
                return _annotate(_with_hook_execution_state(hook_path, state))
            content, replaced = _replace_marker_block(content, _HOOK_SCRIPT)
            if replaced:
                _write_hook(hook_path, content)
                return _annotate(_with_hook_execution_state(hook_path, "upgraded"))
            return _annotate("already installed")
        # Append to existing hook
        _write_hook(hook_path, content.rstrip() + "\n\n" + _HOOK_SCRIPT)
    else:
        _write_hook(hook_path, "#!/bin/sh\n" + _HOOK_SCRIPT)

    return _annotate(_with_hook_execution_state(hook_path, "installed"))


def _is_executable(path: Path) -> bool:
    if os.name == "nt":
        return True
    try:
        return bool(path.stat().st_mode & stat.S_IXUSR)
    except OSError:
        return False


def _with_hook_execution_state(path: Path, state: str) -> str:
    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return state if _is_executable(path) else f"{state} but not executable"


def install_security(repo_path: Path) -> str:
    """Install the opt-in pre-commit block running ``repowise security check --staged``.

    The block goes first, right after the shebang, so a user script that ends
    in ``exit 0`` cannot make it unreachable. An older block is replaced in
    place. Returns a human-readable status message.
    """
    if _git_root(repo_path) is None or (hooks_dir := _hooks_dir(repo_path)) is None:
        return "not a git repository"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    pending = husky_pending_reason(hooks_dir)
    content = hook_path.read_text(encoding="utf-8") if hook_path.exists() else None
    if content is not None and not _is_shell_hook(content):
        return "not installed: the existing pre-commit hook is not a shell script"
    content, state = _with_security_block(content)
    if state != "already installed":
        _write_hook(hook_path, content)
    return f"{state} ({pending})" if pending else state


def _with_security_block(content: str | None) -> tuple[str, str]:
    """*content* (``None`` for no hook yet) holding the current block, and what changed."""
    block = _SECURITY_HOOK_SCRIPT.rstrip() + "\n"
    if content is None:
        return "#!/bin/sh\n" + block, "installed"
    if block in content:
        return content, "already installed"
    if _SECURITY_MARKER in content:
        content, replaced = _replace_marker_block(
            content, block, _SECURITY_MARKER, _SECURITY_MARKER_END
        )
        return content, "upgraded" if replaced else "already installed"
    if content.startswith("#!"):
        shebang, _, rest = content.partition("\n")
        return f"{shebang}\n{block}{rest}", "installed"
    return f"{block}{content}", "installed"


def uninstall(repo_path: Path) -> str:
    """Remove the repowise section from the post-commit hook.

    Preserves other tools' hook content. Deletes the file entirely if
    repowise was the only content.
    """
    return _remove_block(repo_path, "post-commit", _HOOK_MARKER, _HOOK_MARKER_END)


def uninstall_security(repo_path: Path) -> str:
    """Remove the repowise security block from the pre-commit hook, as :func:`uninstall`."""
    return _remove_block(repo_path, "pre-commit", _SECURITY_MARKER, _SECURITY_MARKER_END)


def _remove_block(repo_path: Path, hook_name: str, start: str, end: str) -> str:
    root = _git_root(repo_path)
    if root is None:
        return "not a git repository"

    hooks_dir = _hooks_dir(repo_path)
    if hooks_dir is None:
        return "not a git repository"
    hook_path = hooks_dir / hook_name
    if not hook_path.exists():
        return f"no {hook_name} hook found"

    content = hook_path.read_text(encoding="utf-8")
    if start not in content:
        return f"repowise hook not found in {hook_name}"

    new_content = _marker_block_re(start, end).sub("", content).strip()

    if not new_content or new_content in ("#!/bin/bash", "#!/bin/sh"):
        hook_path.unlink()
        return "removed"
    else:
        _write_hook(hook_path, new_content + "\n")
        return "removed (other hook content preserved)"


def status(repo_path: Path) -> str:
    """Check if the repowise post-commit hook is installed."""
    return _block_status(repo_path, "post-commit", _HOOK_MARKER)


def security_status(repo_path: Path) -> str:
    """Check if the repowise security pre-commit block is installed."""
    return _block_status(repo_path, "pre-commit", _SECURITY_MARKER)


def _block_status(repo_path: Path, hook_name: str, start: str) -> str:
    root = _git_root(repo_path)
    if root is None:
        return "not a git repository"

    hooks_dir = _hooks_dir(repo_path)
    if hooks_dir is None:
        return "not a git repository"
    hook_path = hooks_dir / hook_name
    if not hook_path.exists():
        return "not installed"

    content = hook_path.read_text(encoding="utf-8")
    if start in content:
        pending = husky_pending_reason(hook_path.parent)
        state = "installed" if _is_executable(hook_path) else "installed but not executable"
        return f"{state} ({pending})" if pending else state
    return "not installed"
