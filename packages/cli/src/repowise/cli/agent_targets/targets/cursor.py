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

import contextlib
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


class RemoteServerEntryError(ValueError):
    """The stored ``repowise`` entry names a transport repowise did not write.

    A ``ValueError`` so it lands in the same handler as an unparseable file, and
    a distinct type so the caller can say which of the two happened. Both mean
    "left alone"; only one of them is a broken file.
    """


def _is_remote_entry(entry: dict) -> bool:
    """Whether a stored server entry describes a transport repowise did not write.

    The entry's own ``type`` decides, and a bare ``url`` only counts when there
    is no ``command`` beside it. Reading ``"url" in entry`` on its own, ahead of
    the declared type, calls ``{"type": "stdio", "command": ..., "url": ...}``
    remote and contradicts what the entry says about itself. A leftover ``url``
    on a local entry is exactly the stale state ``agents add`` exists to
    repoint, and treating it as remote wedged it shut against every command
    that could have fixed it.
    """
    declared = entry.get("type")
    if declared is not None:
        return declared != "stdio"
    return "url" in entry and "command" not in entry


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
        stored = servers.get("repowise")
        if isinstance(stored, dict) and _is_remote_entry(stored):
            # ``merge_server_entries`` keeps every key the user added and lets
            # the generated ones win, which is right for ``command`` and
            # ``args`` and wrong for ``type``. Against a hand-wired remote
            # server it forces ``type`` back to ``stdio`` while faithfully
            # preserving the ``url`` beside it, producing an entry that is
            # neither a valid local server nor a valid remote one. The
            # preservation rule is what makes it broken rather than merely
            # overwritten.
            #
            # A remote entry is a deliberate choice repowise did not make, so
            # the honest answer is to leave it and say so.
            raise RemoteServerEntryError("mcp.json 'repowise' is wired to a remote server")
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

    # Drop the wrapper once it is empty, and the file once *it* is empty, so an
    # install followed by an uninstall leaves no trace. A `.cursor/mcp.json`
    # holding `{"mcpServers": {}}` is a file we created, that the user did not
    # ask for, and that still reads as repowise having been here.
    if not servers:
        existing.pop("mcpServers", None)
    if not existing:
        try:
            config_path.unlink()
        except OSError:
            # Suppressing this reported REMOVED for a file still holding our
            # entry, which is a false success on the destructive verb and one
            # a read-only bit is enough to cause. Falling through to the
            # rewrite keeps the loud failure the previous code had, from the
            # same `os.replace` it used.
            write_json_config(config_path, existing)
        return config_path, FileAction.REMOVED

    write_json_config(config_path, existing)
    return config_path, FileAction.REMOVED


def _remove_rules_file(repo_path: Path) -> tuple[Path, FileAction]:
    """Strip the managed block, deleting the file if it held nothing else."""
    from ..formats import marker_block
    from ..formats.marker_block import BlockState
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START

    config_path = rules_path(repo_path)
    if marker_block.remove(
        config_path,
        DISTILL_MARKER_START,
        DISTILL_MARKER_END,
        delete_if_only=RULES_PREFIX,
    ):
        return config_path, FileAction.REMOVED

    # ``remove`` returns False for four different reasons, and they do not mean
    # the same thing to a reader of the output. "There was nothing of ours here"
    # is not-found; "there is a file we deliberately did not touch" is kept.
    # Asking ``exists()`` conflates them, because a rules file with none of our
    # markers in it exists and is not ours.
    state = marker_block.inspect(config_path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
    if state in (BlockState.ABSENT_FILE, BlockState.ABSENT):
        return config_path, FileAction.NOT_FOUND
    return config_path, FileAction.KEPT


def _prune_empty_dirs(repo_path: Path) -> None:
    """Remove ``.cursor/rules`` and ``.cursor`` when uninstall emptied them.

    ``write_rules_file`` creates both, so leaving them is the directory-shaped
    version of the stub file ``delete_if_only`` exists to prevent. It also has a
    second-order effect worth naming: :meth:`CursorTarget.is_present` reads
    ``.cursor/`` as evidence the user has Cursor, so our own leftovers would
    keep the agent pre-ticked in every later listing and checklist, for a repo
    it had just been removed from.

    **Only called when uninstall actually removed a file**, which is the part
    that is not optional. "``rmdir`` refuses a non-empty directory, so an empty
    one must be ours" is the same shape of wrong assumption this module keeps
    meeting: a user who made ``.cursor/`` by hand and left it empty would have
    had it deleted by a remove that found nothing of ours to remove.

    The symlink guard is the second half. ``rmdir`` does refuse a non-empty
    *directory*, and a Windows directory junction is not one: it is a reparse
    point, so ``rmdir`` unlinks it happily however full the target is. The
    target survives and the link does not, which is a repo whose ``.cursor``
    stopped resolving.
    """
    for candidate in (rules_path(repo_path).parent, repo_path / ".cursor"):
        if candidate.is_symlink():
            continue
        with contextlib.suppress(OSError):
            candidate.rmdir()


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
    except (OSError, ValueError):
        # ``ValueError`` covers ``json.JSONDecodeError`` and, separately,
        # ``UnicodeDecodeError`` from a file that is not UTF-8. Naming
        # JSONDecodeError alone let the decode failure escape a probe whose
        # whole contract is that it never raises, and detection runs on paths
        # (``resolve_target_flag``) that do not catch for it.
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
        except RemoteServerEntryError:
            result.record(mcp_config_path(repo_path), FileAction.KEPT)
            result.note(
                '.cursor/mcp.json left unchanged: its "repowise" entry names a remote '
                "server, and converting it in place would leave an entry that is "
                "neither. Run 'repowise agents remove --target=cursor' first if you "
                "want the local server instead."
            )
        # ``OSError`` alongside ``ValueError`` because the read and the parent
        # ``mkdir`` both live in there, and neither is exotic: ``.cursor`` can be
        # a plain file, and a config in a shared checkout can be unreadable.
        # Nothing wraps ``install`` — ``agents add``, ``agents refresh`` and
        # ``doctor --repair`` all call it bare — so an escape here aborts the run
        # after other agents' configs have already been written, and prints a
        # traceback instead of the summary that would have said so.
        except (ValueError, OSError) as exc:
            result.record(mcp_config_path(repo_path), FileAction.KEPT)
            # The reason is carried rather than asserted. This guard is wide
            # enough to catch things that are not a bad config file at all: the
            # generated entry is built inside the guarded call, so an OSError
            # resolving the repo path lands here too, and a note that flatly
            # says "not valid JSON" would send the user to inspect a file that
            # is fine.
            result.note(
                f".cursor/mcp.json left unchanged ({exc}). Add a "
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
                    "unpaired or duplicated, or the file could not be read. Fix it by "
                    "hand and re-run."
                )
        except (OSError, ValueError):
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
        if any(written.action is FileAction.REMOVED for written in result.files):
            _prune_empty_dirs(repo_path)
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
