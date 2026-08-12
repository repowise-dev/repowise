"""Codex CLI as an agent target.

Full tier by the derived rule — it names both a hook adapter and a transcript
adapter — but asymmetric with Claude Code in a way worth stating: its plugin
ships skills and no ``commands/`` directory, so Codex users have no slash
commands today. That gap is Phase 3's, and the tier rule does not paper over it
because slash commands are not one of the two surfaces Full is derived from.

Codex is the only target that writes three formats for one install: a TOML
server table, a TOML feature flag that has to be switched on separately or the
hooks are inert, and a JSON hooks file. That combination is the concrete
argument for composition over a base class — no single inherited ``write()``
shape fits it without an override per format.

Two quirks preserved from the existing implementation, both load-bearing:

* The shell-tool matcher is derived from the hook adapter rather than spelled
  here. Codex renamed its shell tool between releases, and a hardcoded name
  produced installs whose matcher selected nothing — silent in exactly the way
  a working hook is.
* Enabling ``features.hooks`` is a second write to the same TOML file. Skipping
  it leaves a hooks.json the host never reads.
"""

from __future__ import annotations

from pathlib import Path

from ..types import (
    Capability,
    DoctorReport,
    DoctorStatus,
    FileAction,
    InstallMethod,
    Registration,
    Scope,
    WriteResult,
)

ID = "codex"
DISPLAY_NAME = "Codex CLI"
DOCS_URL = "https://developers.openai.com/codex/cli"

#: Config key for this agent's managed instruction file (``AGENTS.md``).
PROJECT_FILE_ID = "agents_md"

METHODS = (
    InstallMethod(
        id="plugin",
        provides=frozenset({Capability.SKILLS, Capability.HOOKS}),
        managed_by="host",
    ),
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.HOOKS, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def project_config_path(repo_path: Path) -> Path:
    return repo_path / ".codex" / "config.toml"


def project_hooks_path(repo_path: Path) -> Path:
    return repo_path / ".codex" / "hooks.json"


def user_hooks_path() -> Path:
    from repowise.cli.editor_integrations.codex_config import _codex_hooks_path

    return _codex_hooks_path()


def instructions_path(repo_path: Path) -> Path:
    return repo_path / "AGENTS.md"


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------


def server_table(repo_path: Path) -> dict[str, object]:
    """The ``[mcp_servers.repowise]`` table Codex reads.

    Bare ``repowise`` command: this file is repo-local and may be committed, so
    it follows the same rule as ``.mcp.json`` rather than pinning an absolute
    path only valid on the machine that ran ``init``.
    """
    return {
        "command": "repowise",
        "args": ["mcp"],
        "cwd": str(repo_path.resolve()),
        "startup_timeout_sec": 20,
    }


def hooks_config() -> dict[str, object]:
    """Project-local Codex hooks for context injection and freshness checks."""
    from repowise.cli.agent_adapters.codex import SHELL_TOOL_MATCHER

    context_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": 30,
        "statusMessage": "Loading repowise context...",
    }
    freshness_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": 30,
        "statusMessage": "Checking repowise freshness...",
    }
    return {
        "hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [context_hook]}],
            "UserPromptSubmit": [{"hooks": [context_hook]}],
            "PostToolUse": [
                {"matcher": SHELL_TOOL_MATCHER, "hooks": [freshness_hook]},
                {"matcher": "apply_patch|Edit|Write", "hooks": [freshness_hook]},
            ],
        }
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def write_server_config(repo_path: Path) -> Path:
    """Merge the repowise server table into project-local ``.codex/config.toml``."""
    from ..formats.toml_merge import (
        ensure_valid_toml,
        load_toml_document,
        replace_table,
        require_table,
        table_block,
    )

    config_path = project_config_path(repo_path)
    block = table_block("mcp_servers.repowise", server_table(repo_path))

    if config_path.exists():
        existing_text = config_path.read_text(encoding="utf-8")
        doc = load_toml_document(config_path, existing_text)
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(f"{block}\n", encoding="utf-8")
        return config_path

    # Both levels are checked before the regex runs: a scalar where a table
    # belongs means the merge would produce a duplicate key, and refusing is
    # the only answer that leaves the user's file intact.
    servers = require_table(doc, "mcp_servers", config_path, "mcp_servers")
    if servers is not None:
        require_table(servers, "repowise", config_path, "mcp_servers.repowise")

    merged_text = replace_table(existing_text, "mcp_servers.repowise", block)
    ensure_valid_toml(merged_text, config_path)
    config_path.write_text(merged_text, encoding="utf-8")
    return config_path


def enable_hooks_feature(repo_path: Path) -> Path:
    """Switch on ``features.hooks``, without which the hooks file is inert."""
    from ..formats.toml_merge import (
        ensure_valid_toml,
        load_toml_document,
        replace_table,
        require_table,
        table_block,
    )

    config_path = project_config_path(repo_path)

    if config_path.exists():
        existing_text = config_path.read_text(encoding="utf-8")
        doc = load_toml_document(config_path, existing_text)
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing_text = ""
        doc = {}

    features = dict(require_table(doc, "features", config_path, "features") or {})
    features["hooks"] = True
    merged_text = replace_table(existing_text, "features", table_block("features", features))
    ensure_valid_toml(merged_text, config_path)
    config_path.write_text(merged_text, encoding="utf-8")
    return config_path


def write_hooks_config(repo_path: Path) -> Path:
    """Merge repowise hooks into ``.codex/hooks.json`` and enable the feature.

    Additive per matcher group: a matcher that already carries one of our hooks
    is left alone, so a user who narrowed or annotated an entry keeps it.
    """
    import click

    from ..formats.json_merge import load_json_object, write_json_config

    hooks_path = project_hooks_path(repo_path)
    new_config = hooks_config()

    if hooks_path.exists():
        existing = load_json_object(hooks_path)
    else:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}

    hooks = existing.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise click.ClickException(
            f"Cannot update {hooks_path}: hooks must contain a JSON object. "
            "Fix or remove it and retry; no changes were written."
        )

    for event, entries in new_config["hooks"].items():
        event_hooks = hooks.setdefault(event, [])
        if not isinstance(event_hooks, list):
            raise click.ClickException(
                f"Cannot update {hooks_path}: hooks.{event} must contain a JSON array. "
                "Fix or remove it and retry; no changes were written."
            )
        for entry in entries:
            if not _has_augment_hook_for_matcher(event_hooks, entry.get("matcher")):
                event_hooks.append(entry)

    write_json_config(hooks_path, existing)
    enable_hooks_feature(repo_path)
    return hooks_path


def _is_augment_hook(hook: dict) -> bool:
    cmd = hook.get("command", "")
    return "repowise-augment" in cmd or "repowise augment" in cmd


def _has_augment_hook_for_matcher(hook_list: list, matcher: object) -> bool:
    for entry in hook_list:
        if entry.get("matcher") != matcher:
            continue
        for hook in entry.get("hooks", []):
            if _is_augment_hook(hook):
                return True
    return False


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Every place Codex is currently wired to repowise."""
    from repowise.cli.editor_integrations.codex_config import codex_rewrite_hook_installed

    found: list[Registration] = []

    if codex_rewrite_hook_installed():
        found.append(
            Registration(method="direct", scope=Scope.USER, config_path=user_hooks_path()),
        )

    if repo_path is not None:
        config_path = project_config_path(repo_path)
        if config_path.exists() and "mcp_servers.repowise" in config_path.read_text(
            encoding="utf-8", errors="ignore"
        ):
            found.append(
                Registration(method="direct", scope=Scope.PROJECT, config_path=config_path),
            )

    return found


class CodexTarget:
    """Descriptor for the Codex CLI. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = "codex"
    session_adapter = "codex"
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        return True

    def detect(self, repo_path: Path | None = None) -> list[Registration]:
        return detect(repo_path)

    def install(
        self,
        scope: Scope,
        options: object = None,
        *,
        repo_path: Path | None = None,
    ) -> WriteResult:
        from repowise.cli.editor_integrations.codex_config import install_codex_rewrite_hook

        result = WriteResult()
        if scope is Scope.PROJECT:
            if repo_path is None:
                raise ValueError("project-scope install needs a repo_path")
            result.record(write_server_config(repo_path), FileAction.UPDATED)
            result.record(write_hooks_config(repo_path), FileAction.UPDATED)
            return result

        hooks = install_codex_rewrite_hook()
        result.record(
            hooks or user_hooks_path(),
            FileAction.UPDATED if hooks else FileAction.NOT_FOUND,
        )
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        from repowise.cli.editor_integrations.codex_config import (
            remove_agents_md_distill_section,
            uninstall_codex_rewrite_hook,
        )

        result = WriteResult()
        if scope is Scope.USER:
            removed = uninstall_codex_rewrite_hook()
            result.record(
                user_hooks_path(),
                FileAction.REMOVED if removed else FileAction.NOT_FOUND,
            )
            return result

        if repo_path is None:
            raise ValueError("project-scope uninstall needs a repo_path")
        removed = remove_agents_md_distill_section(repo_path)
        result.record(
            instructions_path(repo_path),
            FileAction.REMOVED if removed else FileAction.NOT_FOUND,
        )
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        from ..formats.toml_merge import table_block

        target = repo_path or Path.cwd()
        return (
            table_block("mcp_servers.repowise", server_table(target))
            + "\n\n"
            + table_block("features", {"hooks": True})
        )

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        if scope is Scope.USER:
            return [str(user_hooks_path())]
        repo = repo_path or Path.cwd()
        return [
            str(project_config_path(repo)),
            str(project_hooks_path(repo)),
            str(instructions_path(repo)),
        ]

    def doctor(self) -> DoctorReport:
        """Health of the Codex wiring.

        The stale-matcher case matters more here than anywhere else: Codex is
        the harness that actually renamed its shell tool, and an install written
        before the rename carries a matcher that selects nothing.
        """
        from repowise.cli.agent_adapters.codex import SHELL_TOOL_MATCHER
        from repowise.cli.editor_integrations.codex_config import (
            codex_rewrite_hook_matcher,
            codex_supports_rewrite,
        )

        matcher = codex_rewrite_hook_matcher()
        if matcher is None:
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.NOT_INSTALLED,
                issues=("The distill rewrite hook is not installed for Codex.",),
                fix_command="repowise hook rewrite install",
            )

        issues: list[str] = []
        if matcher != SHELL_TOOL_MATCHER:
            issues.append(
                f"The rewrite hook matches {matcher!r}, but Codex now names its shell "
                f"tools {SHELL_TOOL_MATCHER!r}. The hook is installed and will never fire."
            )
        if codex_supports_rewrite() is False:
            issues.append(
                "This Codex build predates PreToolUse command rewriting, so the hook "
                "is registered but its rewrite will be rejected at runtime."
            )

        if not issues:
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.STALE,
            issues=tuple(issues),
            fix_command="repowise hook rewrite install",
        )


TARGET = CodexTarget()
