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

# Fingerprints of legacy hook bodies (pre-marker era). When ``install`` is
# called over the top of a file containing these, we strip the legacy block
# rather than appending a second copy. The legacy block was unreachable due
# to a trailing ``exit 0`` and on Windows would fail every commit because
# ``uv run repowise update`` rebuilt the venv from a fresh resolve.
_LEGACY_HOOK_FINGERPRINTS = (
    "[repowise] Triggering incremental wiki update",
    "/tmp/repowise-update.log",
)

# The hook script detects the platform and runs repowise update in the
# background so the commit is never blocked.
_HOOK_SCRIPT = """\
# repowise-hook-start
# Auto-syncs repowise wiki after each commit (background, non-blocking).
# Installed by: repowise hook install
(
  cd "$(git rev-parse --show-toplevel)" || exit 1
  if [ -d ".repowise" ]; then
    # Detect the right way to invoke repowise
    if command -v repowise >/dev/null 2>&1; then
      repowise update > /dev/null 2>&1
    elif command -v uv >/dev/null 2>&1; then
      uv run repowise update > /dev/null 2>&1
    elif command -v powershell.exe >/dev/null 2>&1; then
      powershell.exe -Command "uv run repowise update" > /dev/null 2>&1
    fi
  fi
) &
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


def install(repo_path: Path) -> str:
    """Install a repowise post-commit hook in the repo's .git/hooks/.

    Appends to an existing post-commit hook if one exists (preserving
    other tools' hooks). If a legacy (pre-marker) repowise body is found,
    it is removed first to avoid an unreachable marker block. Returns a
    human-readable status message.
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
            return "migrated legacy hook" if migrated_legacy else "already installed"
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
