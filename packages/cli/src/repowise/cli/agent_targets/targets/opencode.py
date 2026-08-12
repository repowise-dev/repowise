"""OpenCode as an agent target.

Good tier, structurally: it names neither a hook adapter nor a transcript
adapter, so :func:`derive_tier` cannot place it at Full however many files it
writes. OpenCode exposes no hook protocol repowise can intercept tool calls
through, and no transcript format to mine.

**This is not the ``opencode`` LLM provider.** repowise has shipped one of those
since well before this target: ``core.providers.llm.opencode`` shells out to the
same binary to *generate* wiki pages. That is repowise calling OpenCode. This
module is the opposite direction, OpenCode calling repowise, and the two share
nothing but a name. ``docs/agent/OPENCODE.md`` covers both and says which is
which.

Three host facts this target is built on, each checked against OpenCode's own
documentation on 2026-08-12 rather than inherited from any prior reading:

* **The config key is ``mcp``, not ``mcpServers``**, and the per-server value is
  ``{"type": "local", "command": [...]}`` where ``command`` is a *single array*
  combining the binary and its arguments. Every other JSON host repowise writes
  for splits those into ``command`` and ``args``, so the shape here is the one
  most likely to be "made consistent" with its neighbours and thereby broken.
* **The user config directory is XDG on every platform, Windows included**:
  ``XDG_CONFIG_HOME`` when it is set and not blank, otherwise ``~/.config``,
  then ``opencode``. Never ``%APPDATA%``. Confirmed on this machine, which is
  the strongest form of the check available: OpenCode 1.18.15 created
  ``C:\\Users\\<user>\\.config\\opencode\\opencode.jsonc`` on Windows with
  ``XDG_CONFIG_HOME`` unset.
* **``AGENTS.md`` is its instructions file**, at the repo root and at
  ``<config dir>/AGENTS.md``. That is the same file Codex reads, which is a
  design problem rather than a coincidence. See :func:`_remove_instructions`.

**The config file may legitimately contain comments and repowise declines to
rewrite one that does.** OpenCode accepts JSONC, and repowise ships no
comment-preserving JSON parser. Adding a dependency to this codebase to write
one host's config file is not a trade worth making, and the alternative that
needs no dependency is worse than declining: parsing a commented file with
comments stripped and re-serialising it deletes every comment the user wrote.
So an unparseable file is left exactly as it is, and the note names
``print-config`` so the user can paste the entry in themselves. This matches
what ``vscode.py`` and ``cursor.py`` already do for the same reason.

**New files are created as ``opencode.jsonc``**, matching what OpenCode itself
creates on first run, with an existing ``.jsonc`` preferred over an existing
``.json``. The extension is not what decides whether repowise can read a file --
a ``.jsonc`` holding plain JSON parses fine and is the common case -- so
choosing ``.json`` to advertise our own limitation would buy nothing and risks
the genuinely bad outcome, which is two config files in one directory where the
host reads whichever it prefers and repowise writes the other.
"""

from __future__ import annotations

import contextlib
import json
import os
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

ID = "opencode"
DISPLAY_NAME = "OpenCode"
DOCS_URL = "https://opencode.ai/docs/mcp-servers/"

#: Config key gating this agent's managed instruction file. Deliberately the
#: same id Codex uses, because it is the same file: one ``AGENTS.md`` per repo,
#: so one switch that turns it on and off rather than two that disagree.
PROJECT_FILE_ID = "agents_md"

#: Name of the server entry inside the ``mcp`` table.
SERVER_NAME = "repowise"

#: Seeded into a file repowise creates, and never onto one that already has a
#: ``$schema`` of its own. It is what gives the user completion and validation
#: in their editor, and OpenCode's own first-run file carries it.
SCHEMA_URL = "https://opencode.ai/config.json"

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)


def user_config_dir() -> Path:
    """OpenCode's user config directory, XDG on every platform.

    ``XDG_CONFIG_HOME`` wins when it is set to something that is not blank,
    otherwise ``~/.config``. The blank check is the part that matters and the
    part a plain truthiness test misses: an exported-but-empty
    ``XDG_CONFIG_HOME`` is common in trimmed CI images and in shell profiles
    that build the value conditionally, and treating ``""`` as a real prefix
    resolves the config directory to ``/opencode`` at the filesystem root.
    Whitespace-only gets the same treatment for the same reason.

    The untrimmed value is what gets joined once the check passes. Trimming a
    path is a different decision from rejecting a blank one, and a directory
    whose name legitimately ends in a space is the user's business.

    There is deliberately **no Windows branch**. The absence is the feature:
    ``%APPDATA%\\opencode`` is where this would land if it followed the platform
    convention, OpenCode has never read that location, and an entry written
    there is invisible rather than broken, which is the harder bug to see.
    """
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured and configured.strip():
        return Path(configured) / "opencode"
    return Path.home() / ".config" / "opencode"


def config_dir(scope: Scope, repo_path: Path | None = None) -> Path:
    """Where this scope's ``opencode.json{,c}`` and ``AGENTS.md`` live."""
    if scope is Scope.USER:
        return user_config_dir()
    if repo_path is None:
        raise ValueError("project scope needs a repo_path")
    return repo_path


def config_path(scope: Scope, repo_path: Path | None = None) -> Path:
    """The config file to read and write for *scope*.

    An existing ``.jsonc`` wins, then an existing ``.json``, then ``.jsonc`` for
    a file that does not exist yet. Pure existence, in that order: there is no
    merge across the two, because OpenCode's own precedence between them is not
    something repowise should be guessing at, and writing to the one the host
    does not read is the failure this ordering exists to avoid.
    """
    directory = config_dir(scope, repo_path)
    preferred = directory / "opencode.jsonc"
    if preferred.exists():
        return preferred
    fallback = directory / "opencode.json"
    if fallback.exists():
        return fallback
    return preferred


def instructions_path(scope: Scope, repo_path: Path | None = None) -> Path:
    """``AGENTS.md`` for *scope*. Shared with Codex at project scope."""
    return config_dir(scope, repo_path) / "AGENTS.md"


def server_entry(scope: Scope, repo_path: Path | None = None) -> dict:
    """The ``mcp.repowise`` entry, in OpenCode's shape.

    Two differences from every other JSON host, both load-bearing:

    * ``command`` is one array. The shared generator returns a binary and an
      argument list, so they are concatenated here rather than passed through.
    * The generator's ``description`` is dropped. OpenCode's config is
      ``$schema``-validated and its MCP entry documents ``type``, ``command``,
      ``enabled`` and ``environment``; a key outside that set buys nothing and
      is the sort of thing a strict validator rejects.

    Scope decides the invocation, following the rule the whole codebase uses. A
    project ``opencode.json`` is committed, so it carries the bare ``repowise``
    command and names its repo by absolute path. The user file is per machine
    and serves every workspace, so it pins the absolute binary of the install
    that wrote it -- a PATH shadow cannot hijack it -- and passes **no** repo
    path, letting the server resolve the repo it was launched in. That bare
    ``repowise mcp`` form is not novel: it is what ``.mcp.json``, the README and
    both plugins have always emitted.
    """
    from repowise.cli.mcp_config import generate_mcp_config, resolve_repowise_command

    if scope is Scope.USER:
        binary = resolve_repowise_command()
        return {"type": "local", "command": [binary, "mcp", "--transport", "stdio"]}

    if repo_path is None:
        raise ValueError("project scope needs a repo_path")
    generated = generate_mcp_config(repo_path)["mcpServers"]["repowise"]
    return {
        "type": "local",
        "command": [generated["command"], *generated["args"]],
    }


def _merge_entry(stored: object, generated: dict) -> dict:
    """Overlay *generated* onto whatever the user already had.

    Same rule as ``merge_server_entries``: generated keys win so a moved install
    or a renamed repo takes effect, and every other key the user added -- an
    ``environment`` block carrying provider keys, most importantly -- survives.

    ``enabled`` is the one exception and it runs the other way. It is not a
    field repowise computes, it is a switch the user flips, and ``false`` is the
    documented way to park a server without deleting it. Forcing it back to
    ``true`` would mean ``agents refresh`` and ``doctor --repair`` silently
    re-enable something that was turned off on purpose, which is the same
    class of bug as overwriting ``env``. So it is seeded as ``true`` on an entry
    repowise is creating and left strictly alone on one that already has it.
    """
    if not isinstance(stored, dict):
        return {**generated, "enabled": True}
    merged = dict(stored)
    merged.update(generated)
    if "enabled" not in merged:
        merged["enabled"] = True
    return merged


def write_mcp_config(scope: Scope, repo_path: Path | None = None) -> FileWrite:
    """Merge the repowise server into ``opencode.json{,c}``.

    Raises ``ValueError`` when the file is not strict JSON so the caller can
    skip it. See the module docstring for why declining beats every available
    alternative here.
    """
    from ..formats.json_merge import (
        load_json_object_or_value_error,
        write_json_config,
    )

    path = config_path(scope, repo_path)
    generated = server_entry(scope, repo_path)

    if path.exists():
        existing = load_json_object_or_value_error(path, path.name)
        servers = existing.get("mcp", {})
        if not isinstance(servers, dict):
            raise ValueError(f"{path.name} 'mcp' must be a JSON object")
        servers = dict(servers)
        servers[SERVER_NAME] = _merge_entry(servers.get(SERVER_NAME), generated)
        existing["mcp"] = servers
        merged = existing
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ``$schema`` first so it renders at the top of a file repowise created,
        # where a reader looks for it. Only ever on a new file: an existing one
        # without a ``$schema`` is a choice the user made.
        merged = {"$schema": SCHEMA_URL, "mcp": {SERVER_NAME: {**generated, "enabled": True}}}

    return FileWrite(path=path, action=write_json_config(path, merged))


def write_instructions(scope: Scope, repo_path: Path | None = None) -> FileWrite:
    """Upsert the managed block into ``AGENTS.md``.

    The body comes from :mod:`..instructions`, which is the one home for it.
    Unlike Cursor's rules file this is a document the user owns and may already
    have written in, so there is no ``new_file_prefix`` and no ``delete_if_only``
    -- an ``AGENTS.md`` repowise created still belongs to the repo afterwards,
    and Codex, which has managed this same file since long before this target,
    does not delete it either.
    """
    from ..formats import marker_block
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START, DISTILL_SECTION

    path = instructions_path(scope, repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    action = marker_block.upsert(
        path,
        f"\n{DISTILL_SECTION}\n",
        DISTILL_MARKER_START,
        DISTILL_MARKER_END,
    )
    return FileWrite(path=path, action=action)


def _remove_server_entry(path: Path) -> tuple[Path, FileAction]:
    """Drop ``mcp.repowise``, preserving sibling servers.

    Rounds the install trip back to nothing: the ``mcp`` wrapper goes once it is
    empty, and the file goes once it holds nothing but the ``$schema`` this
    module seeded into it. That last part is the difference between "uninstalled"
    and "left a file behind that still reads as repowise having been here", and
    a bare ``{"$schema": ...}`` is a file the user never asked for.

    A ``$schema`` the user set to something else, or any other key of theirs,
    keeps the file.
    """
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config

    if not path.exists():
        return path, FileAction.NOT_FOUND
    try:
        existing = load_json_object_or_value_error(path, path.name)
    except (OSError, ValueError):
        # Same reason install declines: rewriting a file we could not parse
        # destroys whatever we failed to read.
        return path, FileAction.KEPT

    servers = existing.get("mcp")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return path, FileAction.NOT_FOUND
    servers.pop(SERVER_NAME)

    if not servers:
        existing.pop("mcp", None)
    if not existing or existing == {"$schema": SCHEMA_URL}:
        try:
            path.unlink()
        except OSError:
            # Never report REMOVED over a file that still holds our entry. A
            # read-only bit is enough to cause this, and swallowing it turns a
            # loud failure into a silent false success on the destructive verb.
            # Falling through to the rewrite keeps the failure loud, from the
            # same ``os.replace`` the write path uses.
            write_json_config(path, existing)
        return path, FileAction.REMOVED

    write_json_config(path, existing)
    return path, FileAction.REMOVED


def _remove_instructions(scope: Scope, repo_path: Path | None = None) -> tuple[Path, FileAction]:
    """Strip the managed block from ``AGENTS.md``, unless another agent needs it.

    **The shared-file case, and it is the whole reason this function is not two
    lines.** ``AGENTS.md`` is a host-neutral convention, not this target's
    private config: Codex has managed the same path in the same repo since long
    before OpenCode was wired, and both descriptors are right to claim it.

    Install is unaffected -- the block is marker-delimited and idempotent, so
    whichever agent writes second reports ``unchanged``. Uninstall is where the
    sharing bites. Removing OpenCode from a repo that still has Codex wired
    would strip the block out from under Codex, which stays configured, stops
    getting its instructions, and says nothing about it. So the file is left
    alone while another wired agent is still reading it, and the note names
    which one, because a silent ``kept`` is the state that reads as a bug.

    The same guard is in ``codex.py``. It has to be: a fix that only runs on the
    agent added most recently leaves the identical bug sitting in its sibling,
    which is exactly how it went unnoticed for four phases.
    """
    from ..formats import marker_block
    from ..formats.marker_block import BlockState
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START
    from ..registry import other_managers_of

    path = instructions_path(scope, repo_path)

    owners = other_managers_of(path, exclude=ID, scope=scope, repo_path=repo_path)
    if owners:
        state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
        if state is BlockState.PRESENT:
            return path, FileAction.KEPT

    if marker_block.remove(path, DISTILL_MARKER_START, DISTILL_MARKER_END):
        return path, FileAction.REMOVED

    # ``remove`` returns False for several distinct reasons and they do not mean
    # the same thing to a reader. "There was nothing of ours here" is not-found;
    # "there is a file we deliberately did not touch" is kept. Asking
    # ``exists()`` conflates them, and AGENTS.md is a file users write in, so
    # "left alone" is a common and honest answer.
    state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
    if state in (BlockState.ABSENT_FILE, BlockState.ABSENT):
        return path, FileAction.NOT_FOUND
    return path, FileAction.KEPT


def _prune_user_dir() -> None:
    """Remove ``<config>/opencode`` when a user-scope uninstall emptied it.

    Only ever the directory this module created, only when it is empty, and
    never through a symlink. ``rmdir`` refuses a non-empty *directory*, but a
    Windows junction is a reparse point rather than a directory, so ``rmdir``
    unlinks it however full its target is -- the target survives and the link
    does not, which is a machine whose OpenCode config stopped resolving.

    Not applied to project scope. There is no directory to prune there: both
    files sit at the repo root.
    """
    candidate = user_config_dir()
    if candidate.is_symlink():
        return
    with contextlib.suppress(OSError):
        candidate.rmdir()


def _reads_repowise(path: Path) -> bool:
    """Whether *path* is a config naming the repowise MCP server."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ``ValueError`` covers both ``json.JSONDecodeError`` and
        # ``UnicodeDecodeError``, which is a ``ValueError`` and not an
        # ``OSError``. It also covers the JSONC case: a commented config that
        # genuinely does register repowise reads here as "not detected". That
        # is the honest answer for a probe contracted never to raise -- it
        # cannot see into the file -- and it costs an ``agents add`` that
        # declines and prints the entry rather than a wrong claim.
        return False
    servers = data.get("mcp") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Every place OpenCode is currently wired to repowise.

    Both scopes, matching what this target writes. Contracted never to raise.
    """
    found: list[Registration] = []

    user_config = config_path(Scope.USER)
    if user_config.exists() and _reads_repowise(user_config):
        found.append(Registration(method="direct", scope=Scope.USER, config_path=user_config))

    if repo_path is not None:
        project_config = config_path(Scope.PROJECT, repo_path)
        if project_config.exists() and _reads_repowise(project_config):
            found.append(
                Registration(method="direct", scope=Scope.PROJECT, config_path=project_config)
            )

    return found


class OpenCodeTarget:
    """Descriptor for OpenCode. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = None
    session_adapter = None
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        """Both. The user config serves every workspace, the project one is committed."""
        return True

    def is_present(self, repo_path: Path | None = None) -> bool:
        """The ``opencode`` binary on PATH, or a user config directory.

        No repo-local limb, unlike Cursor and VS Code. OpenCode keeps nothing
        repo-local of its own: its project config is a bare ``opencode.json`` at
        the root, which this target may well have written itself, so reading it
        as evidence the user has OpenCode would make our own output the reason
        the agent keeps being offered.
        """
        import shutil

        if shutil.which("opencode") is not None:
            return True
        return user_config_dir().is_dir()

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

        # Both writes are guarded, and neither guard is theoretical. Nothing
        # wraps ``install`` -- ``agents add``, ``agents refresh`` and
        # ``doctor --repair`` all call it bare -- so anything escaping here
        # aborts the run after other agents' configs have already been written
        # and prints a traceback in place of the summary naming them.
        #
        # ``OSError`` sits alongside ``ValueError`` because the read, the parent
        # ``mkdir`` and the command resolution all live inside the guarded call.
        # ``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``, and is
        # the ordinary way a cp1252 config on Windows reaches this.
        try:
            written = write_mcp_config(scope, repo_path)
            result.record(written.path, written.action)
        except (ValueError, OSError) as exc:
            path = config_path(scope, repo_path)
            result.record(path, FileAction.KEPT)
            # The reason is carried rather than asserted: this guard is wide
            # enough to catch things that are not a bad config file at all, and
            # a note that flatly said "not valid JSON" would send the user to
            # inspect a file that is fine. The JSONC case is the common one, so
            # it is named alongside the command that unblocks it.
            result.note(
                f"{path.name} left unchanged ({exc}). OpenCode accepts comments in this "
                "file and repowise does not rewrite one it cannot parse as strict JSON. "
                "Run 'repowise agents print-config opencode' and paste the entry under "
                '"mcp".'
            )

        try:
            written = write_instructions(scope, repo_path)
            result.record(written.path, written.action)
            if written.action is FileAction.KEPT:
                # The helper refuses an orphaned or duplicated marker pair
                # rather than guessing at a repair that could swallow the
                # user's own text, so name the file to unpick instead of
                # reporting a write that did not happen.
                result.note(
                    f"{instructions_path(scope, repo_path)} left unchanged: its Repowise "
                    "markers are unpaired or duplicated, or the file could not be read. "
                    "Fix it by hand and re-run."
                )
        except (OSError, ValueError) as exc:
            result.record(instructions_path(scope, repo_path), FileAction.KEPT)
            result.note(f"AGENTS.md could not be written ({exc}).")

        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Remove the server entry and the managed block, because install writes both."""
        result = WriteResult()
        if scope is Scope.PROJECT and repo_path is None:
            raise ValueError("project-scope uninstall needs a repo_path")

        result.record(*_remove_server_entry(config_path(scope, repo_path)))

        instructions, action = _remove_instructions(scope, repo_path)
        result.record(instructions, action)
        if action is FileAction.KEPT:
            owners = self._other_instruction_owners(scope, repo_path)
            if owners:
                result.note(
                    f"{instructions} kept: {' and '.join(owners)} still reads the same "
                    "managed block. Remove that agent too if you want the block gone."
                )

        if scope is Scope.USER and any(
            written.action is FileAction.REMOVED for written in result.files
        ):
            _prune_user_dir()
        return result

    def _other_instruction_owners(
        self, scope: Scope, repo_path: Path | None = None
    ) -> list[str]:
        from ..registry import other_managers_of

        return other_managers_of(
            instructions_path(scope, repo_path), exclude=ID, scope=scope, repo_path=repo_path
        )

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        """The entry to paste, in the shape OpenCode reads.

        Project scope resolves ``repo_path`` to the cwd the same way its
        neighbours do, so the printed absolute path is the repo the user is
        standing in rather than a placeholder they have to notice and edit.
        """
        if scope is Scope.USER:
            entry = server_entry(Scope.USER)
        else:
            entry = server_entry(Scope.PROJECT, repo_path or Path.cwd())
        return json.dumps({"mcp": {SERVER_NAME: {**entry, "enabled": True}}}, indent=2)

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        # ``repo_path`` is resolved for both scopes and ignored by the user one,
        # so the fallback is unconditional rather than a branch that has to stay
        # in step with ``config_dir``.
        repo = repo_path or Path.cwd()
        return [
            str(config_path(scope, repo)),
            str(instructions_path(scope, repo)),
        ]

    def doctor(self) -> DoctorReport:
        """User-scope health, which is the only half a bare call can see.

        ``doctor()`` takes no repo path, so the project config is out of reach
        and reporting ``OK`` on its behalf would be a claim nothing checked.
        Unlike Cursor and VS Code there *is* something user-level to look at, so
        a wired user config is reported as wired and the repo-scoped half is
        named as the part still unchecked.
        """
        user_config = config_path(Scope.USER)
        from ..formats.json_merge import is_damaged

        if is_damaged(user_config):
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.BROKEN,
                issues=(f"{user_config} is present but is not valid JSON.",),
                # ``add`` rather than ``refresh``: refresh skips what it cannot
                # detect, and an unparseable config detects as nothing, so it
                # would report success having done nothing at all.
                fix_command="repowise agents add --target=opencode",
                # Neither command repairs this. The file may be JSONC, which is
                # legal here, and repowise declines to rewrite it either way.
                repairable=False,
            )

        if user_config.exists() and _reads_repowise(user_config):
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)

        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.NOT_INSTALLED,
            issues=(
                "OpenCode is not wired at user scope; workspace wiring is repo-local, "
                "so run this from a repo to check that half.",
            ),
            fix_command="repowise agents add --target=opencode",
        )


TARGET = OpenCodeTarget()
