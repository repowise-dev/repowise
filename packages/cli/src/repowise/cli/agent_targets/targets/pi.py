"""Pi as an agent target: MCP and managed instructions (Good tier).

Host facts checked against Pi's documentation and source:

* ``.pi/mcp.json`` and ``~/.pi/agent/mcp.json`` hold ``mcpServers`` with
  ``command`` and ``args`` entries. Servers start in the session directory,
  so the user entry needs no repository path.
  https://pi.dev/docs/latest/mcp
* ``AGENTS.md`` is read from the project and the agent directory.
  https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/resource-loader.ts
* ``PI_CODING_AGENT_DIR`` overrides the user directory, including ``~/``
  expansion. The ``pi`` executable is the installation probe.
  https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/config.ts

Prompt templates, skills, hooks and transcripts are not installed here.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from repowise.core.agents import identity

from ..formats import marker_block
from ..formats.json_merge import (
    load_json_object_or_value_error,
    merge_server_entries,
    write_json_config,
)
from ..formats.marker_block import BlockState
from ..formats.server_entry import RemoteServerEntryError, is_remote_entry
from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START, DISTILL_SECTION
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

IDENTITY = identity.PI
ID = IDENTITY.cli_target_id
DISPLAY_NAME = IDENTITY.display_name
DOCS_URL = "https://pi.dev/docs/latest/mcp"
PROJECT_FILE_ID = "agents_md"
SERVER_NAME = "repowise"
METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)


def config_dir(scope: Scope, repo_path: Path | None = None) -> Path:
    """Pi's project or user directory, following its environment override."""
    if scope is Scope.USER:
        configured = os.environ.get("PI_CODING_AGENT_DIR")
        return Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"
    if repo_path is None:
        raise ValueError("project scope needs a repo_path")
    return repo_path / ".pi"


def mcp_config_path(scope: Scope, repo_path: Path | None = None) -> Path:
    """The MCP config Pi reads at this scope."""
    return config_dir(scope, repo_path) / "mcp.json"


def instructions_path(scope: Scope, repo_path: Path | None = None) -> Path:
    """Project instructions share the root AGENTS.md with other targets."""
    directory = config_dir(scope, repo_path)
    return (directory.parent if scope is Scope.PROJECT else directory) / "AGENTS.md"


def server_entry(scope: Scope, repo_path: Path | None = None) -> dict:
    """A portable project command, or a pinned user command serving the session cwd."""
    from repowise.cli.mcp_config import generate_mcp_config, resolve_repowise_command

    if scope is Scope.USER:
        return {"command": resolve_repowise_command(), "args": ["mcp", "--transport", "stdio"]}
    if repo_path is None:
        raise ValueError("project scope needs a repo_path")
    generated = generate_mcp_config(repo_path)["mcpServers"][SERVER_NAME]
    return {"command": generated["command"], "args": generated["args"]}


def _servers(path: Path) -> tuple[dict, dict]:
    existing = load_json_object_or_value_error(path, path.name) if path.exists() else {}
    servers = existing.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path.name} 'mcpServers' must be a JSON object")
    return existing, servers


def write_mcp_config(scope: Scope, repo_path: Path | None = None) -> FileWrite:
    """Merge the local entry while preserving sibling servers and user options."""
    path = mcp_config_path(scope, repo_path)
    existing, servers = _servers(path)
    stored = servers.get(SERVER_NAME)
    if isinstance(stored, dict) and (
        "url" in stored or is_remote_entry(stored, local_type="stdio")
    ):
        raise RemoteServerEntryError('the "repowise" entry names a remote server')
    merge_server_entries(servers, {SERVER_NAME: server_entry(scope, repo_path)})
    existing["mcpServers"] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileWrite(path=path, action=write_json_config(path, existing))


def write_instructions(scope: Scope, repo_path: Path | None = None) -> FileWrite:
    """Upsert the common instructions without replacing the user's own text."""
    path = instructions_path(scope, repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    action = marker_block.upsert(
        path, f"\n{DISTILL_SECTION}\n", DISTILL_MARKER_START, DISTILL_MARKER_END
    )
    reason = None
    if action is FileAction.KEPT:
        state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
        reason = marker_block.refusal_reason(state)
    return FileWrite(path=path, action=action, reason=reason)


def _remove_instructions(scope: Scope, repo_path: Path | None) -> FileWrite:
    from ..registry import other_managers_of

    path = instructions_path(scope, repo_path)
    state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
    if state in (BlockState.ABSENT_FILE, BlockState.ABSENT):
        return FileWrite(path, FileAction.NOT_FOUND)
    owners = other_managers_of(path, exclude=ID, scope=scope, repo_path=repo_path)
    if owners:
        return FileWrite(path, FileAction.KEPT, f"{' and '.join(owners)} still reads this block")
    if marker_block.remove(path, DISTILL_MARKER_START, DISTILL_MARKER_END):
        return FileWrite(path, FileAction.REMOVED)
    action = FileAction.FAILED if state is BlockState.PRESENT else FileAction.KEPT
    return FileWrite(path, action, marker_block.refusal_reason(state))


class PiTarget:
    """Descriptor for Pi's built-in MCP client and instructions reader."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = IDENTITY.hook_adapter
    session_adapter = IDENTITY.session_adapter
    capture_adapter = IDENTITY.capture_adapter
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        return True

    def is_present(self, repo_path: Path | None = None) -> bool:
        return IDENTITY.is_installed() or config_dir(Scope.USER).is_dir()

    def detect(self, repo_path: Path | None = None) -> list[Registration]:
        found = []
        scopes = [Scope.USER, Scope.PROJECT] if repo_path is not None else [Scope.USER]
        for scope in scopes:
            path = mcp_config_path(scope, repo_path)
            try:
                _, servers = _servers(path)
            except (OSError, ValueError):
                continue
            if SERVER_NAME in servers:
                found.append(Registration(method="direct", scope=scope, config_path=path))
        return found

    def install(
        self, scope: Scope, options: object = None, *, repo_path: Path | None = None
    ) -> WriteResult:
        result = WriteResult()
        paths = (mcp_config_path(scope, repo_path), instructions_path(scope, repo_path))
        for writer, path in zip((write_mcp_config, write_instructions), paths, strict=True):
            try:
                written = writer(scope, repo_path)
                result.record(written.path, written.action, written.reason)
                if written.reason:
                    result.note(f"{path} left unchanged: {written.reason}.")
            except (OSError, ValueError) as exc:
                result.record(path, FileAction.KEPT, str(exc))
                result.note(
                    f"{path} left unchanged ({exc}). Run 'repowise agents print-config pi "
                    f"--scope={scope.value}' for the MCP entry."
                )
        if result.changed:
            result.note("Run /reload in Pi to load the updated configuration.")
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        from .cursor import _remove_server_entry

        result = WriteResult()
        path = mcp_config_path(scope, repo_path)
        try:
            result.record(*_remove_server_entry(path))
        except (OSError, ValueError) as exc:
            result.record(path, FileAction.FAILED, str(exc))
        written = _remove_instructions(scope, repo_path)
        result.record(written.path, written.action, written.reason)
        if written.reason:
            result.note(f"{written.path} kept: {written.reason}.")
        if any(f.action is FileAction.REMOVED for f in result.files):
            directory = config_dir(scope, repo_path)
            if not directory.is_symlink():
                with contextlib.suppress(OSError):
                    directory.rmdir()
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        return json.dumps(
            {"mcpServers": {SERVER_NAME: server_entry(scope, repo_path or Path.cwd())}}, indent=2
        )

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        repo = repo_path or Path.cwd()
        return [str(mcp_config_path(scope, repo)), str(instructions_path(scope, repo))]

    def doctor(self, repo_path: Path | None = None) -> DoctorReport:
        scopes = [Scope.USER, Scope.PROJECT] if repo_path is not None else [Scope.USER]
        for scope in scopes:
            path = mcp_config_path(scope, repo_path)
            try:
                _servers(path)
            except (OSError, ValueError) as exc:
                return DoctorReport(
                    target_id=ID,
                    status=DoctorStatus.BROKEN,
                    issues=(f"{path} cannot be read ({exc}).",),
                    repairable=False,
                    fix_command="repowise agents print-config pi",
                )
        if self.detect(repo_path):
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.NOT_INSTALLED,
            issues=("Pi is not wired to Repowise at the checked scopes.",),
            fix_command="repowise agents add --target=pi",
        )


TARGET = PiTarget()
