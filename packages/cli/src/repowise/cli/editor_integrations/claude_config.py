"""Claude Desktop and Claude Code MCP config helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from repowise.cli.mcp_config import (
    generate_mcp_config,
    load_existing_config,
    merge_mcp_entry,
    resolve_repowise_command,
)
from repowise.core.workspace.config import find_workspace_root


def _claude_desktop_config_path() -> Path | None:
    """Return the Claude Desktop config path for this OS, or None if unsupported."""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if sys.platform == "win32":
        appdata = Path.home() / "AppData" / "Roaming"
        return appdata / "Claude" / "claude_desktop_config.json"
    # Linux / other: Claude Desktop not officially supported yet
    return None


def _claude_code_settings_path() -> Path:
    """Return the global Claude Code settings path (~/.claude/settings.json)."""
    return Path.home() / ".claude" / "settings.json"


def _resolve_mcp_target(repo_path: Path) -> Path:
    """Pick the right path to register as the MCP server target.

    The Claude Desktop / Claude Code MCP config is global — there is only one
    ``"repowise"`` server key. When the user is operating inside a workspace,
    registering the per-repo path means every ``repowise init`` against a
    sibling repo silently overwrites the entry to point at whichever repo was
    indexed last, breaking workspace mode.

    If ``repo_path`` lives inside a workspace (``.repowise-workspace.yaml`` in
    any ancestor), return the workspace root instead so the MCP server is
    invoked in workspace mode and ``repo="<alias>"`` queries work across all
    repos. Otherwise fall back to the per-repo path, preserving single-repo
    behavior.
    """
    workspace_root = find_workspace_root(repo_path)
    return workspace_root if workspace_root is not None else repo_path


def register_with_claude_desktop(repo_path: Path) -> Path | None:
    """Add repowise MCP server to Claude Desktop's config.

    When ``repo_path`` is inside a workspace, the registration targets the
    workspace root so the MCP server starts in workspace mode.

    Returns the config path if successful, None if Claude Desktop is not
    present or the platform is unsupported.
    """
    config_path = _claude_desktop_config_path()
    if config_path is None:
        return None
    if not config_path.parent.exists():
        # Claude Desktop not installed
        return None
    target = _resolve_mcp_target(repo_path)
    # Per-user config: pin the absolute path of the running install so a
    # PATH shadow install (conda, old pip, pipx) can't hijack the server.
    # Refreshed on every re-registration, so a moved venv self-heals on the
    # next `repowise init`/`update`.
    entry = generate_mcp_config(target, command=resolve_repowise_command())["mcpServers"]
    return config_path if merge_mcp_entry(config_path, entry) else None


def register_with_claude_code(repo_path: Path) -> Path | None:
    """Add repowise MCP server to global Claude Code settings (~/.claude/settings.json).

    When ``repo_path`` is inside a workspace, the registration targets the
    workspace root so the MCP server starts in workspace mode and subsequent
    inits against sibling repos do not overwrite the entry.

    Returns the settings path if successful, None on failure.
    """
    settings_path = _claude_code_settings_path()
    target = _resolve_mcp_target(repo_path)
    # Per-user config: absolute command, see register_with_claude_desktop.
    entry = generate_mcp_config(target, command=resolve_repowise_command())["mcpServers"]
    return settings_path if merge_mcp_entry(settings_path, entry) else None


def enable_tool_search_in_claude_code() -> Path | None:
    """Set ``env.ENABLE_TOOL_SEARCH=true`` in ~/.claude/settings.json.

    Claude Code defers MCP tool schemas (loads them on demand via tool search)
    when this is on, so repowise's tools don't sit in every session's standing
    context. The behavior is client-default in newer builds but flips off in
    some configs, so we pin it explicitly. Idempotent and non-destructive: an
    existing ``ENABLE_TOOL_SEARCH`` value the user set (including a deliberate
    ``false``) is left untouched, and other ``env`` keys are preserved.

    Returns the settings path when it newly sets the key, else None (already
    set, or a write failure — both non-fatal to ``init``).
    """
    settings_path = _claude_code_settings_path()
    try:
        if settings_path.exists():
            existing = load_existing_config(settings_path)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
        env = existing.get("env")
        if not isinstance(env, dict):
            env = {}
        if "ENABLE_TOOL_SEARCH" in env:
            return None
        env["ENABLE_TOOL_SEARCH"] = "true"
        existing["env"] = env
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return settings_path
    except (OSError, ValueError):
        return None


# Current augment PostToolUse matcher. Read/Edit/Write power the distill
# read-intelligence layer (skeleton nudges + per-file stale-read notices);
# PowerShell is the Windows Claude Code shell tool (same payload shape as
# Bash); the mcp__ pattern feeds the read-after-served ledger (read_enrich)
# and matches the repowise MCP server under any registration name (local,
# plugin, hosted "Repowise"); legacy installs with the narrower matchers
# below are widened in place.
_AUGMENT_MATCHER = "Bash|PowerShell|Grep|Glob|Read|Edit|Write|mcp__.*[Rr]epowise.*__.*"
_LEGACY_AUGMENT_MATCHERS = (
    "Bash",
    "Bash|Grep|Glob",
    "Bash|Grep|Glob|Read|Edit|Write",
    "Bash|PowerShell|Grep|Glob|Read|Edit|Write",
)

# SessionStart emits the live index-freshness / trust context block. `compact`
# is excluded on purpose: the block usually survives compaction in the summary,
# and re-emitting it there would double it up.
_SESSION_START_MATCHER = "startup|resume|clear"


def _session_start_entry() -> dict:
    return {
        "matcher": _SESSION_START_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": "repowise-augment",
                "timeout": 10,
                "statusMessage": "Loading repowise context...",
            }
        ],
    }


def install_claude_code_hooks() -> Path | None:
    """Register PostToolUse + SessionStart hooks in ~/.claude/settings.json.

    PostToolUse detects git staleness, enriches Grep/Glob results, and emits
    Read-intelligence notices; SessionStart injects the live index-freshness
    context block. Existing user hooks are preserved.
    """
    settings_path = _claude_code_settings_path()

    post_hook_entry = {
        "matcher": _AUGMENT_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": "repowise-augment",
                "timeout": 10,
                "statusMessage": "Checking codebase context...",
            }
        ],
    }

    try:
        if settings_path.exists():
            existing = load_existing_config(settings_path)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        hooks = existing.setdefault("hooks", {})

        # Drop any pre-existing legacy *augment* PreToolUse entry; augment
        # routes everything through PostToolUse. The distill rewrite hook
        # (`repowise-rewrite`) is a separate, opt-in PreToolUse entry managed
        # by install/uninstall_claude_code_rewrite_hook and must be preserved.
        pre_hooks = hooks.setdefault("PreToolUse", [])
        _strip_repowise_pretool(pre_hooks)
        if not pre_hooks:
            hooks.pop("PreToolUse", None)

        # PostToolUse: migrate legacy command + matcher, then add if missing.
        post_hooks = hooks.setdefault("PostToolUse", [])
        _migrate_legacy_hook(post_hooks)
        if not _has_repowise_hook(post_hooks):
            post_hooks.append(post_hook_entry)

        # SessionStart: live index-freshness context at session start.
        session_hooks = hooks.setdefault("SessionStart", [])
        if not _has_repowise_hook(session_hooks):
            session_hooks.append(_session_start_entry())

        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return settings_path
    except OSError:
        return None


_REWRITE_HOOK_COMMAND = "repowise-rewrite"

# Current rewrite PreToolUse matcher; PowerShell is the Windows Claude Code
# shell tool. Legacy Bash-only installs are widened in place.
_REWRITE_MATCHER = "Bash|PowerShell"
_LEGACY_REWRITE_MATCHERS = ("Bash",)


def install_claude_code_rewrite_hook() -> Path | None:
    """Register the opt-in distill PreToolUse rewrite hook (shell matcher).

    Idempotent; preserves user hooks and the augment PostToolUse entry.
    Returns the settings path on success, None on failure.
    """
    settings_path = _claude_code_settings_path()
    pre_hook_entry = {
        "matcher": _REWRITE_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": _REWRITE_HOOK_COMMAND,
                "timeout": 5,
                "statusMessage": "Distilling command output...",
            }
        ],
    }

    try:
        if settings_path.exists():
            existing = load_existing_config(settings_path)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        hooks = existing.setdefault("hooks", {})
        pre_hooks = hooks.setdefault("PreToolUse", [])
        changed = _migrate_legacy_rewrite_matcher(pre_hooks)
        if not _has_rewrite_hook(pre_hooks):
            pre_hooks.append(pre_hook_entry)
            changed = True
        if changed:
            settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return settings_path
    except OSError:
        return None


# Permission allow rules for rewritten commands. A rewrite changes the
# command string, so the user's existing allowlist entries (e.g.
# ``Bash(git diff:*)``) stop matching the rewritten ``repowise distill …``
# and every approved family starts prompting again. One rule per shell tool
# covers the distill prefix; `repowise distill` runs the wrapped command
# unchanged, so the rule never widens what a command can do.
DISTILL_ALLOW_RULES = (
    "Bash(repowise distill:*)",
    "PowerShell(repowise distill:*)",
)


def add_claude_code_distill_allow_rules() -> Path | None:
    """Append the distill allow rules to ``permissions.allow`` (idempotent).

    Returns the settings path on success, None when settings.json exists but
    can't be read or written. Strictly additive — user rules are untouched.
    """
    settings_path = _claude_code_settings_path()
    try:
        if settings_path.exists():
            existing = load_existing_config(settings_path)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
    except Exception:
        return None

    permissions = existing.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        return None
    allow = permissions.setdefault("allow", [])
    if not isinstance(allow, list):
        return None

    missing = [rule for rule in DISTILL_ALLOW_RULES if rule not in allow]
    if missing:
        allow.extend(missing)
        try:
            settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        except OSError:
            return None
    return settings_path


def _migrate_legacy_rewrite_matcher(hook_list: list) -> bool:
    """Widen legacy rewrite-hook matchers in place (``Bash`` → current)."""
    changed = False
    for entry in hook_list:
        only_rewrite = entry.get("hooks") and all(_is_rewrite_hook(h) for h in entry["hooks"])
        if only_rewrite and entry.get("matcher", "") in _LEGACY_REWRITE_MATCHERS:
            entry["matcher"] = _REWRITE_MATCHER
            changed = True
    return changed


def uninstall_claude_code_rewrite_hook() -> bool:
    """Remove the distill rewrite hook; True when something was removed."""
    settings_path = _claude_code_settings_path()
    if not settings_path.exists():
        return False
    try:
        existing = load_existing_config(settings_path)
    except Exception:
        return False

    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    pre_hooks = hooks.get("PreToolUse")
    if not isinstance(pre_hooks, list):
        return False

    changed = _strip_hooks(pre_hooks, _is_rewrite_hook)
    if not changed:
        return False
    if not pre_hooks:
        hooks.pop("PreToolUse", None)

    try:
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def claude_code_rewrite_hook_installed() -> bool:
    """True when the distill rewrite hook is registered in settings.json."""
    settings_path = _claude_code_settings_path()
    if not settings_path.exists():
        return False
    try:
        existing = load_existing_config(settings_path)
    except Exception:
        return False
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    pre_hooks = hooks.get("PreToolUse")
    return isinstance(pre_hooks, list) and _has_rewrite_hook(pre_hooks)


def _is_rewrite_hook(hook: dict) -> bool:
    return _REWRITE_HOOK_COMMAND in hook.get("command", "")


def _has_rewrite_hook(hook_list: list) -> bool:
    return any(_is_rewrite_hook(h) for entry in hook_list for h in entry.get("hooks", []))


def _strip_hooks(hook_list: list, predicate) -> bool:
    """Remove hooks matching *predicate* from a hook bucket in place."""
    changed = False
    for entry in list(hook_list):
        kept = [h for h in entry.get("hooks", []) if not predicate(h)]
        if len(kept) != len(entry.get("hooks", [])):
            changed = True
            if kept:
                entry["hooks"] = kept
            else:
                hook_list.remove(entry)
    return changed


def _has_repowise_hook(hook_list: list) -> bool:
    """Check if a repowise hook is already registered, current or legacy."""
    for entry in hook_list:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if "repowise-augment" in cmd or "repowise augment" in cmd:
                return True
    return False


def _is_repowise_hook(hook: dict) -> bool:
    cmd = hook.get("command", "")
    return "repowise-augment" in cmd or "repowise augment" in cmd


def _strip_repowise_pretool(hook_list: list) -> bool:
    """Remove legacy *augment* PreToolUse entries from a hook bucket in place.

    Matches only the augment command names — the opt-in ``repowise-rewrite``
    PreToolUse hook is intentionally untouched.
    """
    return _strip_hooks(hook_list, _is_repowise_hook)


def _migrate_legacy_hook(hook_list: list) -> bool:
    """In-place migration of legacy PostToolUse entries to current shape."""
    changed = False
    for entry in hook_list:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd == "repowise augment":
                hook["command"] = "repowise-augment"
                changed = True
        matcher = entry.get("matcher", "")
        only_repowise = entry.get("hooks") and all(_is_repowise_hook(h) for h in entry["hooks"])
        if only_repowise and matcher in _LEGACY_AUGMENT_MATCHERS:
            entry["matcher"] = _AUGMENT_MATCHER
            changed = True
    return changed


def migrate_claude_code_hooks() -> bool:
    """Self-healing migration of legacy Claude Code hook entries."""
    settings_path = _claude_code_settings_path()
    if not settings_path.exists():
        return False

    try:
        existing = load_existing_config(settings_path)
    except Exception:
        return False

    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False

    pre = hooks.get("PreToolUse")
    if isinstance(pre, list):
        if _strip_repowise_pretool(pre):
            changed = True
            if not pre:
                hooks.pop("PreToolUse", None)
        if _migrate_legacy_rewrite_matcher(pre):
            changed = True

    post = hooks.get("PostToolUse")
    if isinstance(post, list) and _migrate_legacy_hook(post):
        changed = True

    # Users who installed before the SessionStart context hook existed have
    # the augment PostToolUse entry but no SessionStart one; backfill it.
    # Gated on the augment hook so migration never installs for someone who
    # removed repowise hooks on purpose.
    if isinstance(post, list) and _has_repowise_hook(post):
        session = hooks.get("SessionStart")
        if session is None:
            hooks["SessionStart"] = [_session_start_entry()]
            changed = True
        elif isinstance(session, list) and not _has_repowise_hook(session):
            session.append(_session_start_entry())
            changed = True

    if not changed:
        return False

    try:
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True
