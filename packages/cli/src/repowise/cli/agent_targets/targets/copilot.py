"""GitHub Copilot CLI as an agent target.

Good tier: MCP plus the managed instructions block, no hook or transcript
adapter yet.

Host facts, each checked against the source named beside it:

* MCP lives in ``~/.copilot/mcp-config.json`` (``$COPILOT_HOME`` overrides the
  directory) under ``mcpServers``; the entry is
  ``{"type": "stdio", "command", "args", "tools": ["*"]}``.
  https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference
* There is no project-local MCP file (github/copilot-cli#2528), so the one
  user entry names no repo and the server resolves the repo it is launched in.
* The CLI reads ``.github/copilot-instructions.md``, the file the ``vscode``
  target already manages, so project scope shares that block rather than
  writing a second file (same reference page).
* The VS Code extension creates ``~/.copilot/ide/``, so that directory alone is
  not evidence the CLI is installed (codegraph ``copilot-cli.ts``).
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from repowise.core.agents import identity

from ..formats.server_entry import RemoteServerEntryError
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
from . import vscode

IDENTITY = identity.COPILOT
ID = IDENTITY.cli_target_id
DISPLAY_NAME = IDENTITY.display_name
DOCS_URL = (
    "https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference"
)

#: Same switch as VS Code, because it is the same project file.
PROJECT_FILE_ID = vscode.PROJECT_FILE_ID

SERVER_NAME = "repowise"

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)


def config_dir() -> Path:
    """``$COPILOT_HOME`` when set and not blank, else ``~/.copilot``."""
    configured = (os.environ.get("COPILOT_HOME") or "").strip()
    return Path(configured) if configured else Path.home() / ".copilot"


def mcp_config_path() -> Path:
    return config_dir() / "mcp-config.json"


def server_entry() -> dict:
    """The user-scope entry: pinned binary, no repo path (see module docstring)."""
    from repowise.cli.mcp_config import resolve_repowise_command

    return {
        "type": "stdio",
        "command": resolve_repowise_command(),
        "args": ["mcp", "--transport", "stdio"],
    }


def write_mcp_config() -> FileWrite:
    """Merge our entry into ``mcp-config.json``, keeping sibling servers.

    ``tools`` is seeded only on a new entry: a user who narrowed it chose to.
    Raises ``ValueError`` for a file that is not a JSON object.
    """
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config
    from ..formats.server_entry import is_remote_entry

    path = mcp_config_path()
    existing = load_json_object_or_value_error(path, path.name) if path.exists() else {}
    servers = existing.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path.name} 'mcpServers' must be a JSON object")
    stored = servers.get(SERVER_NAME)
    if isinstance(stored, dict):
        # Copilot accepts "local" as well as "stdio" for a launched command.
        if stored.get("type") != "local" and is_remote_entry(stored, local_type="stdio"):
            raise RemoteServerEntryError(f"{path.name} 'repowise' is wired to a remote server")
        entry = {**stored, **server_entry()}
    else:
        entry = {**server_entry(), "tools": ["*"]}
    existing["mcpServers"] = {**servers, SERVER_NAME: entry}
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileWrite(path=path, action=write_json_config(path, existing))


def _reads_repowise() -> bool:
    try:
        data = json.loads(mcp_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def _has_managed_block(repo_path: Path) -> bool:
    from ..formats import marker_block
    from ..formats.marker_block import BlockState
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START

    path = vscode.instructions_path(repo_path)
    state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
    return state is BlockState.PRESENT


def detect(repo_path: Path | None = None) -> list[Registration]:
    """User registration from ``mcp-config.json``; project only alongside it.

    The project block is shared with VS Code, so on its own it says nothing
    about Copilot CLI. Requiring the user entry too is the Hermes rule, and it
    is what ``registry.other_managers_of`` relies on.
    """
    if not _reads_repowise():
        return []
    found = [Registration(method="direct", scope=Scope.USER, config_path=mcp_config_path())]
    if repo_path is not None and _has_managed_block(repo_path):
        found.append(
            Registration(
                method="direct",
                scope=Scope.PROJECT,
                config_path=vscode.instructions_path(repo_path),
                detail="managed instructions block",
            )
        )
    return found


def _prune_config_dir() -> None:
    """Remove the config dir only if our uninstall left it empty."""
    directory = config_dir()
    if directory.is_symlink():
        return
    with contextlib.suppress(OSError):
        directory.rmdir()


class CopilotTarget:
    """Descriptor for GitHub Copilot CLI. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = IDENTITY.hook_adapter
    session_adapter = IDENTITY.session_adapter
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        """User scope is the MCP entry, project scope the instructions block."""
        return True

    def is_present(self, repo_path: Path | None = None) -> bool:
        """``copilot`` on PATH, or a config dir holding more than ``ide/``."""
        if IDENTITY.is_installed():
            return True
        try:
            return any(entry.name != "ide" for entry in config_dir().iterdir())
        except OSError:
            return False

    def detect(self, repo_path: Path | None = None) -> list[Registration]:
        return detect(repo_path)

    def install(
        self,
        scope: Scope,
        options: object = None,
        *,
        repo_path: Path | None = None,
    ) -> WriteResult:
        result = WriteResult()
        if scope is Scope.PROJECT:
            if repo_path is None:
                raise ValueError("project-scope install needs a repo_path")
            try:
                written = vscode.write_instructions(repo_path)
            except (OSError, ValueError) as exc:
                path = vscode.instructions_path(repo_path)
                result.record(path, FileAction.KEPT, f"could not be written ({exc})")
                result.note(f"{path} could not be written ({exc}).")
                return result
            result.record(written.path, written.action)
            if written.action is FileAction.KEPT:
                result.note(
                    f"{written.path} left unchanged: its Repowise markers are unpaired or "
                    "duplicated, or the file could not be read. Fix it by hand and re-run."
                )
            return result

        path = mcp_config_path()
        try:
            written = write_mcp_config()
            result.record(written.path, written.action)
        except RemoteServerEntryError:
            result.record(path, FileAction.KEPT, 'its "repowise" entry names a remote server')
            result.note(
                f'{path} left unchanged: its "repowise" entry names a remote server. Run '
                "'repowise agents remove --target=copilot' first if you want the local "
                "server instead."
            )
        except (OSError, ValueError) as exc:
            result.record(path, FileAction.KEPT, f"could not be rewritten ({exc})")
            result.note(
                f"{path} left unchanged ({exc}). Run 'repowise agents print-config "
                "copilot --scope=user' and paste the entry under \"mcpServers\"."
            )
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        result = WriteResult()
        if scope is Scope.PROJECT:
            if repo_path is None:
                raise ValueError("project-scope uninstall needs a repo_path")
            path, action, reason = vscode.remove_instructions(repo_path, exclude=ID)
            result.record(path, action, reason)
            if action is FileAction.KEPT:
                result.note(f"{path} kept: {reason}.")
            return result

        from .cursor import _remove_server_entry

        path, action, reason = _remove_server_entry(mcp_config_path())
        result.record(path, action, reason)
        if action is FileAction.REMOVED:
            _prune_config_dir()
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        """The user entry; Copilot CLI has no project MCP file to paste into."""
        return json.dumps(
            {"mcpServers": {SERVER_NAME: {**server_entry(), "tools": ["*"]}}}, indent=2
        )

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        if scope is Scope.USER:
            return [str(mcp_config_path())]
        return [str(vscode.instructions_path(repo_path or Path.cwd()))]

    def doctor(self) -> DoctorReport:
        """User-scope health; the project block needs a repo to check."""
        from ..formats.json_merge import load_json_object_or_value_error

        path = mcp_config_path()
        if _reads_repowise():
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)
        if path.exists():
            try:
                load_json_object_or_value_error(path, path.name)
            except (OSError, ValueError):
                return DoctorReport(
                    target_id=ID,
                    status=DoctorStatus.BROKEN,
                    issues=(f"{path} is not a JSON object, so Copilot CLI cannot read it.",),
                    repairable=False,
                    fix_command="repowise agents add --target=copilot",
                )
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.NOT_INSTALLED,
            issues=("GitHub Copilot CLI is not wired at user scope.",),
            fix_command="repowise agents add --target=copilot",
        )


TARGET = CopilotTarget()
