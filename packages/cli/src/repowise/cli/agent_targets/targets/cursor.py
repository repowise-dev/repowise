"""Cursor as an agent target.

Good tier, structurally: it names neither a hook adapter nor a transcript
adapter, so :func:`derive_tier` cannot place it at Full however many files it
writes. Cursor exposes no hook protocol repowise can intercept tool calls
through, and no transcript format to mine.

Cursor is **not** a flag on the VS Code target despite the fork lineage. It does
not read ``.vscode/mcp.json``; it reads ``.cursor/mcp.json``, and the two files
disagree about their top-level key (``mcpServers`` against ``servers``). There is
also no provider API for an extension to register a server through, which is what
the VS Code extension uses.

Two host facts this target is built on, both re-checked against Cursor's own
documentation on 2026-08-12 rather than inherited:

* ``.cursor/mcp.json`` keys servers under ``mcpServers`` and **requires** a
  ``type`` field for stdio servers. That last part is the one worth pinning: it
  is the single difference between this file and the repo-shared ``.mcp.json``,
  and the shape most likely to be "simplified" back out.
* ``.cursor/rules/*.mdc`` takes YAML frontmatter, and ``alwaysApply: true`` loads
  the rule in every conversation with ``description`` and ``globs`` ignored. So
  the frontmatter here is one key, matching the host's own minimal example,
  rather than three of which two do nothing.

**The working-directory quirk, and why it costs us nothing.** Cursor launches MCP
subprocesses with a working directory that is not the workspace root, and does
not pass ``rootUri`` in the MCP ``initialize`` call. A server that resolves its
repo from ``cwd`` therefore looks at the wrong tree and reports "not indexed" on
every call. repowise is unaffected only because its registration already carries
the absolute repo path positionally (``repowise mcp <abs-path>``), which the
shared generator has always emitted. That is a load-bearing accident, so
``test_agent_targets`` pins it: if the generator ever drops the positional path,
this target breaks in a way that looks like an indexing bug.

**Project scope only.** Cursor does read ``~/.cursor/mcp.json``, but one global
entry can only name one repo, and the workaround for that is a
``${workspaceFolder}`` token repowise cannot verify Cursor expands inside an
argument array. Writing a user-scope entry that silently points every workspace
at one repo is worse than not writing one, and ``print_config`` covers the person
who wants to paste it themselves.
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

ID = "cursor"
DISPLAY_NAME = "Cursor"
DOCS_URL = "https://cursor.com/docs/context/mcp"

#: Config key gating this agent's managed rules file.
PROJECT_FILE_ID = "cursor_rules"

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)

#: Frontmatter plus the note, written above the managed block when repowise
#: creates the file. One key, because ``alwaysApply: true`` makes the host ignore
#: ``description`` and ``globs``, and a key the host ignores is noise a reader has
#: to evaluate.
#:
#: Paired with ``marker_block.remove(delete_if_only=...)`` so install then
#: uninstall round-trips back to "no file" rather than leaving a stub carrying
#: nothing but our own frontmatter — which would still read as repowise-managed
#: to anyone who opened it.
RULES_PREFIX = (
    "---\n"
    "alwaysApply: true\n"
    "---\n"
    "\n"
    "<!-- Add your own Cursor rules above or below the Repowise section. "
    "Repowise only updates the managed section between markers. -->\n"
)


def mcp_config_path(repo_path: Path) -> Path:
    return repo_path / ".cursor" / "mcp.json"


def rules_path(repo_path: Path) -> Path:
    return repo_path / ".cursor" / "rules" / "repowise.mdc"


def server_entry(repo_path: Path) -> dict:
    """The ``.cursor/mcp.json`` server entry.

    Command and args come from the shared generator, so this resolves the same
    server as the committed ``.mcp.json`` on every contributor's checkout. The
    ``type`` field is added because Cursor documents it as required for stdio
    servers; the repo-shared file does not carry one and does not need one.
    """
    from repowise.cli.mcp_config import generate_mcp_config

    entry = generate_mcp_config(repo_path)["mcpServers"]["repowise"]
    return {"type": "stdio", **entry}


def write_mcp_config(repo_path: Path) -> FileWrite:
    """Merge the repowise server into ``.cursor/mcp.json``.

    Raises ``ValueError`` when the existing file is not strict JSON, so the
    caller can skip rather than destroy it. Cursor does not document comment
    support here, but the cost of being wrong is asymmetric: declining leaves a
    working config untouched and prints what to add, while rewriting a file we
    misparsed deletes whatever we could not read.
    """
    from ..formats.json_merge import (
        load_json_object_or_value_error,
        merge_server_entries,
        write_json_config,
    )

    config_path = mcp_config_path(repo_path)
    new_entry = {"repowise": server_entry(repo_path)}

    if config_path.exists():
        existing = load_json_object_or_value_error(config_path, "mcp.json")
        servers = existing.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcp.json 'mcpServers' must be a JSON object")
        servers = dict(servers)
        merge_server_entries(servers, new_entry)
        existing["mcpServers"] = servers
        merged = existing
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        merged = {"mcpServers": new_entry}

    return FileWrite(path=config_path, action=write_json_config(config_path, merged))


def write_rules_file(repo_path: Path) -> FileWrite:
    """Upsert the managed block into ``.cursor/rules/repowise.mdc``.

    The first caller of the marker-block helper against a file repowise creates
    *outright*, frontmatter and all, rather than one the user already owns. The
    helper covers that with ``new_file_prefix``, so the only thing this adds is
    the directory: ``atomic_write_text`` deliberately creates no parents, and
    ``.cursor/rules/`` will not exist on a machine that has never written a rule.
    """
    from ..formats import marker_block
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START, DISTILL_SECTION

    config_path = rules_path(repo_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    action = marker_block.upsert(
        config_path,
        f"\n{DISTILL_SECTION}\n",
        DISTILL_MARKER_START,
        DISTILL_MARKER_END,
        new_file_prefix=RULES_PREFIX,
    )
    return FileWrite(path=config_path, action=action)


def _remove_server_entry(config_path: Path) -> tuple[Path, FileAction]:
    """Drop ``mcpServers.repowise``, preserving sibling servers."""
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config

    if not config_path.exists():
        return config_path, FileAction.NOT_FOUND
    try:
        existing = load_json_object_or_value_error(config_path, "mcp.json")
    except ValueError:
        # Same reason install declines: rewriting a file we could not parse
        # destroys whatever we failed to read.
        return config_path, FileAction.KEPT

    servers = existing.get("mcpServers")
    if not isinstance(servers, dict) or "repowise" not in servers:
        return config_path, FileAction.NOT_FOUND
    servers.pop("repowise")
    write_json_config(config_path, existing)
    return config_path, FileAction.REMOVED


def _remove_rules_file(repo_path: Path) -> tuple[Path, FileAction]:
    """Strip the managed block, deleting the file if it held nothing else."""
    from ..formats import marker_block
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START

    config_path = rules_path(repo_path)
    removed = marker_block.remove(
        config_path,
        DISTILL_MARKER_START,
        DISTILL_MARKER_END,
        delete_if_only=RULES_PREFIX,
    )
    if removed:
        return config_path, FileAction.REMOVED
    # ``remove`` reports False for both "no block here" and "the markers are
    # malformed, so touching this would eat the user's text". Only the second is
    # a file left standing on purpose, and the two want different words.
    return config_path, FileAction.KEPT if config_path.exists() else FileAction.NOT_FOUND


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Whether the workspace MCP config names repowise.

    Project scope only, matching what this target writes. Returning nothing for
    an unparseable file is deliberate: it may well be wired up, and reporting
    "not configured" would be a guess.
    """
    if repo_path is None:
        return []
    config_path = mcp_config_path(repo_path)
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "repowise" not in servers:
        return []
    return [Registration(method="direct", scope=Scope.PROJECT, config_path=config_path)]


class CursorTarget:
    """Descriptor for Cursor. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = None
    session_adapter = None
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        """Project scope only. See the module docstring for why not user scope."""
        return scope is Scope.PROJECT

    def is_present(self, repo_path: Path | None = None) -> bool:
        """A ``.cursor/`` in the repo, the ``cursor`` shim on PATH, or ``~/.cursor``.

        The repo-local check earns its place for the same reason VS Code's does:
        ``.cursor/`` is committed and read by whoever opens the repo next, so a
        workspace carrying one is worth configuring even from a machine where
        the editor was never installed.
        """
        import shutil

        if repo_path is not None and (repo_path / ".cursor").is_dir():
            return True
        if shutil.which("cursor") is not None:
            return True
        return (Path.home() / ".cursor").is_dir()

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

        try:
            written = write_mcp_config(repo_path)
            result.record(written.path, written.action)
        except ValueError:
            result.record(mcp_config_path(repo_path), FileAction.KEPT)
            result.note(
                ".cursor/mcp.json left unchanged (not valid JSON). Add a "
                '"repowise" server under "mcpServers" manually.'
            )

        try:
            written = write_rules_file(repo_path)
            result.record(written.path, written.action)
            if written.action is FileAction.KEPT:
                # The helper refuses an orphaned or duplicated marker pair rather
                # than guessing at a repair that could swallow the user's text,
                # so say which file needs unpicking instead of reporting a write.
                result.note(
                    f"{rules_path(repo_path).name} left unchanged: its Repowise markers are "
                    "unpaired or duplicated. Fix them by hand and re-run."
                )
        except OSError:
            result.record(rules_path(repo_path), FileAction.KEPT)
            result.note(".cursor/rules/repowise.mdc could not be written.")

        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Remove the server entry and the rules file, because install writes both."""
        result = WriteResult()
        if scope is not Scope.PROJECT or repo_path is None:
            return result
        result.record(*_remove_server_entry(mcp_config_path(repo_path)))
        result.record(*_remove_rules_file(repo_path))
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        return json.dumps(
            {"mcpServers": {"repowise": server_entry(repo_path or Path.cwd())}}, indent=2
        )

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        if scope is not Scope.PROJECT:
            return []
        repo = repo_path or Path.cwd()
        return [str(mcp_config_path(repo)), str(rules_path(repo))]

    def doctor(self) -> DoctorReport:
        """Health is repo-scoped, so a bare call can only report the honest answer.

        Same shape as VS Code's, and for the same reason: ``doctor()`` takes no
        repo path, and there is nothing user-level this target writes. Reporting
        ``OK`` would be a claim nothing checked.
        """
        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.NOT_INSTALLED,
            issues=("Cursor wiring is workspace-local; run this from a repo to check it.",),
            fix_command="repowise agents add --target=cursor",
        )


TARGET = CursorTarget()
