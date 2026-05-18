"""Git hook management for repowise auto-sync.

Installs/uninstalls a post-commit hook that runs ``repowise update`` in the
background after every commit, keeping the wiki in sync automatically.

The hook uses start/end markers so it can safely coexist with other hooks
in the same file (e.g. lint hooks, graphify hooks).
"""

from __future__ import annotations

import os
import re
import stat
import sys
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
_HOOK_SCRIPT = """\
# repowise-hook-start
# Auto-syncs repowise wiki after each commit (background, non-blocking).
# Installed by: repowise hook install
{
  ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
  [ -d "$ROOT/.repowise" ] || exit 0
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
    elif command -v uv >/dev/null 2>&1; then
      uv run repowise update >> "$LOG" 2>&1
    fi
  ) &
} >/dev/null 2>&1
# repowise-hook-end
"""


def _git_root(path: Path) -> Path | None:
    """Walk up to find .git directory."""
    current = path.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


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
                if stripped.startswith("# post-commit hook") or stripped.startswith(
                    "# Auto-syncs"
                ):
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

    cleaned = "\n".join(lines[:start] + lines[end + 1:]).rstrip() + "\n"
    return cleaned, True


def _replace_marker_block(content: str, new_block: str) -> tuple[str, bool]:
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
    pattern = re.compile(
        rf"{re.escape(_HOOK_MARKER)}.*?{re.escape(_HOOK_MARKER_END)}\n?",
        flags=re.DOTALL,
    )
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

    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    migrated_legacy = False
    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8")
        content, migrated_legacy = _strip_legacy_block(content)
        if migrated_legacy:
            hook_path.write_text(content, encoding="utf-8")

        if _HOOK_MARKER in content:
            # Marker block present. Decide whether to leave alone or upgrade.
            current_block = _HOOK_SCRIPT.rstrip() + "\n"
            if current_block in content:
                return (
                    "migrated legacy hook" if migrated_legacy else "already installed"
                )
            content, replaced = _replace_marker_block(content, _HOOK_SCRIPT)
            if replaced:
                hook_path.write_text(content, encoding="utf-8")
                try:
                    hook_path.chmod(
                        hook_path.stat().st_mode
                        | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
                    )
                except OSError:
                    pass
                return "upgraded"
            return "already installed"
        # Append to existing hook
        hook_path.write_text(
            content.rstrip() + "\n\n" + _HOOK_SCRIPT,
            encoding="utf-8",
        )
    else:
        hook_path.write_text("#!/bin/sh\n" + _HOOK_SCRIPT, encoding="utf-8")

    # Make executable (no-op on Windows but harmless)
    try:
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass

    return "installed"


def uninstall(repo_path: Path) -> str:
    """Remove the repowise section from the post-commit hook.

    Preserves other tools' hook content. Deletes the file entirely if
    repowise was the only content.
    """
    root = _git_root(repo_path)
    if root is None:
        return "not a git repository"

    hook_path = root / ".git" / "hooks" / "post-commit"
    if not hook_path.exists():
        return "no post-commit hook found"

    content = hook_path.read_text(encoding="utf-8")
    if _HOOK_MARKER not in content:
        return "repowise hook not found in post-commit"

    new_content = re.sub(
        rf"{re.escape(_HOOK_MARKER)}.*?{re.escape(_HOOK_MARKER_END)}\n?",
        "",
        content,
        flags=re.DOTALL,
    ).strip()

    if not new_content or new_content in ("#!/bin/bash", "#!/bin/sh"):
        hook_path.unlink()
        return "removed"
    else:
        hook_path.write_text(new_content + "\n", encoding="utf-8")
        return "removed (other hook content preserved)"


def status(repo_path: Path) -> str:
    """Check if the repowise post-commit hook is installed."""
    root = _git_root(repo_path)
    if root is None:
        return "not a git repository"

    hook_path = root / ".git" / "hooks" / "post-commit"
    if not hook_path.exists():
        return "not installed"

    content = hook_path.read_text(encoding="utf-8")
    if _HOOK_MARKER in content:
        return "installed"
    return "not installed"
