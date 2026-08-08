"""Codex CLI rewrite-hook config and the AGENTS.md distill awareness section.

Two surfaces, both managed here so ``hook rewrite install/uninstall/status``
has one place to look:

  - **User-level hooks.json** (``~/.codex/hooks.json``): the opt-in distill
    PreToolUse rewrite entry — one install covers every repo, per-repo
    behavior stays gated by ``distill.commands`` in ``.repowise/config.yaml``
    (the hook script checks it at decide time). Install is version-gated:
    Codex applies a PreToolUse ``updatedInput`` rewrite only from
    :data:`CODEX_REWRITE_MIN_VERSION`; older builds reject it at runtime, so
    installing there would break every shell call.
  - **AGENTS.md awareness section** (repo root, ``REPOWISE_DISTILL``
    markers): teaches the agent to run ``repowise distill <cmd>`` voluntarily
    and to ``repowise expand`` markers instead of re-running commands. Works
    with zero hook support, on any Codex version. Strictly marker-managed —
    install/uninstall round-trips user content byte-for-byte.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# The adapter owns what Codex calls its shell tool; the installed matcher is
# derived from it so the two cannot drift. Safe at module scope: the adapter
# imports this module lazily, inside its methods, so there is no cycle, and
# its own imports are stdlib only.
from repowise.cli.agent_adapters.codex import SHELL_TOOL_MATCHER

#: First Codex release whose PreToolUse hooks honor an ``updatedInput``
#: command rewrite; earlier builds report it as an unsupported hook output.
CODEX_REWRITE_MIN_VERSION: tuple[int, int] = (0, 137)

_REWRITE_HOOK_COMMAND = "repowise-rewrite"

_REWRITE_HOOK_ENTRY = {
    "matcher": SHELL_TOOL_MATCHER,
    "hooks": [
        {
            "type": "command",
            "command": f"{_REWRITE_HOOK_COMMAND} --agent codex",
            "timeout": 5,
            "statusMessage": "Distilling command output...",
        }
    ],
}


#: Matchers this entry has shipped with, current one excluded. An install
#: predating a Codex tool rename holds one of these and therefore matches
#: nothing — and because :func:`_is_rewrite_hook` keys on the *command*, a
#: reinstall would find it, call it present, and leave it dead forever. So
#: install upgrades a matcher it recognises as its own. Only these exact
#: strings are moved: a user who narrowed the matcher deliberately keeps it.
_LEGACY_REWRITE_MATCHERS = ("Bash",)


def _migrate_rewrite_matcher(hook_list: list) -> bool:
    """Repoint an existing repowise entry at the current matcher.

    Returns True when something moved, so the caller knows to write.
    """
    changed = False
    for entry in hook_list:
        if not isinstance(entry, dict):
            continue
        if not any(_is_rewrite_hook(h) for h in entry.get("hooks", []) or []):
            continue
        if entry.get("matcher") in _LEGACY_REWRITE_MATCHERS:
            entry["matcher"] = SHELL_TOOL_MATCHER
            changed = True
    return changed


def migrate_codex_rewrite_hook() -> bool:
    """Repoint an already-installed rewrite hook at the current matcher.

    The twin of :func:`~repowise.cli.editor_integrations.claude_config.migrate_claude_code_hooks`,
    and it exists for the same reason: a user who opted in before a Codex
    tool rename holds an entry that matches nothing, and reinstalling is a
    step nobody knows to take. Never installs — it only moves a matcher on an
    entry that is already ours, so someone who removed the hook on purpose
    stays without it. Returns True when the file was rewritten.
    """
    from repowise.cli.mcp_config import load_existing_config

    hooks_path = _codex_hooks_path()
    if not hooks_path.exists():
        return False
    try:
        existing = load_existing_config(hooks_path)
        hooks = existing.get("hooks")
        if not isinstance(hooks, dict):
            return False
        pre_hooks = hooks.get("PreToolUse")
        if not isinstance(pre_hooks, list):
            return False
        if not _migrate_rewrite_matcher(pre_hooks):
            return False
        hooks_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        # Same posture as install: a malformed or unreadable hooks file is a
        # migration that did not happen, never an error the user sees.
        return False


def _codex_hooks_path() -> Path:
    """The user-level Codex hooks file (~/.codex/hooks.json)."""
    return Path.home() / ".codex" / "hooks.json"


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------


def codex_cli_version() -> tuple[int, ...] | None:
    """Parse ``codex --version`` into a tuple, or None when unavailable."""
    from repowise.cli.mcp_config import resolve_codex_executable

    codex_cmd = resolve_codex_executable()
    if not codex_cmd:
        return None
    try:
        result = subprocess.run(
            [codex_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(\d+(?:\.\d+)+)", result.stdout or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def codex_supports_rewrite(version: tuple[int, ...] | None = None) -> bool | None:
    """True/False when the Codex version is known; None when it isn't."""
    resolved = version if version is not None else codex_cli_version()
    if resolved is None:
        return None
    return resolved >= CODEX_REWRITE_MIN_VERSION


# ---------------------------------------------------------------------------
# hooks.json install / uninstall — same merge discipline as the Claude Code
# settings.json writer: idempotent, additive, user hooks untouched.
# ---------------------------------------------------------------------------


def install_codex_rewrite_hook() -> Path | None:
    """Register the distill PreToolUse rewrite entry in ~/.codex/hooks.json."""
    from repowise.cli.mcp_config import load_existing_config

    hooks_path = _codex_hooks_path()
    try:
        if hooks_path.exists():
            existing = load_existing_config(hooks_path)
        else:
            hooks_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        hooks = existing.setdefault("hooks", {})
        pre_hooks = hooks.setdefault("PreToolUse", [])
        if not _has_rewrite_hook(pre_hooks):
            pre_hooks.append(json.loads(json.dumps(_REWRITE_HOOK_ENTRY)))
            hooks_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        elif _migrate_rewrite_matcher(pre_hooks):
            # Present but pointed at a name Codex no longer uses. Reinstalling
            # is the only repair path a user has, so it has to be one.
            hooks_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return hooks_path
    except Exception:
        # OSError on write, ClickException from load_existing_config on a
        # malformed existing file — either way, install reports failure.
        return None


def uninstall_codex_rewrite_hook() -> bool:
    """Remove the distill rewrite entry; True when something was removed."""
    from repowise.cli.mcp_config import load_existing_config

    hooks_path = _codex_hooks_path()
    if not hooks_path.exists():
        return False
    try:
        existing = load_existing_config(hooks_path)
    except Exception:
        return False

    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    pre_hooks = hooks.get("PreToolUse")
    if not isinstance(pre_hooks, list):
        return False

    changed = False
    for entry in list(pre_hooks):
        kept = [h for h in entry.get("hooks", []) if not _is_rewrite_hook(h)]
        if len(kept) != len(entry.get("hooks", [])):
            changed = True
            if kept:
                entry["hooks"] = kept
            else:
                pre_hooks.remove(entry)
    if not changed:
        return False
    if not pre_hooks:
        hooks.pop("PreToolUse", None)

    try:
        hooks_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def codex_rewrite_hook_installed() -> bool:
    """True when the distill rewrite entry is registered in hooks.json."""
    return codex_rewrite_hook_matcher() is not None


def codex_rewrite_hook_matcher() -> str | None:
    """The matcher on the installed rewrite entry, or None when not installed.

    ``""`` is a real answer: an entry with no matcher at all. The caller needs
    the matcher and not just its presence because the two say different
    things — an entry whose matcher names a tool Codex has since renamed is
    registered and will never fire.
    """
    from repowise.cli.mcp_config import load_existing_config

    hooks_path = _codex_hooks_path()
    if not hooks_path.exists():
        return None
    try:
        existing = load_existing_config(hooks_path)
    except Exception:
        return None
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return None
    pre_hooks = hooks.get("PreToolUse")
    if not isinstance(pre_hooks, list):
        return None
    return _rewrite_matcher(pre_hooks)


def _is_rewrite_hook(hook: dict) -> bool:
    return _REWRITE_HOOK_COMMAND in hook.get("command", "")


def _rewrite_matcher(hook_list: list) -> str | None:
    """Matcher of the first entry carrying our hook, or None if there is none."""
    for entry in hook_list:
        if any(_is_rewrite_hook(h) for h in entry.get("hooks", [])):
            matcher = entry.get("matcher")
            return matcher if isinstance(matcher, str) else ""
    return None


def _has_rewrite_hook(hook_list: list) -> bool:
    return _rewrite_matcher(hook_list) is not None


# ---------------------------------------------------------------------------
# AGENTS.md awareness section — marker-managed, mirrors the CLAUDE.md
# "Output Distillation" block.
# ---------------------------------------------------------------------------

_DISTILL_MARKER_START = (
    "<!-- REPOWISE_DISTILL:START — Do not edit below this line. Auto-generated by Repowise. -->"
)
_DISTILL_MARKER_END = "<!-- REPOWISE_DISTILL:END -->"

_DISTILL_SECTION_HEADING = "### Output Distillation"

_DISTILL_SECTION = f"""{_DISTILL_SECTION_HEADING}

- Prefer `repowise distill <cmd>` for noisy commands — test runs, builds, `git status`/`log`/`diff`, searches, file listings. It runs the command unchanged (exit code preserved) and prints a compact, errors-first rendering; every error line survives.
- Output may contain a marker like `[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); restore: repowise expand a1b2c3d4e5f6]`. The omitted content is fully preserved — run `repowise expand <ref>` to retrieve it, or `repowise expand <ref> -q <regex>` for just the matching lines.
- Never re-run a command to see omitted output; expand the marker instead.
- For structure-level questions about a large indexed file ("what's in here", "which function handles X"), `get_context(["path"], include=["skeleton"])` returns the file with bodies elided — every signature plus the bodies of the most central symbols — at a fraction of the cost of a full Read."""

_NEW_FILE_PLACEHOLDER = (
    "# AGENTS.md\n\n"
    "<!-- Add your project instructions above or below the Repowise section. "
    "Repowise only updates the managed section between markers. -->\n"
)


def _agents_md_path(repo_path: Path) -> Path:
    return Path(repo_path) / "AGENTS.md"


def install_agents_md_distill_section(repo_path: Path) -> Path | None:
    """Ensure AGENTS.md carries the distill awareness section.

    Idempotent: an existing marker block is refreshed in place; a file that
    already teaches distillation elsewhere (e.g. a future indexed-template
    section) is left untouched; otherwise the block is appended. Returns the
    AGENTS.md path, or None on write failure.
    """
    target = _agents_md_path(repo_path)
    wrapped = f"{_DISTILL_MARKER_START}\n{_DISTILL_SECTION}\n{_DISTILL_MARKER_END}"
    try:
        if not target.exists():
            content = f"{_NEW_FILE_PLACEHOLDER}\n{wrapped}\n"
        else:
            existing = target.read_text(encoding="utf-8")
            if _DISTILL_MARKER_START in existing:
                pattern = re.escape(_DISTILL_MARKER_START) + r".*?" + re.escape(_DISTILL_MARKER_END)
                content = re.sub(pattern, wrapped, existing, flags=re.DOTALL)
            elif _DISTILL_SECTION_HEADING in existing:
                return target  # already taught elsewhere in the file
            else:
                content = existing.rstrip() + "\n\n" + wrapped + "\n"
        target.write_text(content, encoding="utf-8", newline="\n")
        return target
    except OSError:
        return None


def remove_agents_md_distill_section(repo_path: Path) -> bool:
    """Remove the marker-managed section; True when something was removed.

    If nothing but the install-time placeholder remains afterwards, the file
    is deleted entirely so install→uninstall round-trips to "no AGENTS.md".
    User content is never touched.
    """
    target = _agents_md_path(repo_path)
    if not target.exists():
        return False
    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        return False
    if _DISTILL_MARKER_START not in existing:
        return False

    pattern = (
        r"\n*" + re.escape(_DISTILL_MARKER_START) + r".*?" + re.escape(_DISTILL_MARKER_END) + r"\n?"
    )
    remaining = re.sub(pattern, "", existing, flags=re.DOTALL)
    if remaining and not remaining.endswith("\n"):
        # Install rstrips before appending the block; restore the trailing
        # newline that strip consumed so removal is a true inverse.
        remaining += "\n"
    try:
        if remaining.strip() in ("", _NEW_FILE_PLACEHOLDER.strip()):
            target.unlink()
        else:
            target.write_text(remaining, encoding="utf-8", newline="\n")
    except OSError:
        return False
    return True


def agents_md_distill_section_installed(repo_path: Path) -> bool:
    """True when AGENTS.md carries the marker-managed awareness section."""
    target = _agents_md_path(repo_path)
    try:
        return target.exists() and _DISTILL_MARKER_START in target.read_text(encoding="utf-8")
    except OSError:
        return False
