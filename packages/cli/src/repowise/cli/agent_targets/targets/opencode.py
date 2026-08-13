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

Both spellings are swept by detection and removal, and writes follow whichever
one already holds the entry (:func:`write_target_path`). Those three have to
agree. When they did not, an install that had landed in ``opencode.json``
stayed detected there once a ``.jsonc`` appeared beside it, and ``refresh``
wrote a second entry into the other file rather than skipping a scope it had
been told was already wired.
"""

from __future__ import annotations

import json
import os
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

ID = "opencode"
DISPLAY_NAME = "OpenCode"
DOCS_URL = "https://opencode.ai/docs/mcp-servers/"

#: Config key gating this agent's managed instruction file. Deliberately the
#: same id Codex uses, because it is the same file: one ``AGENTS.md`` per repo,
#: so one switch that turns it on and off rather than two that disagree.
PROJECT_FILE_ID = "agents_md"

#: Name of the server entry inside the ``mcp`` table.
SERVER_NAME = "repowise"

#: Both spellings OpenCode reads, in the order :func:`config_path` prefers them.
#: Uninstall and detection sweep the whole tuple rather than the one file
#: :func:`config_path` picks today, because which one that is depends on what
#: exists at the moment of the call. An entry written into ``opencode.json``
#: before an ``opencode.jsonc`` appeared beside it would otherwise be invisible
#: to every command that could remove it.
CONFIG_NAMES = ("opencode.jsonc", "opencode.json")

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

    The **stripped** value is what gets joined. Surrounding whitespace on this
    variable comes from the same sloppy profile line that produces the blank
    value above, and leaving it in is worse than blank rather than merely
    untidy: ``Path(" /home/u/.config")`` is a *relative* path, so the config
    would be written into a directory named ``" "`` inside whatever repo the
    user happened to be standing in, and read back from somewhere else on the
    next call from a different working directory.

    There is deliberately **no Windows branch**. The absence is the feature:
    ``%APPDATA%\\opencode`` is where this would land if it followed the platform
    convention, OpenCode has never read that location, and an entry written
    there is invisible rather than broken, which is the harder bug to see.
    """
    configured = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if configured:
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

    **This answers "where should a write go", and only that.** Removal and
    detection use :func:`config_paths` instead, because the answer here depends
    on what exists at the moment of the call and that can change between an
    install and the uninstall that undoes it.
    """
    directory = config_dir(scope, repo_path)
    for name in CONFIG_NAMES:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return directory / CONFIG_NAMES[0]


def config_paths(scope: Scope, repo_path: Path | None = None) -> list[Path]:
    """Every file this target may have written an entry into, preferred first.

    Removal and detection sweep all of them rather than trusting
    :func:`config_path`. The failure that forces this is ordinary: install into
    a repo that has only ``opencode.json`` writes there, OpenCode later creates
    an ``opencode.jsonc`` beside it, and from then on ``config_path`` answers
    ``.jsonc``. Uninstall would report success against the empty new file while
    the real registration sat in the old one, invisible to ``detect`` and so
    unreachable by every command that could have removed it.
    """
    directory = config_dir(scope, repo_path)
    return [directory / name for name in CONFIG_NAMES]


def write_target_path(scope: Scope, repo_path: Path | None = None) -> Path:
    """Where an install should write: the file already holding our entry, else the preferred one.

    Detection sweeps both spellings and writing did not, which is worse than
    either rule on its own. An install that had landed in ``opencode.json``
    stayed *detected* there after an ``opencode.jsonc`` appeared beside it, so
    ``agents refresh`` no longer skipped the scope as unwired -- and then wrote
    a **second** repowise entry into the other file. ``doctor --repair`` routes
    through the same refresh, so it did it too, and the result renders as
    "configured x2" under a warning that an agent wired twice loads repowise
    twice. Refresh is contracted to add nothing, so creating a registration is
    the one thing it must not do.

    Following the entry also makes re-running idempotent whichever file holds
    it, which is what an install should be.
    """
    for candidate in config_paths(scope, repo_path):
        if candidate.exists() and _reads_repowise(candidate):
            return candidate
    return config_path(scope, repo_path)


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


#: OpenCode's spelling of "a command we launch". Its remote servers are
#: ``type: "remote"`` and carry a ``url`` instead.
LOCAL_TYPE = "local"


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
    from ..formats.server_entry import is_remote_entry

    path = write_target_path(scope, repo_path)
    generated = server_entry(scope, repo_path)

    if path.exists():
        existing = load_json_object_or_value_error(path, path.name)
        servers = existing.get("mcp", {})
        if not isinstance(servers, dict):
            raise ValueError(f"{path.name} 'mcp' must be a JSON object")
        servers = dict(servers)
        stored = servers.get(SERVER_NAME)
        if isinstance(stored, dict) and is_remote_entry(stored, local_type=LOCAL_TYPE):
            # See ``formats.server_entry``. This config is ``$schema``-validated,
            # so the stray key a half-conversion leaves behind can cost the whole
            # file rather than the one entry.
            raise RemoteServerEntryError(f"{path.name} 'repowise' is wired to a remote server")
        servers[SERVER_NAME] = _merge_entry(stored, generated)
        existing["mcp"] = servers
        merged = existing
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        # No ``$schema`` seeded here, deliberately. It would be a nice courtesy
        # and it costs more than it gives: OpenCode's own first-run file is
        # exactly ``{"$schema": "..."}`` and nothing else, so a file holding
        # only that is far more likely to be the host's than ours. Writing one
        # leaves uninstall no way to tell "a stub we created" from "the config
        # the user already had", and the safe reading of that ambiguity is the
        # one that never deletes. Without it, a file repowise created holds only
        # ``mcp`` and empties to nothing, which is unambiguous.
        merged = {"mcp": {SERVER_NAME: {**generated, "enabled": True}}}

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


def _remove_server_entry(path: Path) -> tuple[Path, FileAction, str | None]:
    """Drop ``mcp.repowise``, preserving sibling servers.

    Rounds the install trip back to nothing: the ``mcp`` wrapper goes once it is
    empty, and the file goes once *it* is empty, so a file repowise created
    leaves no stub behind.

    "Empty" means empty, not "holds nothing we recognise". An earlier version
    also deleted a file holding just ``{"$schema": "..."}`` on the grounds that
    it was a stub of ours, which is false in the most ordinary case there is:
    that is exactly the file OpenCode itself writes on first run. Not seeding a
    ``$schema`` on create is what makes this test unambiguous.
    """
    from ..formats.json_merge import load_json_object_or_value_error, write_json_config

    if not path.exists():
        return path, FileAction.NOT_FOUND, None
    try:
        existing = load_json_object_or_value_error(path, path.name)
    except (OSError, ValueError):
        # Same reason install declines: rewriting a file we could not parse
        # destroys whatever we failed to read.
        return (
            path,
            FileAction.KEPT,
            "not strict JSON, so removing our entry would drop comments",
        )

    servers = existing.get("mcp")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return path, FileAction.NOT_FOUND, None
    servers.pop(SERVER_NAME)

    if not servers:
        existing.pop("mcp", None)
    if not existing:
        try:
            path.unlink()
        except OSError:
            # Never report REMOVED over a file that still holds our entry. A
            # read-only bit is enough to cause this, and swallowing it turns a
            # loud failure into a silent false success on the destructive verb.
            # Falling through to the rewrite keeps the failure loud, from the
            # same ``os.replace`` the write path uses.
            write_json_config(path, existing)
        return path, FileAction.REMOVED, None

    write_json_config(path, existing)
    return path, FileAction.REMOVED, None


def _remove_instructions(
    scope: Scope, repo_path: Path | None = None
) -> tuple[Path, FileAction, list[str]]:
    """Strip the managed block from ``AGENTS.md``, unless another agent needs it.

    **The shared-file case, and it is the whole reason this function is not two
    lines.** ``AGENTS.md`` is a host-neutral convention, not this target's
    private config: Codex has managed the same path in the same repo since long
    before OpenCode was wired, Hermes reads it too, and every one of those
    descriptors is right to claim it.

    Install is unaffected -- the block is marker-delimited and idempotent, so
    whichever agent writes last reports ``unchanged``. Uninstall is where the
    sharing bites. Removing OpenCode from a repo that still has another of them
    wired would strip the block out from under an agent that stays configured,
    stops getting its instructions, and says nothing about it. So the file is
    left alone while another wired agent is still reading it, and the note names
    which one, because a silent ``kept`` is the state that reads as a bug.

    The same guard is in ``codex.py`` and ``hermes.py``, and all three ask the
    registry rather than naming each other, so the next agent to adopt this file
    gets it for free. A fix that only runs on the agent added most recently
    leaves the identical bug sitting in its siblings, which is exactly how it
    went unnoticed for four phases.

    Returns the owners that caused a ``KEPT``, empty when something else did.
    The caller needs that distinction rather than re-deriving it: ``KEPT`` also
    means "the markers are malformed" and "the file could not be read", and a
    note blaming shared ownership for one of those sends the user to remove an
    agent that will not help.
    """
    from ..formats import marker_block
    from ..formats.marker_block import BlockState
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START
    from ..registry import other_managers_of

    path = instructions_path(scope, repo_path)

    state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
    if state is BlockState.PRESENT:
        owners = other_managers_of(path, exclude=ID, scope=scope, repo_path=repo_path)
        if owners:
            return path, FileAction.KEPT, owners

    if marker_block.remove(path, DISTILL_MARKER_START, DISTILL_MARKER_END):
        return path, FileAction.REMOVED, []

    # ``remove`` returns False for several distinct reasons and they do not mean
    # the same thing to a reader. "There was nothing of ours here" is not-found;
    # "there is a file we deliberately did not touch" is kept. Asking
    # ``exists()`` conflates them, and AGENTS.md is a file users write in, so
    # "left alone" is a common and honest answer.
    if state in (BlockState.ABSENT_FILE, BlockState.ABSENT):
        return path, FileAction.NOT_FOUND, []
    return path, FileAction.KEPT, []


def _instructions_outcome(owners: list[str], path: Path) -> tuple[FileAction, str]:
    """What a non-removal of ``AGENTS.md`` actually was, and how to say it.

    Three genuinely different outcomes reach one action, and they want opposite
    things from the user. Shared ownership is a deliberate refusal and stays
    ``KEPT``. A malformed marker pair is also a refusal. But the block still
    sitting there *with no other owner* means the write failed, which is a
    ``FAILED`` and a different exit code, not a decision we made.
    """
    from ..formats import marker_block
    from ..formats.marker_block import BlockState
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START

    if owners:
        return FileAction.KEPT, (
            f"{' and '.join(owners)} still reads the same managed block; "
            "remove that agent too if you want the block gone"
        )
    state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
    action = FileAction.FAILED if state is BlockState.PRESENT else FileAction.KEPT
    return action, marker_block.refusal_reason(state)


def _prune_user_dir() -> None:
    """Remove ``<config>/opencode`` when a user-scope uninstall emptied it.

    **Only when uninstall actually removed a file**, and only via ``rmdir``,
    which refuses a directory holding anything. So the host's own config
    directory survives: OpenCode keeps state in there, and a directory that
    still has any of it in it cannot be removed by this.

    An earlier round deleted this function outright, over-correcting past the
    defect it was reacting to. That defect was the ``$schema`` file deletion,
    and it is fixed where it lived. Removing the prune as well cost something
    real and non-obvious: :meth:`OpenCodeTarget.is_present` reads this directory
    as evidence the user has OpenCode, and ``install`` creates it. So an
    ``agents add`` followed by an ``agents remove`` left an empty directory that
    kept OpenCode pre-ticked in every later checklist and listing, on a machine
    that had never had it. Our own residue became the reason we kept offering.

    The symlink guard is the other half, and it is not theoretical. ``rmdir``
    does refuse a non-empty *directory*, and a Windows junction is not one: it
    is a reparse point, so ``rmdir`` unlinks it however full its target is. The
    target survives and the link does not.
    """
    import contextlib

    candidate = user_config_dir()
    if candidate.is_symlink():
        return
    with contextlib.suppress(OSError):
        candidate.rmdir()


def _parses_as_strict_json(path: Path) -> bool:
    """Whether *path* can be read as strict JSON at all.

    Separate from :func:`_reads_repowise` because "we could not read it" and
    "we read it and repowise is not in it" lead a health check to say different
    things, and both make :func:`_reads_repowise` answer False.

    Not :func:`json_merge.is_damaged`, for two reasons. It exists to answer "is
    this file damaged", which for a host that accepts JSONC is the wrong
    question. And it lets ``UnicodeDecodeError`` escape: that is a ``ValueError``
    rather than an ``OSError`` or a ``JSONDecodeError``, so neither of its
    handlers catches it.
    """
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return True


def _reads_repowise(path: Path) -> bool:
    """Whether *path* is a config naming the repowise MCP server."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # ``UnicodeDecodeError`` is a ``ValueError`` and not an ``OSError``, and
        # a file we cannot decode tells us nothing either way.
        return False

    try:
        data = json.loads(text)
    except ValueError:
        # The JSONC case, and answering "no" here was wrong in a way that was
        # not obvious. This probe is what ``detect`` is built on, and
        # ``registry.other_managers_of`` asks ``detect`` who is still using a
        # shared file. So a user whose ``opencode.jsonc`` carries a comment --
        # the exact user this module goes out of its way to support -- was
        # reported as not wired, and removing *Codex* then stripped the managed
        # AGENTS.md block out from under a fully working OpenCode, silently and
        # with no note. That is the failure the ownership guard exists to
        # prevent, reached through the front door.
        #
        # So a commented file gets a text probe rather than a verdict of "no".
        # It is the same shape ``codex.py`` uses on its TOML, and the error
        # asymmetry is what justifies it: a false positive costs an
        # ``agents add`` that declines and prints the entry to paste, while a
        # false negative deletes instructions from an agent that is working.
        return f'"{SERVER_NAME}"' in text and '"mcp"' in text

    servers = data.get("mcp") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Every place OpenCode is currently wired to repowise.

    Both scopes, matching what this target writes, and both config spellings
    within each scope: an entry written into ``opencode.json`` stays findable
    after an ``opencode.jsonc`` appears beside it, which is what makes it
    removable. Contracted never to raise.
    """
    found: list[Registration] = []

    scopes: list[tuple[Scope, Path | None]] = [(Scope.USER, None)]
    if repo_path is not None:
        scopes.append((Scope.PROJECT, repo_path))

    for scope, repo in scopes:
        for candidate in config_paths(scope, repo):
            if candidate.exists() and _reads_repowise(candidate):
                found.append(Registration(method="direct", scope=scope, config_path=candidate))

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
        except RemoteServerEntryError:
            path = write_target_path(scope, repo_path)
            result.record(
                path,
                FileAction.KEPT,
                'its "repowise" entry names a remote server',
            )
            result.note(
                f'{path.name} left unchanged: its "repowise" entry names a remote server, '
                "and converting it in place would leave an entry that is neither. Run "
                "'repowise agents remove --target=opencode' first if you want the local "
                "server instead."
            )
        except (ValueError, OSError) as exc:
            # ``write_target_path`` rather than ``config_path``: the note has to
            # name the file the write actually aimed at, which is the one
            # already holding our entry when there is one.
            path = write_target_path(scope, repo_path)
            result.record(path, FileAction.KEPT, f"could not be rewritten ({exc})")
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
            result.record(
                written.path,
                written.action,
                _instructions_outcome([], written.path)[1]
                if written.action is FileAction.KEPT
                else None,
            )
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
            result.record(
                instructions_path(scope, repo_path),
                FileAction.KEPT,
                f"could not be written ({exc})",
            )
            result.note(f"AGENTS.md could not be written ({exc}).")

        return result

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Remove the server entry and the managed block, because install writes both."""
        result = WriteResult()
        if scope is Scope.PROJECT and repo_path is None:
            raise ValueError("project-scope uninstall needs a repo_path")

        # Both spellings, not just the one ``config_path`` prefers right now.
        # A file that never held our entry costs a stat and reports nothing,
        # except for the preferred one, which reports not-found so that removing
        # from a repo that was never wired still names a file.
        #
        # ``preferred`` is resolved once, before the loop. Reading it inside
        # would consult the filesystem after a candidate had already been
        # deleted, so the answer could change halfway through and decide whether
        # the *next* iteration reports anything.
        preferred = config_path(scope, repo_path)
        for candidate in config_paths(scope, repo_path):
            path, action, reason = _remove_server_entry(candidate)
            if action is not FileAction.NOT_FOUND or path == preferred:
                result.record(path, action, reason)

        instructions, action, owners = _remove_instructions(scope, repo_path)
        reason = None
        if action is FileAction.KEPT:
            action, reason = _instructions_outcome(owners, instructions)
        result.record(instructions, action, reason)
        if owners:
            result.note(f"{instructions} kept: {reason}.")

        if scope is Scope.USER and any(
            written.action is FileAction.REMOVED for written in result.files
        ):
            _prune_user_dir()
        return result

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

        **An unparseable config is never reported as BROKEN here**, which is
        where this differs from every other target. ``BROKEN`` fails the whole
        ``doctor`` run, and for a host that accepts JSONC by design a file
        ``json.loads`` rejects is far more likely to be a legal config with a
        comment in it than a damaged one. Calling that broken would fail
        ``doctor`` -- and any CI running it -- for someone who has simply
        installed OpenCode and never touched repowise, and then hand them a fix
        command that declines for the same reason. It is reported as unknown
        rather than as damage, which is what it is.
        """
        # Both spellings, like detection and removal. Reading only the file
        # ``config_path`` prefers made this contradict ``repowise agents``: an
        # entry in ``opencode.json`` with a ``.jsonc`` beside it showed as wired
        # in the listing and not-installed here, and the fix command printed
        # here then wrote a second entry into the other file.
        candidates = config_paths(Scope.USER)
        if any(path.exists() and _reads_repowise(path) for path in candidates):
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)

        unreadable = [
            path for path in candidates if path.exists() and not _parses_as_strict_json(path)
        ]
        if unreadable:
            user_config = unreadable[0]
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.NOT_INSTALLED,
                issues=(
                    f"{user_config} could not be read as strict JSON, so whether repowise "
                    "is registered in it is unknown. Comments are legal in this file; if "
                    "yours has them, add the entry with "
                    "'repowise agents print-config opencode'.",
                ),
                # No ``repairable=False`` here, deliberately: ``repo_checks``
                # returns before reading it for a ``not-installed`` row, so
                # setting it would read as load-bearing while doing nothing.
                # The advice that matters is in the issue text above, which is
                # what that row actually prints.
                fix_command="repowise agents print-config opencode",
            )

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
