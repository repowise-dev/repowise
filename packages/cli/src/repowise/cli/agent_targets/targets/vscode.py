"""VS Code as an agent target.

Good tier, and the descriptor makes that structural rather than editorial: it
names neither a hook adapter nor a transcript adapter, so :func:`derive_tier`
cannot place it at Full however many files it writes. VS Code gets the MCP
server and an extension recommendation; it has no hook protocol for repowise to
intercept tool calls through, and no transcript format to mine.

The distinguishing quirk is that both files it writes may legally contain
comments — VS Code accepts JSONC throughout ``.vscode/``. So this target is the
one that *declines* rather than repairs: a file it cannot parse is far more
likely to be a commented config than a damaged one, and rewriting it would
silently delete the user's comments. Both writers raise ``ValueError`` and the
caller reports what to add by hand.

Worth recording for Phase 5: Cursor does **not** read ``.vscode/mcp.json``. It
uses ``.cursor/mcp.json`` and exposes no provider API, so it is a separate
target rather than a flag on this one.
"""

from __future__ import annotations

import json
from pathlib import Path

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

ID = "vscode"
DISPLAY_NAME = "VS Code"
DOCS_URL = "https://code.visualstudio.com/docs/copilot/chat/mcp-servers"

EXTENSION_ID = "repowise-dev.repowise"

#: Config key gating this agent's managed project files.
PROJECT_FILE_ID = "vscode_mcp"

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP}),
        managed_by="repowise",
        preferred=True,
    ),
)


def mcp_config_path(repo_path: Path) -> Path:
    return repo_path / ".vscode" / "mcp.json"


def extensions_config_path(repo_path: Path) -> Path:
    return repo_path / ".vscode" / "extensions.json"


def server_entry(repo_path: Path) -> dict:
    """The ``.vscode/mcp.json`` server entry.

    VS Code keys stdio servers under a top-level ``servers`` map and expects a
    ``type`` field. Command and path convention mirror the repo-shared
    ``.mcp.json`` exactly, so a committed workspace config resolves the same
    server on every contributor's checkout.
    """
    from repowise.cli.mcp_config import generate_mcp_config

    entry = generate_mcp_config(repo_path)["mcpServers"]["repowise"]
    return {"type": "stdio", **entry}


def write_mcp_config(repo_path: Path) -> FileWrite:
    """Merge the repowise server into ``.vscode/mcp.json``.

    Raises ``ValueError`` when the existing file is not strict JSON or is not
    shaped as expected, so the caller can skip rather than destroy a JSONC file.
    """
    from ..formats.json_merge import (
        load_json_object_or_value_error,
        merge_server_entries,
        write_json_config,
    )
    from ..formats.server_entry import is_remote_entry

    config_path = mcp_config_path(repo_path)
    new_entry = {"repowise": server_entry(repo_path)}

    if config_path.exists():
        existing = load_json_object_or_value_error(config_path, "mcp.json")
        servers = existing.get("servers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcp.json 'servers' must be a JSON object")
        servers = dict(servers)
        stored = servers.get("repowise")
        if isinstance(stored, dict) and is_remote_entry(stored, local_type="stdio"):
            # VS Code documents ``"type": "http"`` in this file, so a hand-wired
            # remote repowise server is a shape that really turns up. See
            # ``formats.server_entry``: the merge would force ``type`` back to
            # ``stdio`` and keep the ``url`` beside it, leaving an entry that is
            # neither.
            raise RemoteServerEntryError("mcp.json 'repowise' is wired to a remote server")
        merge_server_entries(servers, new_entry)
        existing["servers"] = servers
        merged = existing
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        merged = {"servers": new_entry}

    return FileWrite(path=config_path, action=write_json_config(config_path, merged))


def write_extensions_config(repo_path: Path) -> FileWrite:
    """Recommend the repowise extension, preserving existing entries."""
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config

    config_path = extensions_config_path(repo_path)

    if config_path.exists():
        existing = load_json_object_or_value_error(config_path, "extensions.json")
        recommendations = existing.get("recommendations", [])
        if not isinstance(recommendations, list):
            raise ValueError("extensions.json 'recommendations' must be a JSON array")
        recommendations = list(recommendations)
        if EXTENSION_ID not in recommendations:
            recommendations.append(EXTENSION_ID)
        existing["recommendations"] = recommendations
        merged = existing
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        merged = {"recommendations": [EXTENSION_ID]}

    return FileWrite(path=config_path, action=write_json_config(config_path, merged))


def _remove_server_entry(config_path: Path) -> tuple[Path, FileAction]:
    """Drop ``servers.repowise``, preserving sibling servers."""
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config

    if not config_path.exists():
        return config_path, FileAction.NOT_FOUND
    try:
        existing = load_json_object_or_value_error(config_path, "mcp.json")
    except ValueError:
        # Same reason install declines: it is far more likely to be JSONC than
        # damaged, and rewriting it would silently delete the user's comments.
        return config_path, FileAction.KEPT

    servers = existing.get("servers")
    if not isinstance(servers, dict) or "repowise" not in servers:
        return config_path, FileAction.NOT_FOUND
    servers.pop("repowise")
    write_json_config(config_path, existing)
    return config_path, FileAction.REMOVED


def _remove_extension_recommendation(config_path: Path) -> tuple[Path, FileAction]:
    """Drop our id from ``recommendations``, preserving everyone else's."""
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config

    if not config_path.exists():
        return config_path, FileAction.NOT_FOUND
    try:
        existing = load_json_object_or_value_error(config_path, "extensions.json")
    except ValueError:
        return config_path, FileAction.KEPT

    recommendations = existing.get("recommendations")
    if not isinstance(recommendations, list) or EXTENSION_ID not in recommendations:
        return config_path, FileAction.NOT_FOUND
    existing["recommendations"] = [r for r in recommendations if r != EXTENSION_ID]
    write_json_config(config_path, existing)
    return config_path, FileAction.REMOVED


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Whether the workspace MCP config names repowise.

    Project scope only: VS Code reads the workspace file, and repowise writes
    nothing user-level for it.
    """
    if repo_path is None:
        return []
    config_path = mcp_config_path(repo_path)
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A JSONC workspace file is unparseable here but may well be wired up.
        # Reporting "not configured" would be a guess, so report nothing.
        #
        # ``ValueError`` rather than ``json.JSONDecodeError`` so a file that is
        # not UTF-8 lands here too. It is a ``UnicodeDecodeError``, which is a
        # ``ValueError`` and not a ``JSONDecodeError``, so it used to escape a
        # probe contracted never to raise.
        return []
    servers = data.get("servers")
    if not isinstance(servers, dict) or "repowise" not in servers:
        return []
    return [Registration(method="direct", scope=Scope.PROJECT, config_path=config_path)]


class VSCodeTarget:
    """Descriptor for VS Code. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = None
    session_adapter = None
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        """Project scope only — there is no user-level file repowise writes."""
        return scope is Scope.PROJECT

    def is_present(self, repo_path: Path | None = None) -> bool:
        """``code`` on PATH, a user data directory, or a ``.vscode/`` in the repo.

        The repo-local check earns its place: a workspace with ``.vscode/`` in
        it is worth configuring even from a machine where the ``code`` shim was
        never installed, because the file is committed and read by whoever
        opens the repo next.
        """
        import shutil

        if repo_path is not None and (repo_path / ".vscode").is_dir():
            return True
        if shutil.which("code") is not None:
            return True
        home = Path.home()
        return any((home / candidate).is_dir() for candidate in (".vscode", ".vscode-server"))

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
        if scope is not Scope.PROJECT:
            return result
        if repo_path is None:
            raise ValueError("project-scope install needs a repo_path")

        # ``OSError`` alongside ``ValueError`` because the read and the parent
        # ``mkdir`` both live inside these writers, and neither failure is
        # exotic: ``.vscode`` can be a plain file, and a config in a shared
        # checkout can be unreadable. Nothing wraps ``install`` — ``agents
        # add``, ``agents refresh`` and ``doctor --repair`` all call it bare —
        # so an escape aborts the run after other agents' configs have already
        # been written, and prints a traceback instead of the summary naming
        # them.
        try:
            written = write_mcp_config(repo_path)
            result.record(written.path, written.action)
        except RemoteServerEntryError:
            # Before the broader handler below, which it would otherwise reach
            # as a ``ValueError`` and be described as an unreadable file. It is
            # a perfectly readable file holding a deliberate choice.
            result.record(mcp_config_path(repo_path), FileAction.KEPT)
            result.note(
                '.vscode/mcp.json left unchanged: its "repowise" entry names a remote '
                "server, and converting it in place would leave an entry that is "
                "neither. Run 'repowise agents remove --target=vscode' first if you "
                "want the local server instead."
            )
        except (ValueError, OSError):
            result.record(mcp_config_path(repo_path), FileAction.KEPT)
            result.note(
                ".vscode/mcp.json left unchanged (unreadable, or not valid JSON; it "
                'may contain comments). Add a "repowise" server under "servers" manually.'
            )
        try:
            written = write_extensions_config(repo_path)
            result.record(written.path, written.action)
        except (ValueError, OSError):
            result.record(extensions_config_path(repo_path), FileAction.KEPT)
            result.note(
                ".vscode/extensions.json left unchanged (unreadable, or not valid JSON; "
                f'it may contain comments). Add "{EXTENSION_ID}" to "recommendations" '
                "manually."
            )
        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Remove the server entry and the extension recommendation.

        Both files, because :meth:`install` writes both and
        :meth:`describe_paths` names both. Leaving the recommendation behind
        meant ``agents remove --target=vscode`` still had the editor prompting
        every contributor to install an extension for an integration that was
        just removed — and said nothing about the file it had skipped.
        """
        result = WriteResult()
        if scope is not Scope.PROJECT or repo_path is None:
            return result
        result.record(*_remove_server_entry(mcp_config_path(repo_path)))
        result.record(*_remove_extension_recommendation(extensions_config_path(repo_path)))
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        return json.dumps({"servers": {"repowise": server_entry(repo_path or Path.cwd())}}, indent=2)

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        if scope is not Scope.PROJECT:
            return []
        repo = repo_path or Path.cwd()
        return [str(mcp_config_path(repo)), str(extensions_config_path(repo))]

    def doctor(self) -> DoctorReport:
        """Health is repo-scoped, so a bare call can only report the honest answer.

        Without a repo path there is nothing user-level to inspect. Reporting
        ``OK`` would be a claim we have not checked, so this reports
        not-installed with the command that would wire it.
        """
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.NOT_INSTALLED,
            issues=("VS Code wiring is workspace-local; run this from a repo to check it.",),
            fix_command="repowise init",
        )


TARGET = VSCodeTarget()
