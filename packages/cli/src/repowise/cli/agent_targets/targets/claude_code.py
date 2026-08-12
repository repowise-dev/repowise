"""Claude Code (and Claude Desktop) as an agent target.

Reaches Full tier through two different routes, which is why the method axis
exists at all:

* **plugin** — the host owns the artifact. It ships MCP, hooks, skills and
  slash commands together, and repowise cannot rewrite it; only ``/plugin
  update`` can. A version skew here is something to *report*, never to repair
  by writing.
* **direct** — repowise owns the files. ``init`` writes the MCP registration
  and the augment hooks into ``~/.claude/settings.json`` plus a repo-local
  ``.mcp.json``. No skills, no commands.

Both routes register ``mcpServers.repowise`` and the same augment hook
commands, and Claude Code merges them without complaint, so a user on both
paths pays two process spawns per matched tool call and carries duplicate tool
schemas. That is a cost problem rather than a correctness one — the emission
dedup in ``augment_cmd`` means the agent still sees one notice — which is
exactly why :meth:`ClaudeCodeTarget.detect` reports every registration it finds
instead of collapsing them into a boolean.

The hook writing itself is *not* reimplemented here. ``editor_integrations.claude_config``
owns the settings.json hook merge and its legacy-matcher migrations, which
encode several years of shape changes; this module names it and calls it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..types import (
    Capability,
    DoctorReport,
    DoctorStatus,
    FileAction,
    FileWrite,
    InstallMethod,
    Registration,
    Scope,
    WriteResult,
)

ID = "claude-code"
DISPLAY_NAME = "Claude Code"
DOCS_URL = "https://docs.claude.com/en/docs/claude-code"

#: Name the plugin registers itself under in the host's plugin manifest.
PLUGIN_KEY = "repowise@repowise"

#: Config key for this agent's managed instruction file, under
#: ``editor_files`` in ``.repowise/config.yaml``. Owned here so the CLI flag
#: mapping can look it up rather than restate it per agent.
PROJECT_FILE_ID = "claude_md"

METHODS = (
    InstallMethod(
        id="plugin",
        provides=frozenset(
            {
                Capability.MCP,
                Capability.HOOKS,
                Capability.SKILLS,
                Capability.COMMANDS,
            }
        ),
        managed_by="host",
        preferred=True,
    ),
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.HOOKS, Capability.INSTRUCTIONS}),
        managed_by="repowise",
    ),
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def settings_path() -> Path:
    """The user-level Claude Code settings file."""
    from repowise.cli.editor_integrations.claude_config import _claude_code_settings_path

    return _claude_code_settings_path()


def desktop_config_path() -> Path | None:
    """Claude Desktop's config file, or None where the platform has none."""
    from repowise.cli.editor_integrations.claude_config import _claude_desktop_config_path

    return _claude_desktop_config_path()


def project_mcp_config_path(repo_path: Path) -> Path:
    """The repo-root ``.mcp.json`` MCP clients discover."""
    return repo_path / ".mcp.json"


def plugin_manifest_path() -> Path:
    """Where the host records which plugins are installed."""
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def write_project_mcp_config(repo_path: Path) -> FileWrite:
    """Merge the repowise server into the repo-root ``.mcp.json``.

    Repo-shared and frequently committed, so it keeps the bare ``repowise``
    command: one contributor's absolute path would break every other checkout.
    Other servers the user configured are preserved.
    """
    from repowise.cli.mcp_config import generate_mcp_config

    from ..formats.json_merge import (
        load_json_object,
        merge_server_entries,
        write_json_config,
    )

    config_path = project_mcp_config_path(repo_path)
    new_entry = generate_mcp_config(repo_path)["mcpServers"]

    if config_path.exists():
        existing = load_json_object(config_path)
        servers = dict(existing.get("mcpServers", {}))
        merge_server_entries(servers, new_entry)
        existing["mcpServers"] = servers
        merged = existing
    else:
        merged = {"mcpServers": new_entry}

    return FileWrite(path=config_path, action=write_json_config(config_path, merged))


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _plugin_installs() -> list[dict]:
    """Records the host holds for the repowise plugin, or an empty list.

    Reads the host's own manifest (``installed_plugins.json``, schema version 2:
    a ``plugins`` map from ``"<plugin>@<marketplace>"`` to a list of installs,
    each carrying ``scope``, ``installPath`` and ``version``). Every failure
    mode degrades to "no plugin": a manifest we cannot read is not evidence of
    absence, but treating it as presence would make us report a registration
    that may not exist.
    """
    manifest = plugin_manifest_path()
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return []
    installs = plugins.get(PLUGIN_KEY)
    if not isinstance(installs, list):
        return []
    return [entry for entry in installs if isinstance(entry, dict)]


#: What the host, not repowise, has to run to update the plugin.
PLUGIN_UPDATE_COMMAND = "/plugin update repowise@repowise"


def plugin_version_skew() -> list[str]:
    """Installed plugin versions that are not this CLI's version, sorted.

    The plugin is the one artifact the CLI cannot rewrite. ``pip install -U
    repowise`` upgrades the MCP server and every command, and leaves the plugin's
    skills and slash commands exactly where they were, so the two drift apart
    silently, and silence is the worst of the available behaviours. Measured on a
    real machine: an 0.16.0 plugin installed months earlier against a 0.41.0 CLI,
    which is why five slash commands the CLI had shipped did not exist in the
    session.

    Reads the host's own manifest through :func:`_plugin_installs` rather than a
    second parser. An entry with no ``version`` is skipped: it cannot be compared,
    and a report the user cannot act on is noise.

    Compared through :func:`release_key`, so ``0.41``, ``0.41.0`` and ``v0.41.0``
    are one release. Spelling drift is not drift, and reporting it produces a row
    no command can ever clear.
    """
    from repowise.cli import __version__

    current = release_key(__version__)
    return sorted(
        {
            str(entry["version"])
            for entry in _plugin_installs()
            if entry.get("version") and not _same_release(str(entry["version"]), current)
        }
    )


def release_key(version: str) -> tuple[int, ...] | None:
    """*version* as a comparable tuple, or None when it is not a plain release.

    Deliberately **not** ``core.upgrade.release.parse_release``, which answers a
    different question. That one exists to decide "is there a newer release", so
    it drops everything after the first non-digit: it reads ``0.41.0rc1`` and
    ``0.41.0.post1`` as plain ``0.41.0``, which is right for an upgrade prompt and
    wrong here: a release candidate is not the release. It also returns None for
    a leading ``v``, which is the single most likely way for a plugin manifest to
    spell a version.

    So: strip one leading ``v``, require every remaining part to be digits, and
    drop trailing zeros so ``0.41`` and ``0.41.0`` land on the same key. Anything
    with a suffix returns None and falls back to exact string comparison, which
    reports it, the conservative direction for a version we cannot reason about.
    """
    text = version.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = text.split(".")
    if not text or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def _same_release(version: str, current: tuple[int, ...] | None) -> bool:
    installed = release_key(version)
    if installed is None or current is None:
        from repowise.cli import __version__

        return version == __version__
    return installed == current


def _has_repowise_server(config_path: Path) -> bool:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and "repowise" in servers


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Every place Claude Code is currently wired to repowise.

    Deliberately a list. A machine carrying both the plugin and a direct
    ``init`` has two MCP registrations for one product, and the only way to
    tell the user that is to have counted them.
    """
    found: list[Registration] = []

    for entry in _plugin_installs():
        scope = Scope.USER if entry.get("scope") != "project" else Scope.PROJECT
        found.append(
            Registration(
                method="plugin",
                scope=scope,
                config_path=plugin_manifest_path(),
                version=entry.get("version"),
                detail=entry.get("installPath"),
            )
        )

    settings = settings_path()
    if settings.exists() and _has_repowise_server(settings):
        found.append(
            Registration(method="direct", scope=Scope.USER, config_path=settings),
        )

    desktop = desktop_config_path()
    if desktop is not None and desktop.exists() and _has_repowise_server(desktop):
        found.append(
            Registration(method="direct", scope=Scope.USER, config_path=desktop, detail="desktop"),
        )

    if repo_path is not None:
        project = project_mcp_config_path(repo_path)
        if project.exists() and _has_repowise_server(project):
            found.append(
                Registration(method="direct", scope=Scope.PROJECT, config_path=project),
            )

    return found


class ClaudeCodeTarget:
    """Descriptor for Claude Code. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = "claude-code"
    session_adapter = "claude_code"
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        return True

    def is_present(self, repo_path: Path | None = None) -> bool:
        """``~/.claude`` exists, or Claude Desktop's config directory does.

        Claude Code creates ``~/.claude`` on first run and never removes it, so
        its presence is the cheapest honest signal. Claude Desktop is checked
        separately because it is a different product with the same brand and a
        user can have either.
        """
        if (Path.home() / ".claude").is_dir():
            return True
        desktop = desktop_config_path()
        return desktop is not None and desktop.parent.is_dir()

    def detect(self, repo_path: Path | None = None) -> list[Registration]:
        return detect(repo_path)

    def install(
        self,
        scope: Scope,
        options: object = None,
        *,
        repo_path: Path | None = None,
    ) -> WriteResult:
        """Wire Claude Code up at *scope*.

        The direct method only. The plugin is host-managed by definition, so
        there is nothing here to write for it — when :meth:`detect` finds it,
        the caller is the one that decides whether to skip the direct write.
        """
        from repowise.cli.editor_integrations.claude_config import (
            enable_tool_search_in_claude_code,
            install_claude_code_hooks,
            register_with_claude_code,
            register_with_claude_desktop,
        )

        result = WriteResult()
        if scope is Scope.PROJECT:
            if repo_path is None:
                raise ValueError("project-scope install needs a repo_path")
            written = write_project_mcp_config(repo_path)
            result.record(written.path, written.action)
            return result

        if repo_path is None:
            raise ValueError("user-scope registration needs a repo_path to point at")

        from ..formats.observe import observed_action, read_bytes

        settings = settings_path()
        desktop_path = desktop_config_path()
        settings_before = read_bytes(settings)
        desktop_before = read_bytes(desktop_path) if desktop_path is not None else None

        register_with_claude_desktop(repo_path)
        register_with_claude_code(repo_path)
        install_claude_code_hooks()
        enable_tool_search_in_claude_code()

        # One entry per file, not one per call: three of the four calls above
        # write settings.json, and reporting it three times says nothing a
        # reader can act on.
        result.record(settings, observed_action(settings_before, read_bytes(settings)))
        if desktop_path is not None:
            after = read_bytes(desktop_path)
            if not (desktop_before is None and after is None):
                result.record(desktop_path, observed_action(desktop_before, after))
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Remove the distill rewrite hook; leave sibling user hooks intact.

        Scoped narrowly on purpose. Tearing the augment hooks and the MCP
        registration out is not something any command asks for today, and a
        half-written uninstall is worse than none — the surface grows in
        Phase 2 alongside the command that needs it.
        """
        from repowise.cli.editor_integrations.claude_config import (
            uninstall_claude_code_rewrite_hook,
        )

        result = WriteResult()
        settings = settings_path()
        if scope is not Scope.USER:
            result.record(settings, FileAction.KEPT)
            return result
        removed = uninstall_claude_code_rewrite_hook()
        result.record(settings, FileAction.REMOVED if removed else FileAction.NOT_FOUND)
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        """The MCP snippet to paste. Touches nothing."""
        from repowise.cli.mcp_config import generate_mcp_config

        target = repo_path or Path.cwd()
        return json.dumps(generate_mcp_config(target), indent=2)

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        if scope is Scope.PROJECT:
            return [str(project_mcp_config_path(repo_path or Path.cwd()))]
        paths = [str(settings_path())]
        desktop = desktop_config_path()
        if desktop is not None:
            paths.append(str(desktop))
        return paths

    def doctor(self) -> DoctorReport:
        """Health of the Claude Code wiring, with one command to fix it.

        Reports a stale rewrite-hook matcher explicitly. An entry whose matcher
        names a tool the host has since renamed is installed, parses, and will
        never fire — which is indistinguishable from working unless something
        says so out loud. This is also the surface that keeps the
        ``REPOWISE_SKIP_EDITOR_SETUP`` decision honest: someone who exports that
        var permanently never gets hook migrations, so the staleness has to be
        visible somewhere.
        """
        from repowise.cli.agent_adapters.claude_code import SHELL_TOOL_MATCHER
        from repowise.cli.editor_integrations.claude_config import (
            claude_code_rewrite_hook_matcher,
        )

        from ..formats.json_merge import is_damaged

        # Checked before detection, because detection cannot tell an absent
        # registration from one inside a file it could not parse — and
        # reporting "not installed" for a settings file with a trailing comma
        # sends the user to run an install that refuses for the same reason.
        settings = settings_path()
        if is_damaged(settings):
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.BROKEN,
                issues=(
                    f"{settings} is not valid JSON, so Claude Code ignores all of it. "
                    "Fix or remove it, then re-register.",
                ),
                # `add`, not `refresh`. A file this damaged makes detection
                # find nothing, and refresh only touches what it detects — so
                # it would skip this target entirely and report success. Which
                # is exactly why this is not repairable: `--repair` runs that
                # same refresh, so letting it try buys the skip and the false
                # success this comment was written about.
                fix_command="repowise agents add --target=claude-code",
                repairable=False,
            )

        registrations = detect()
        if not registrations:
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.NOT_INSTALLED,
                issues=("repowise is not registered with Claude Code.",),
                fix_command="repowise init",
            )

        issues: list[str] = []
        matcher = claude_code_rewrite_hook_matcher()
        # The one issue here that `agents refresh` genuinely rewrites, so it is
        # the one that decides `repairable`. Tracked separately from the issue
        # list because the two questions (what is wrong, and can the repair
        # pass fix it) have different answers per issue.
        matcher_stale = matcher is not None and matcher != SHELL_TOOL_MATCHER
        if matcher_stale:
            issues.append(
                f"The distill rewrite hook matches {matcher!r}, but Claude Code now "
                f"names its shell tools {SHELL_TOOL_MATCHER!r}. The hook is installed "
                "and will never fire."
            )

        duplicates = [r for r in registrations if r.method == "plugin"] and [
            r for r in registrations if r.method == "direct"
        ]
        if duplicates:
            issues.append(
                "repowise is registered twice — once by the plugin and once directly — "
                "so its MCP tool schemas and hooks are loaded twice."
            )

        # Reported last and fixed first: an out-of-date plugin ships its own
        # copy of the hooks, so updating it can resolve the matcher issue above
        # as a side effect, where repairing the matcher does nothing for it.
        fix_command = "repowise hook rewrite install"
        skew = plugin_version_skew()
        if skew:
            from repowise.cli import __version__

            versions = ", ".join(skew)
            current = release_key(__version__)
            # Ahead of the CLI is the same drift running the other way, and
            # telling someone to update an already-newer plugin is a dead end.
            # A version we cannot parse falls to the plugin side: an odd string
            # in a plugin manifest is far likelier than an odd CLI version.
            ahead = current is not None and all(
                (key := release_key(version)) is not None and key > current for version in skew
            )
            issues.append(
                f"The Claude Code plugin is at {versions} but this CLI is {__version__}. "
                "Its skills and slash commands are the plugin's, not the CLI's, and "
                "`pip install -U repowise` does not touch them."
            )
            fix_command = "pip install -U repowise" if ahead else PLUGIN_UPDATE_COMMAND

        # Only the stale matcher. The plugin is host-managed and a duplicate
        # registration is not something refresh removes, so on either of those
        # alone `--repair` would write global config for a problem it cannot
        # touch and then report success. But a *skewed plugin alongside* a stale
        # matcher must stay repairable: the matcher is still broken, still
        # rewritable, and the first cut of this let the skew suppress its repair.
        repairable = matcher_stale

        if not issues:
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.STALE,
            issues=tuple(issues),
            fix_command=fix_command,
            repairable=repairable,
        )


TARGET = ClaudeCodeTarget()
