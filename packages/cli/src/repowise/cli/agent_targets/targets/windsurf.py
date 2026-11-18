"""Windsurf as an agent target.

Basic tier: repowise writes the MCP server entry and nothing that tells the
agent when to use it. Windsurf has no hook protocol or transcript format for
repowise to use.

Windsurf reads MCP servers from one per-user file,
``~/.codeium/windsurf/mcp_config.json``, keyed under ``mcpServers``:
https://docs.windsurf.com/windsurf/cascade/mcp

* A **local** server is a ``command`` and ``args``. The file is per user with
  no project scope, so the entry names its repo, the way Claude Desktop's does,
  and the same rule decides when an entry serving another repo moves:
  :func:`~repowise.cli.editor_integrations.claude_config.planned_global_entry`.
* A **remote** server is ``serverUrl`` rather than ``url``, which is the one
  shape difference from every other host. The hosted entry
  (``agents add --hosted``) goes here too.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.agents import identity

from ..formats.server_entry import (
    LOCAL_NAME,
    RENAME_HINT,
    RemoteServerEntryError,
    ServerSpec,
    is_remote_entry,
)
from ..types import (
    Capability,
    DoctorReport,
    DoctorStatus,
    FileAction,
    FileWrite,
    HostedSupport,
    InstallMethod,
    InstallOptions,
    Registration,
    Scope,
    WriteResult,
)

IDENTITY = identity.WINDSURF
ID = IDENTITY.cli_target_id
DISPLAY_NAME = IDENTITY.display_name
DOCS_URL = "https://docs.windsurf.com/windsurf/cascade/mcp"

#: Config key gating this agent's managed project files. Windsurf has none
#: today; the id exists because every descriptor names one.
PROJECT_FILE_ID = "windsurf_mcp"

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP}),
        managed_by="repowise",
        preferred=True,
    ),
)

#: One file per user, so the hosted entry serves the repo it names everywhere.
HOSTED = HostedSupport(
    scope=Scope.USER,
    next_step=(
        'Open Windsurf Settings > Cascade > MCP servers, refresh, and approve "{name}" '
        "in the browser when Windsurf asks."
    ),
)


def config_path() -> Path:
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def _servers() -> dict:
    """The ``mcpServers`` map, or ``{}`` when absent or unreadable."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else {}


def _local_entry() -> dict | None:
    """Our local ``repowise`` entry, or ``None`` when absent or remote."""
    stored = _servers().get(LOCAL_NAME)
    if isinstance(stored, dict) and not is_remote_entry(stored, local_type="stdio"):
        return stored
    return None


def write_server(spec: ServerSpec) -> tuple[str, FileWrite]:
    """Merge *spec* (the local server or the hosted one) into ``mcp_config.json``.

    Raises ``ValueError`` on a file that is not strict JSON, and
    ``RemoteServerEntryError`` for a local write onto a remote ``repowise``.
    """
    from ..formats.server_entry import write_json_servers

    return write_json_servers(config_path(), "mcpServers", spec, local_type="stdio")


def detect(repo_path: Path | None = None) -> list[Registration]:
    """The local ``repowise`` entry, at user scope. A hosted entry is not counted."""
    if _local_entry() is None:
        return []
    return [Registration(method="direct", scope=Scope.USER, config_path=config_path())]


class WindsurfTarget:
    """Descriptor for Windsurf. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = IDENTITY.hook_adapter
    session_adapter = IDENTITY.session_adapter
    capture_adapter = IDENTITY.capture_adapter
    methods = METHODS
    project_file_id = PROJECT_FILE_ID
    hosted = HOSTED

    def supports_scope(self, scope: Scope) -> bool:
        """User scope only: Windsurf has no project MCP file."""
        return scope is Scope.USER

    def is_present(self, repo_path: Path | None = None) -> bool:
        """``windsurf`` on PATH, or the ``~/.codeium/windsurf`` directory it creates."""
        return IDENTITY.is_installed() or config_path().parent.is_dir()

    def detect(self, repo_path: Path | None = None) -> list[Registration]:
        return detect(repo_path)

    def install(
        self,
        scope: Scope,
        options: object = None,
        *,
        repo_path: Path | None = None,
    ) -> WriteResult:
        from repowise.cli.editor_integrations.claude_config import planned_global_entry

        from ..formats.server_entry import install_hosted

        result = WriteResult()
        if scope is not Scope.USER:
            return result
        if repo_path is None:
            raise ValueError("user-scope registration needs a repo_path to point at")
        hosted_url = options.hosted_url if isinstance(options, InstallOptions) else None
        if hosted_url:
            return install_hosted(config_path(), write_server, {"serverUrl": hosted_url})

        retarget = isinstance(options, InstallOptions) and options.retarget
        entry = planned_global_entry(
            _local_entry(), repo_path, follows_launch_dir=False, retarget=retarget
        )
        if entry is None:
            result.record(
                config_path(),
                FileAction.KEPT,
                "it serves another repo; 'repowise agents add --target=windsurf' moves it",
            )
            return result
        try:
            _, written = write_server(ServerSpec(entry))
            result.record(written.path, written.action)
        except RemoteServerEntryError:
            result.record(config_path(), FileAction.KEPT, RENAME_HINT)
            result.note(f"{config_path().name} left unchanged: {RENAME_HINT}.")
        except (ValueError, OSError) as exc:
            result.record(config_path(), FileAction.KEPT, f"could not be rewritten ({exc})")
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Drop the local ``repowise`` entry, keeping every other server."""
        from ..formats.json_merge import load_json_object_or_value_error, write_json_config

        result = WriteResult()
        path = config_path()
        if scope is not Scope.USER:
            return result
        if _local_entry() is None:
            result.record(path, FileAction.NOT_FOUND)
            return result
        try:
            existing = load_json_object_or_value_error(path, path.name)
            existing["mcpServers"].pop(LOCAL_NAME)
            write_json_config(path, existing)
        except (OSError, ValueError) as exc:
            result.record(path, FileAction.KEPT, f"could not be rewritten ({exc})")
            return result
        result.record(path, FileAction.REMOVED)
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        from repowise.cli.mcp_config import generate_mcp_config

        entry = generate_mcp_config(repo_path or Path.cwd())["mcpServers"][LOCAL_NAME]
        return json.dumps({"mcpServers": {LOCAL_NAME: entry}}, indent=2)

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        return [str(config_path())] if scope is Scope.USER else []

    def doctor(self, repo_path: Path | None = None) -> DoctorReport:
        from ..formats.json_merge import is_damaged

        fix = "repowise agents add --target=windsurf --scope=user"
        if is_damaged(config_path()):
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.BROKEN,
                issues=(f"{config_path()} is not valid JSON, so Windsurf loads no MCP servers.",),
                fix_command=fix,
                repairable=False,
            )
        if _local_entry() is None:
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.NOT_INSTALLED,
                issues=("The repowise MCP server is not registered with Windsurf.",),
                fix_command=fix,
            )
        return DoctorReport(target_id=ID, status=DoctorStatus.OK)


TARGET = WindsurfTarget()
