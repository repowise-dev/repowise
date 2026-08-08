"""Claude Desktop and Claude Code MCP config helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from repowise.cli.agent_adapters.claude_code import SHELL_TOOL_MATCHER
from repowise.cli.mcp_config import (
    generate_mcp_config,
    load_existing_config,
    merge_mcp_entry,
    resolve_repowise_command,
)


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
    # Deferred: importing ``core.workspace.config`` runs ``core.workspace``'s
    # package init, which pulls the extractor stack, the language registry,
    # networkx and sqlalchemy — 849ms measured. ``migrate_claude_code_hooks``
    # is called on every agent hook invocation and never reaches this
    # function, so at module scope the whole graph was hook hot-path cost.
    from repowise.core.workspace.config import find_workspace_root

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


def _stored_repowise_entry(config_path: Path | None) -> dict | None:
    """Read the current ``mcpServers.repowise`` entry, or None if absent."""
    if config_path is None or not config_path.exists():
        return None
    try:
        existing = load_existing_config(config_path)
    except Exception:
        # A config we can't parse is one registration will fail on anyway;
        # the probe stays silent rather than guessing at what it holds.
        return None
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get("repowise")
    return entry if isinstance(entry, dict) else None


def _entry_repo_target(entry: dict) -> str | None:
    """The repo path an existing MCP entry points at (``mcp <path> …``)."""
    args = entry.get("args")
    if not isinstance(args, list) or len(args) < 2:
        return None
    return str(args[1])


def describe_mcp_registration_change(repo_path: Path) -> str | None:
    """Warn when registering would repoint an existing ``repowise`` MCP entry.

    Both Claude configs hold a single ``repowise`` key, so an ``init`` from a
    second repo *replaces* the entry rather than adding one alongside it. That
    write is silent, and only surfaces later when the binary or repo it points
    at is gone and the MCP server stops loading. This is a read-only probe run
    before registration; it returns a one-line notice when the stored command
    or repo target differs from what is about to be written, else None.
    """
    new_entry = generate_mcp_config(
        _resolve_mcp_target(repo_path), command=resolve_repowise_command()
    )["mcpServers"]["repowise"]
    new_command = new_entry.get("command")
    new_target = _entry_repo_target(new_entry)

    for config_path in (_claude_code_settings_path(), _claude_desktop_config_path()):
        stored = _stored_repowise_entry(config_path)
        if stored is None:
            continue
        # Both fields are reported when both move, which is the usual shape of
        # the mistake: a second repo indexed by a second install. Naming only
        # one would hide half of what is about to change.
        changes = [
            (label, was, now)
            for label, was, now in (
                ("repo", _entry_repo_target(stored), new_target),
                ("command", stored.get("command"), new_command),
            )
            if was != now
        ]
        if not changes:
            continue
        fields = " and ".join(label for label, _was, _now in changes)
        lines = [f"Replacing the existing repowise MCP {fields} in {config_path}:"]
        for label, was, now in changes:
            # A hand-edited entry can be missing either field outright.
            lines.append(f"    {label} was: {was if was is not None else '(not set)'}")
            lines.append(f"    {label} now: {now}")
        lines.append(
            "  There is one 'repowise' entry per config; pass --no-editor-setup to leave it alone."
        )
        return "\n".join(lines)
    return None


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
# read-intelligence layer (skeleton replacement + per-file stale-read notices);
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

#: Hook commands run through a shell, so absence of the console script is a
#: ``command not found`` on *every* matched tool call — non-blocking, but
#: repeated forever. The plugin ships these hooks independently of the CLI, so
#: "installed the plugin, never installed repowise" is a supported state, not a
#: broken one; a partially-written install (Windows holds ``repowise.exe`` open
#: while an MCP server runs, so uv can abort mid-install) reaches it too.
#: ``augment_hook`` already exits 0 silently on any internal failure — this
#: extends the same courtesy to the script not being there at all.
_AUGMENT_HOOK_COMMAND = (
    "if command -v repowise-augment >/dev/null 2>&1; then exec repowise-augment; fi"
)


# PostToolUseFailure carries the wrong-path rescue. Narrower than
# _AUGMENT_MATCHER on purpose: only the tools that take a path argument can
# fail in the way this surface answers, and a Bash failure is a command line
# rather than a path, so matching it would wake the hook for nothing.
_FAILURE_MATCHER = "Read|Edit|Write|Grep|Glob|NotebookEdit"


def _session_start_entry() -> dict:
    return {
        "matcher": _SESSION_START_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": _AUGMENT_HOOK_COMMAND,
                "timeout": 10,
                "statusMessage": "Loading repowise context...",
            }
        ],
    }


def _backfill_event(hooks: dict, event: str, entry_factory) -> bool:
    """Add this event's augment entry if the install predates it.

    Preserves a user's own hooks on the same event by appending rather than
    replacing, and is a no-op once a repowise entry is present, so migration
    stays idempotent across runs.
    """
    existing = hooks.get(event)
    if existing is None:
        hooks[event] = [entry_factory()]
        return True
    if isinstance(existing, list) and not _has_repowise_hook(existing):
        existing.append(entry_factory())
        return True
    return False


def _failure_entry() -> dict:
    return {
        "matcher": _FAILURE_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": _AUGMENT_HOOK_COMMAND,
                "timeout": 10,
                "statusMessage": "Checking codebase context...",
            }
        ],
    }


def install_claude_code_hooks() -> Path | None:
    """Register the augment hooks in ~/.claude/settings.json.

    PostToolUse detects git staleness, enriches Grep/Glob results, and emits
    Read-intelligence notices; SessionStart injects the live index-freshness
    context block; PostToolUseFailure carries the wrong-path rescue. Existing
    user hooks are preserved.
    """
    settings_path = _claude_code_settings_path()

    post_hook_entry = {
        "matcher": _AUGMENT_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": _AUGMENT_HOOK_COMMAND,
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
        # PostToolUseFailure: the wrong-path rescue.
        # Both go through the same helper as the self-heal, which leaves a
        # hand-written non-list value alone instead of iterating it into an
        # AttributeError the OSError handler below would not catch.
        _backfill_event(hooks, "SessionStart", _session_start_entry)
        _backfill_event(hooks, "PostToolUseFailure", _failure_entry)

        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return settings_path
    except OSError:
        return None


_REWRITE_HOOK_COMMAND = "repowise-rewrite"

# Current rewrite PreToolUse matcher, derived from the adapter's own tool-name
# set so the installed matcher and the gate that reads the payload cannot
# drift apart. Legacy Bash-only installs are widened in place. Safe at module
# scope for the same reason the Codex twin is: the adapter imports this module
# lazily, and its own imports are stdlib only.
_REWRITE_MATCHER = SHELL_TOOL_MATCHER
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
    return claude_code_rewrite_hook_matcher() is not None


def claude_code_rewrite_hook_matcher() -> str | None:
    """The matcher on the installed rewrite entry, or None when not installed.

    ``""`` is a real answer: an entry with no matcher at all. Presence and
    matcher say different things — an entry pointing at a tool name Claude
    Code no longer uses is registered and will never fire.
    """
    settings_path = _claude_code_settings_path()
    if not settings_path.exists():
        return None
    try:
        existing = load_existing_config(settings_path)
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
            # Both the old subcommand form and the unguarded console script
            # migrate to the guarded command, so an install predating the guard
            # stops emitting "command not found" without needing a re-init.
            if cmd in ("repowise augment", "repowise-augment"):
                hook["command"] = _AUGMENT_HOOK_COMMAND
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

    # An install predating one of the later events has the augment PostToolUse
    # entry but not that event's; backfill each. Gated on the augment hook so
    # migration never installs for someone who removed repowise hooks on
    # purpose.
    if isinstance(post, list) and _has_repowise_hook(post):
        if _backfill_event(hooks, "SessionStart", _session_start_entry):
            changed = True
        if _backfill_event(hooks, "PostToolUseFailure", _failure_entry):
            changed = True

    if not changed:
        return False

    try:
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True
