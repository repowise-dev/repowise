"""Kiro as an agent target.

Good tier: MCP plus a steering file, no hook or transcript adapter. Kiro's two
hook formats disagree in its own docs, so none are written.

Host facts, each checked against the source named beside it:

* MCP lives in ``.kiro/settings/mcp.json`` (workspace) and
  ``~/.kiro/settings/mcp.json`` (user) under ``mcpServers``; an entry is
  ``command`` plus ``args``, with no ``type`` field.
  https://kiro.dev/docs/mcp/configuration
* Every ``*.md`` in ``.kiro/steering/`` (or ``~/.kiro/steering/``) is loaded as
  context, so repowise owns ``steering/repowise.md`` outright and needs no
  marker block. No frontmatter: the inclusion key names are unverified.
  https://kiro.dev/docs/steering
* Kiro IDE ships with MCP turned off; the CLI reads the same file ungated
  (codegraph ``kiro.ts``). Install says so.
"""

from __future__ import annotations

import contextlib
import json
import shutil
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

IDENTITY = identity.KIRO
ID = IDENTITY.cli_target_id
DISPLAY_NAME = IDENTITY.display_name
DOCS_URL = "https://kiro.dev/docs/mcp/configuration"

PROJECT_FILE_ID = "kiro_steering"

SERVER_NAME = "repowise"

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)

#: First line of every steering file repowise writes; how uninstall knows the
#: file is ours rather than a hand-written one with the same name.
STEERING_HEADER = "<!-- Managed by Repowise. 'repowise agents remove --target=kiro' deletes it. -->"


def steering_text() -> str:
    from ..instructions import DISTILL_SECTION

    return f"{STEERING_HEADER}\n\n{DISTILL_SECTION}\n"


def config_dir(scope: Scope, repo_path: Path | None = None) -> Path:
    if scope is Scope.USER:
        return Path.home() / ".kiro"
    if repo_path is None:
        raise ValueError("project scope needs a repo_path")
    return repo_path / ".kiro"


def mcp_config_path(scope: Scope, repo_path: Path | None = None) -> Path:
    return config_dir(scope, repo_path) / "settings" / "mcp.json"


def steering_path(scope: Scope, repo_path: Path | None = None) -> Path:
    return config_dir(scope, repo_path) / "steering" / "repowise.md"


def server_entry(scope: Scope, repo_path: Path | None = None) -> dict:
    """Project: bare command plus repo path, like ``.mcp.json``. User: pinned, no path."""
    from repowise.cli.mcp_config import generate_mcp_config, resolve_repowise_command

    if scope is Scope.USER:
        return {"command": resolve_repowise_command(), "args": ["mcp", "--transport", "stdio"]}
    if repo_path is None:
        raise ValueError("project scope needs a repo_path")
    generated = generate_mcp_config(repo_path)["mcpServers"][SERVER_NAME]
    return {"command": generated["command"], "args": generated["args"]}


def write_mcp_config(scope: Scope, repo_path: Path | None = None) -> FileWrite:
    """Merge our entry into ``mcp.json``; raises ``ValueError`` on a bad file."""
    from ..formats.json_merge import (
        load_json_object_or_value_error,
        merge_server_entries,
        write_json_config,
    )
    from ..formats.server_entry import is_remote_entry

    path = mcp_config_path(scope, repo_path)
    existing = load_json_object_or_value_error(path, path.name) if path.exists() else {}
    servers = existing.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path.name} 'mcpServers' must be a JSON object")
    servers = dict(servers)
    stored = servers.get(SERVER_NAME)
    if isinstance(stored, dict) and is_remote_entry(stored, local_type="stdio"):
        raise RemoteServerEntryError(f"{path.name} 'repowise' is wired to a remote server")
    merge_server_entries(servers, {SERVER_NAME: server_entry(scope, repo_path)})
    existing["mcpServers"] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileWrite(path=path, action=write_json_config(path, existing))


def _read(path: Path) -> str | None:
    """File text, ``None`` when absent; raises ``OSError``/``ValueError`` when unreadable."""
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_steering(scope: Scope, repo_path: Path | None = None) -> tuple[FileWrite, str | None]:
    """Write the whole steering file; a same-named file we did not write is kept."""
    from ..formats.json_merge import atomic_write_text

    path = steering_path(scope, repo_path)
    text = steering_text()
    current = _read(path)
    if current is not None:
        if current.replace("\r\n", "\n") == text:
            return FileWrite(path=path, action=FileAction.UNCHANGED), None
        if not current.startswith(STEERING_HEADER):
            return FileWrite(path=path, action=FileAction.KEPT), "not written by repowise"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text, newline="\n")
    action = FileAction.CREATED if current is None else FileAction.UPDATED
    return FileWrite(path=path, action=action), None


def _remove_steering(path: Path) -> tuple[Path, FileAction, str | None]:
    try:
        current = _read(path)
    except (OSError, ValueError):
        return path, FileAction.KEPT, "the file could not be read, so its contents are unknown"
    if current is None:
        return path, FileAction.NOT_FOUND, None
    if not current.startswith(STEERING_HEADER):
        return path, FileAction.KEPT, "not written by repowise"
    try:
        path.unlink()
    except OSError as exc:
        return path, FileAction.FAILED, f"could not be deleted ({exc})"
    return path, FileAction.REMOVED, None


def _reads_repowise(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Every ``mcp.json`` naming repowise, user scope first. Never raises."""
    scopes: list[tuple[Scope, Path | None]] = [(Scope.USER, None)]
    if repo_path is not None:
        scopes.append((Scope.PROJECT, repo_path))
    return [
        Registration(method="direct", scope=scope, config_path=mcp_config_path(scope, repo))
        for scope, repo in scopes
        if _reads_repowise(mcp_config_path(scope, repo))
    ]


def _prune_dirs(scope: Scope, repo_path: Path | None) -> None:
    """``rmdir`` what uninstall emptied; a non-empty or linked dir is left."""
    root = config_dir(scope, repo_path)
    for candidate in (root / "settings", root / "steering", root):
        if candidate.is_symlink():
            continue
        with contextlib.suppress(OSError):
            candidate.rmdir()


class KiroTarget:
    """Descriptor for Kiro. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = IDENTITY.hook_adapter
    session_adapter = IDENTITY.session_adapter
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        return True

    def is_present(self, repo_path: Path | None = None) -> bool:
        """``kiro-cli`` or the IDE's ``kiro`` on PATH, or a ``~/.kiro`` dir."""
        if IDENTITY.is_installed() or shutil.which("kiro"):
            return True
        return (Path.home() / ".kiro").is_dir()

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
        if scope is Scope.PROJECT and repo_path is None:
            raise ValueError("project-scope install needs a repo_path")

        path = mcp_config_path(scope, repo_path)
        try:
            written = write_mcp_config(scope, repo_path)
            result.record(written.path, written.action)
        except RemoteServerEntryError:
            result.record(path, FileAction.KEPT, 'its "repowise" entry names a remote server')
            result.note(
                f'{path} left unchanged: its "repowise" entry names a remote server. Run '
                "'repowise agents remove --target=kiro' first if you want the local server."
            )
        except (OSError, ValueError) as exc:
            result.record(path, FileAction.KEPT, f"could not be rewritten ({exc})")
            result.note(
                f"{path} left unchanged ({exc}). Run 'repowise agents print-config kiro' "
                'and paste the entry under "mcpServers".'
            )

        try:
            steering, reason = write_steering(scope, repo_path)
            result.record(steering.path, steering.action, reason)
            if reason:
                result.note(f"{steering.path} left unchanged: {reason}.")
        except (OSError, ValueError) as exc:
            steering_file = steering_path(scope, repo_path)
            result.record(steering_file, FileAction.KEPT, f"could not be written ({exc})")
            result.note(f"{steering_file} could not be written ({exc}).")

        if result.changed:
            result.note("Kiro IDE ships with MCP disabled; enable MCP in its settings.")
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        result = WriteResult()
        if scope is Scope.PROJECT and repo_path is None:
            raise ValueError("project-scope uninstall needs a repo_path")
        from .cursor import _remove_server_entry

        result.record(*_remove_server_entry(mcp_config_path(scope, repo_path)))
        result.record(*_remove_steering(steering_path(scope, repo_path)))
        if any(f.action is FileAction.REMOVED for f in result.files):
            _prune_dirs(scope, repo_path)
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        repo = None if scope is Scope.USER else repo_path or Path.cwd()
        return json.dumps({"mcpServers": {SERVER_NAME: server_entry(scope, repo)}}, indent=2)

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        repo = repo_path or Path.cwd()
        return [str(mcp_config_path(scope, repo)), str(steering_path(scope, repo))]

    def doctor(self) -> DoctorReport:
        """User-scope health; the workspace half needs a repo to check."""
        from ..formats.json_merge import load_json_object_or_value_error

        path = mcp_config_path(Scope.USER)
        if _reads_repowise(path):
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)
        if path.exists():
            try:
                load_json_object_or_value_error(path, path.name)
            except (OSError, ValueError):
                return DoctorReport(
                    target_id=ID,
                    status=DoctorStatus.BROKEN,
                    issues=(f"{path} is not a JSON object, so Kiro cannot read it.",),
                    repairable=False,
                    fix_command="repowise agents add --target=kiro",
                )
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.NOT_INSTALLED,
            issues=(
                "Kiro is not wired at user scope; workspace wiring is repo-local, "
                "so run this from a repo to check that half.",
            ),
            fix_command="repowise agents add --target=kiro",
        )


TARGET = KiroTarget()
