"""Hermes as an agent target.

Good tier, structurally: it names neither a hook adapter nor a transcript
adapter, so :func:`derive_tier` cannot place it at Full however many files it
writes. Hermes exposes no hook protocol repowise can intercept tool calls
through, and no transcript format to mine.

Every host fact below was read out of Hermes 0.20.0's own source and then
checked against its shipped documentation, rather than inherited. Where the
two disagreed the source won, and the disagreements are called out.

**The config file is YAML, one per machine, and the path is platform
dependent.** ``$HERMES_HOME/config.yaml`` when that variable is set, otherwise
``%LOCALAPPDATA%\\hermes\\config.yaml`` on Windows and ``~/.hermes/config.yaml``
everywhere else. The Windows branch is the fact most likely to be dropped for
looking like an accident: an entry written to ``~/.hermes`` on Windows is not
broken, it is *invisible*, which is the harder failure to see. Confirmed on
this machine, which is the strongest form of the check available -- Hermes had
created ``%LOCALAPPDATA%\\hermes`` with a real config in it, and ``~/.hermes``
did not exist.

**There is no project-scope config file.** Hermes reads exactly one
``config.yaml``. So the MCP registration is user scope and nothing else, and
one registration serves every repo, which is why the entry names no repo path
and lets the server resolve the repo it was launched in.

**Project scope writes the instructions file, and which file that is matters.**
Hermes loads exactly one project context file, first match wins, in the order
``.hermes.md`` / ``HERMES.md``, then ``AGENTS.md``, then ``CLAUDE.md``, then
``.cursorrules``. The tempting choice is the one named after the host, and it
is the wrong one: writing ``HERMES.md`` into a repo that already has an
``AGENTS.md`` would take precedence over it and **suppress the user's existing
instructions entirely**. So repowise writes the managed block into
``AGENTS.md``, the file it already manages for two other agents, and a repo
with no context file at all still gets one.

**The MCP entry carries no ``type`` field.** Hermes distinguishes a local
subprocess from a remote endpoint by which keys are present: ``command`` and
``args`` for stdio, ``url`` and ``headers`` for HTTP. See
:func:`_is_remote_entry` for why the shared ``server_entry`` predicate is not
used here.

**``platform_toolsets.cli`` is deliberately left alone in the ordinary case,
and that is the most load-bearing decision in this module.** See
:func:`_mcp_allowlist`. The short version: Hermes exposes every enabled MCP
server on every platform by default, and ``platform_toolsets.cli`` only turns
into an allowlist once it already names an MCP server. Adding ``repowise`` to
a list that names none would convert a permissive config into an allowlist
containing only us, silently disabling every other MCP server the user had.
"""

from __future__ import annotations

import os
import sys
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

ID = "hermes"
DISPLAY_NAME = "Hermes"
DOCS_URL = "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp"

#: Config key gating this agent's managed instruction file. Deliberately the
#: same id Codex and OpenCode use, because it is the same file: one
#: ``AGENTS.md`` per repo, so one switch that turns it on and off rather than
#: three that can disagree.
PROJECT_FILE_ID = "agents_md"

#: Name of the server entry, and the name Hermes matches in a toolset list.
SERVER_NAME = "repowise"

#: Top-level key holding MCP servers.
MCP_KEY = "mcp_servers"

#: Top-level key holding per-platform toolset lists.
TOOLSETS_KEY = "platform_toolsets"

#: Byte-order mark, spelled as an escape so it cannot be mistaken for stray
#: whitespace or lost by an editor that strips invisible characters.
BOM = "\ufeff"

#: The platform key for interactive ``hermes`` sessions.
CLI_PLATFORM = "cli"

#: Sentinel a user puts in a platform's toolset list to turn every MCP server
#: off for that platform. Hermes honours it ahead of any allowlist, so a config
#: carrying it is one repowise must not try to add itself to.
NO_MCP = "no_mcp"

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)


def hermes_home() -> Path:
    """Hermes's data directory, matching the host's own resolution order.

    ``HERMES_HOME`` first, then the platform-native default. The blank check is
    the part a plain truthiness test misses: an exported-but-empty
    ``HERMES_HOME`` is ordinary in trimmed CI images and in shell profiles that
    build the value conditionally, and treating ``""`` as a real prefix would
    resolve the config to the filesystem root. The value is **stripped** before
    use for the same reason, and because ``Path(" /home/u/.hermes")`` is a
    *relative* path -- the config would be written into a directory named
    ``" "`` inside whatever repo the user happened to be standing in.

    The Windows branch is not a portability nicety, it is where the file
    actually is. Hermes uses ``%LOCALAPPDATA%\\hermes`` there and has never read
    ``~/.hermes`` on that platform.
    """
    configured = (os.environ.get("HERMES_HOME") or "").strip()
    if configured:
        return Path(configured)
    if sys.platform == "win32":
        local_appdata = (os.environ.get("LOCALAPPDATA") or "").strip()
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"


def config_path() -> Path:
    """The one ``config.yaml`` Hermes reads. User scope; there is no other."""
    return hermes_home() / "config.yaml"


def instructions_path(repo_path: Path) -> Path:
    """``AGENTS.md`` at the repo root. Shared with Codex and OpenCode."""
    return repo_path / "AGENTS.md"


def server_entry() -> dict:
    """The ``mcp_servers.repowise`` entry, in Hermes's shape.

    Three differences from the JSON hosts, all read off Hermes's schema:

    * No ``type`` key. Hermes has none; ``command`` versus ``url`` is the
      discriminator.
    * No ``description``. Hermes's MCP entry documents ``command``, ``args``,
      ``env``, ``url``, ``headers`` and a fixed set of timeouts and filters; a
      key outside that set buys nothing.
    * No ``enabled: true``. It defaults to true when absent, so writing it adds
      a line that says what the file already means -- and, more usefully, never
      writing it means ``agents refresh`` can never flip an ``enabled: false``
      the user set deliberately back on. Parking a server without deleting it
      is the documented use of that flag.

    This file is per machine and serves every workspace, so the absolute binary
    of the install that wrote it is pinned -- a PATH shadow cannot hijack it --
    and **no** repo path is passed, letting the server resolve the repo it was
    launched in. That bare ``repowise mcp`` form is not novel: it is what
    ``.mcp.json``, the README and both plugins have always emitted.
    """
    from repowise.cli.mcp_config import resolve_repowise_command

    return {
        "command": resolve_repowise_command(),
        "args": ["mcp", "--transport", "stdio"],
    }


def _is_remote_entry(entry: dict) -> bool:
    """Whether a stored entry names a transport repowise did not write.

    Not :func:`..formats.server_entry.is_remote_entry`, and the difference is
    not cosmetic. That helper is built around a declared ``type`` field, which
    every JSON host has and Hermes does not, and its fallback limb reads
    ``"url" in entry and "command" not in entry``. Hermes's own loader is
    stricter than that fallback: given an entry carrying **both**, it logs a
    warning and uses the HTTP transport. So an entry with a ``url`` beside a
    ``command`` is remote as far as this host is concerned, and merging our
    command into it would leave a registration that looks repointed and still
    never launches us.

    Reusing a helper that answers a nearby but different question is how this
    codebase has produced its most expensive bugs, so the rule is spelled out
    here where the host's behaviour can be checked against it. The shared
    *exception* is still the right one to raise: the caller's handling is
    identical.
    """
    return "url" in entry


def _parse_enabled_flag(value: object) -> bool:
    """Whether a server's ``enabled`` value reads as on, mirroring the host.

    Hermes treats a missing flag, and any value it does not recognise, as
    enabled; only ``false`` / ``0`` / ``no`` / ``off`` and a falsey number turn
    a server off. This is mirrored rather than approximated because it decides
    whether a toolset list counts as an MCP allowlist, and guessing it wrong in
    the permissive direction is what would make repowise write the key it must
    not write.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return True


def _enabled_server_names(doc: dict) -> set[str]:
    """Names of MCP servers this config has switched on."""
    servers = doc.get(MCP_KEY)
    if not isinstance(servers, dict):
        return set()
    return {
        str(name)
        for name, entry in servers.items()
        if isinstance(entry, dict) and _parse_enabled_flag(entry.get("enabled"))
    }


def _mcp_allowlist(doc: dict) -> list | None:
    """``platform_toolsets.cli`` when it is already acting as an MCP allowlist.

    **Why this is a condition rather than an unconditional second write.**
    Hermes resolves a platform's toolsets and then, separately, decides which
    MCP servers that platform sees. The rule it applies is: if the platform's
    toolset list names one or more MCP servers, that list is an allowlist and
    only the named servers are exposed; otherwise **every** enabled MCP server
    is exposed. A missing ``platform_toolsets`` key means the platform's
    built-in default applies, which names no MCP server, so it takes the
    permissive branch too.

    Two consequences, and they point in opposite directions:

    * On an ordinary config -- no ``platform_toolsets`` at all, or a plain
      ``cli: [hermes-cli]`` -- repowise is already exposed the moment it is in
      ``mcp_servers``. Adding ``repowise`` to that list would flip the config
      onto the allowlist branch with exactly one entry, and **every other MCP
      server the user had would stop being exposed to the CLI**. Writing the
      key would not merely be redundant, it would be a regression we caused.
    * On a config whose list already names an MCP server, the allowlist branch
      is already active, and a repowise absent from that list is filtered out.
      There the entry is required, and omitting it is the silent failure.

    So the list is touched only when it is already an allowlist, which is what
    this function returns. ``no_mcp`` is honoured ahead of everything: it turns
    MCP off for the platform outright, and a user who wrote it does not want us
    adding ourselves back.

    Membership is tested against the servers this document actually enables, so
    a list naming only a server the user has since disabled does not read as an
    allowlist.

    **That test is close to the host's and not identical to it, deliberately.**
    Hermes also counts MCP servers contributed in memory by an enabled plugin,
    which are not in ``config.yaml`` at all and cannot be known without loading
    Hermes. So a toolset list whose only MCP name comes from a plugin reads as
    an allowlist to the host and as an ordinary list here, and repowise leaves
    itself out of a list that is filtering on it. The cost of being wrong that
    way is that repowise does not appear and the user adds one line; the cost of
    guessing the other way is silently disabling servers they do have. Between a
    miss the user can see and a regression they cannot, this takes the miss.
    """
    toolsets = doc.get(TOOLSETS_KEY)
    if not isinstance(toolsets, dict):
        return None
    listed = toolsets.get(CLI_PLATFORM)
    # A non-list value is not a configuration Hermes reads: it falls back to
    # the platform default, which is the permissive branch.
    if not isinstance(listed, list):
        return None
    names = {str(item) for item in listed}
    if NO_MCP in names:
        return None
    if not (names & _enabled_server_names(doc)):
        return None
    return listed


def _merge_entry(stored: object, generated: dict) -> dict:
    """Overlay *generated* onto whatever the user already had.

    Generated keys win so a moved install takes effect, and every other key the
    user added survives -- an ``env`` block carrying provider keys, a ``tools``
    include/exclude filter, a raised ``timeout``, and ``enabled: false`` on a
    server they parked on purpose.
    """
    if not isinstance(stored, dict):
        return dict(generated)
    merged = dict(stored)
    merged.update(generated)
    return merged


def _plan_write(existing_text: str | None) -> tuple[str, dict, dict | None]:
    """Compute the whole file this install would write, in one pass.

    Returns ``(merged_text, merged_doc, existing_doc)``. Both edits this target
    makes land in the same file, so they are two transforms of one string and
    then one atomic write -- there is no ordering to get wrong and no state in
    which one landed and the other did not. That is worth stating because the
    two-key shape invites a two-write implementation, and a two-write
    implementation would have to answer what a half-installed Hermes means.
    This one does not have to, because the state cannot occur.

    Raises ``ValueError`` when the file cannot be parsed, when the stored entry
    is remote, or when the splice did not produce the intended document.
    """
    from ..formats import yaml_merge

    generated = server_entry()

    if existing_text is None:
        existing_doc = None
        base_doc: dict = {}
        text = ""
    else:
        existing_doc = yaml_merge.load_mapping(existing_text)
        base_doc = existing_doc
        text = existing_text

    servers = base_doc.get(MCP_KEY)
    stored = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    if isinstance(stored, dict) and _is_remote_entry(stored):
        raise RemoteServerEntryError(
            f"{MCP_KEY}.{SERVER_NAME} names a remote server"
        )

    entry = _merge_entry(stored, generated)

    # Build the document we intend, from the parsed original, and hand it to
    # ``verify`` after the splice. Anything the line matching misreads shows up
    # as a mismatch there rather than as a damaged config.
    intended = _deep_copy(base_doc)
    # ``setdefault`` is the obvious call here and it is wrong: it does not
    # replace a key that is present and null. A section header with nothing
    # under it yet is ordinary YAML and an ordinary thing to find in a config
    #
    #     mcp_servers:
    #       # servers go here
    #
    # and it parses to ``None``, not to ``{}``. Treating that as a bad shape
    # made install decline permanently, with a note calling the user's valid
    # file invalid, and doctor then handed back the command that had just
    # refused. Hermes itself coerces the same key to an empty mapping before
    # reading it.
    section = intended.get(MCP_KEY)
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError(f"'{MCP_KEY}' must be a YAML mapping")
    intended[MCP_KEY] = section
    intended[MCP_KEY][SERVER_NAME] = entry

    merged_text = yaml_merge.set_child(text, MCP_KEY, SERVER_NAME, entry)

    allowlist = _mcp_allowlist(intended)
    if allowlist is not None and SERVER_NAME not in {str(item) for item in allowlist}:
        updated = [*allowlist, SERVER_NAME]
        intended[TOOLSETS_KEY][CLI_PLATFORM] = updated
        merged_text = yaml_merge.set_child(merged_text, TOOLSETS_KEY, CLI_PLATFORM, updated)

    yaml_merge.verify(merged_text, intended)
    return merged_text, intended, existing_doc


def _deep_copy(doc: dict) -> dict:
    """A copy deep enough that editing the intent cannot touch the original.

    ``copy.deepcopy`` would do, and this exists to keep the comparison in
    ``write_if_changed`` honest without importing it: the existing document is
    compared against the intended one, so the two must not share the nested
    containers being edited.
    """
    import copy

    return copy.deepcopy(doc)


def write_mcp_config() -> FileWrite:
    """Merge the repowise server into ``config.yaml``.

    Raises ``ValueError`` when the file cannot be read or the merge cannot be
    made safely, so the caller can leave it alone and say so.
    """
    from ..formats import yaml_merge

    path = config_path()
    raw = _read_text(path)
    bom, newline, existing_text = _prepare(raw)
    merged_text, merged_doc, existing_doc = _plan_write(existing_text)

    if raw is None:
        path.parent.mkdir(parents=True, exist_ok=True)
    return FileWrite(
        path=path,
        action=yaml_merge.write_if_changed(
            path, bom + merged_text, merged_doc, existing_doc, newline=newline
        ),
    )


def _prepare(raw: str | None) -> tuple[str, str | None, str | None]:
    """Split a file's text into what to put back and what to work on.

    Returns ``(bom, newline, text)``. The text is stripped of a byte-order mark
    and normalised to ``\\n``, which is the only shape the splice understands;
    the other two are what the write needs to put the file back the way it was.

    **The byte-order mark is the reason this is a function.** Every line match
    in the splice is anchored at column zero, so a mark sitting in front of the
    first key hides that key from the search: the edit appends a second copy of
    a section that was already there, the duplicate makes the merged text parse
    to something else, and install declines forever on a file both PyYAML and
    Hermes read without complaint. Notepad on Windows is the ordinary way to get
    one, and this target is Windows-facing. It is carried across rather than
    dropped: reading with ``utf-8-sig`` would be shorter and would silently
    rewrite the file without its mark, which is a change to someone else's file
    that nothing asked for.

    One helper rather than the same three lines twice, because the install and
    the removal both do this and the failure when they drift is invisible. The
    write path keeping the mark while the removal dropped it would break the
    round trip on exactly the files this exists for.
    """
    from ..formats import yaml_merge

    if raw is None:
        return "", None, None
    bom = BOM if raw.startswith(BOM) else ""
    body = raw[len(bom) :]
    return bom, yaml_merge.detect_newline(body), body.replace("\r\n", "\n")


def _read_text(path: Path) -> str | None:
    """The file's text, or ``None`` when it is not there.

    Read with ``newline=""`` so the line endings survive into
    :func:`..formats.yaml_merge.detect_newline`. ``read_text`` translates them
    away in memory, which would make every file look like LF and leave the
    write to guess -- and the guess is the platform's, which rewrites a user's
    whole config on Windows for a three-line edit.
    """
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def write_instructions(repo_path: Path) -> FileWrite:
    """Upsert the managed block into ``AGENTS.md``.

    The body comes from :mod:`..instructions`, which is the one home for it.
    This is a document the user owns and may already have written in, so there
    is no ``new_file_prefix`` and no ``delete_if_only``: an ``AGENTS.md``
    repowise created still belongs to the repo afterwards, and the two other
    agents managing this same file do not delete it either.
    """
    from ..formats import marker_block
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START, DISTILL_SECTION

    path = instructions_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    action = marker_block.upsert(
        path,
        f"\n{DISTILL_SECTION}\n",
        DISTILL_MARKER_START,
        DISTILL_MARKER_END,
    )
    return FileWrite(path=path, action=action)


def _remove_server_entry() -> tuple[Path, FileAction]:
    """Drop ``mcp_servers.repowise`` and any allowlist entry naming it.

    Both edits again land in one file and one write.

    **The file itself is only deleted when it is unambiguously ours**, which
    takes two tests, and the first one is the one that is easy to get wrong.

    The **text** left after the removal has to be blank, not the document. An
    empty document is not an empty file: a ``config.yaml`` holding nothing but
    the user's comments parses to ``{}``, and so does one holding a commented
    out server with a note about why it is parked. Deleting either of those on
    the strength of the parse destroys a file the user wrote, reports it as
    ``removed``, and says nothing. Comments do not survive into the parse, so no
    amount of comparing documents can see the difference.

    The directory has to hold nothing else, as a second test rather than the
    only one. ``config.yaml`` is the host's own file: Hermes creates it during
    setup and rewrites it on every ``hermes config set``. But a Hermes that has
    ever loaded its config also seeds ``SOUL.md`` and ten subdirectories beside
    it, so a config that is alone in its directory did not come from Hermes.
    Together the two mean the file is empty, we filled it, and nothing else has
    ever used the directory.
    """
    from ..formats import yaml_merge

    path = config_path()
    try:
        raw = _read_text(path)
    except (OSError, ValueError):
        return path, FileAction.KEPT
    if raw is None:
        return path, FileAction.NOT_FOUND
    bom, newline, existing_text = _prepare(raw)
    try:
        existing_doc = yaml_merge.load_mapping(existing_text or "")
    except (OSError, ValueError):
        # Same reason install declines: rewriting a file we could not read
        # destroys whatever we failed to read.
        return path, FileAction.KEPT

    servers = existing_doc.get(MCP_KEY)
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return path, FileAction.NOT_FOUND

    merged_text, section = yaml_merge.remove_child(existing_text, MCP_KEY, SERVER_NAME)

    intended = _deep_copy(existing_doc)
    del intended[MCP_KEY][SERVER_NAME]
    if not intended[MCP_KEY]:
        # What an emptied section leaves behind depends on how it was written:
        # the header stays when the user's comments are still inside it and
        # parses to ``None``, an inline one comes back as ``{}``, and otherwise
        # the key goes. Follow what the helper reports instead of assuming, or
        # the check below refuses a removal that was correct.
        if section is yaml_merge.ABSENT:
            del intended[MCP_KEY]
        else:
            intended[MCP_KEY] = section

    merged_text = _prune_allowlist(merged_text, intended)

    try:
        yaml_merge.verify(merged_text, intended)
    except ValueError:
        # A splice that did not produce the intended document must not be
        # written, and on the destructive verb that is worth reporting as
        # "left alone" rather than as a removal that did not happen.
        return path, FileAction.KEPT

    merged_text = bom + merged_text
    if not merged_text.strip() and _is_sole_occupant(path):
        try:
            path.unlink()
        except OSError:
            # Never report REMOVED over a file that still holds our entry. A
            # read-only bit is enough to cause this, and swallowing it turns a
            # loud failure into a silent false success on the destructive verb.
            yaml_merge.write_if_changed(
                path, merged_text, intended, existing_doc, newline=newline
            )
        return path, FileAction.REMOVED

    yaml_merge.write_if_changed(path, merged_text, intended, existing_doc, newline=newline)
    return path, FileAction.REMOVED


def _prune_allowlist(text: str, intended: dict) -> str:
    """Take ``repowise`` back out of ``platform_toolsets.cli``, mutating *intended*.

    Only when it is there, which is only when install put it there. If that
    leaves the list empty the ``cli`` key goes as well, restoring Hermes's
    built-in default rather than leaving behind a list that resolves to no
    toolsets at all -- which the host warns about, and which would be a worse
    state than the one before repowise was ever installed. That case cannot
    arise from anything install writes, since install only ever appends to a
    list that already named another server, but a hand-edited config can reach
    it and the destructive verb is the wrong place to be surprised.
    """
    from ..formats import yaml_merge

    toolsets = intended.get(TOOLSETS_KEY)
    if not isinstance(toolsets, dict):
        return text
    listed = toolsets.get(CLI_PLATFORM)
    if not isinstance(listed, list) or SERVER_NAME not in listed:
        return text

    remaining = [item for item in listed if item != SERVER_NAME]
    if remaining:
        toolsets[CLI_PLATFORM] = remaining
        return yaml_merge.set_child(text, TOOLSETS_KEY, CLI_PLATFORM, remaining)

    del toolsets[CLI_PLATFORM]
    pruned, section = yaml_merge.remove_child(text, TOOLSETS_KEY, CLI_PLATFORM)
    if not toolsets:
        # Same contract as the servers section above.
        if section is yaml_merge.ABSENT:
            del intended[TOOLSETS_KEY]
        else:
            intended[TOOLSETS_KEY] = section
    return pruned


def _is_sole_occupant(path: Path) -> bool:
    """Whether *path* is the only thing in its directory."""
    try:
        return [entry.name for entry in path.parent.iterdir()] == [path.name]
    except OSError:
        return False


def _prune_home() -> None:
    """Remove the Hermes home directory when a user-scope uninstall emptied it.

    Only via ``rmdir``, which refuses a directory holding anything, and only
    after something was actually removed. So a real Hermes home survives
    untouched. It matters because ``install`` creates this directory and
    :meth:`HermesTarget.is_present` reads it as evidence the user has Hermes:
    without the prune, an ``agents add`` followed by an ``agents remove`` would
    leave our own residue behind as the reason Hermes stays pre-ticked in every
    later checklist, on a machine that never had it.

    The symlink guard is the other half and it is not theoretical. ``rmdir``
    refuses a non-empty *directory*, and a Windows junction is not one: it is a
    reparse point, so ``rmdir`` unlinks it however full its target is. The
    target survives and the link does not.
    """
    import contextlib

    candidate = hermes_home()
    if candidate.is_symlink():
        return
    with contextlib.suppress(OSError):
        candidate.rmdir()


def _remove_instructions(scope: Scope, repo_path: Path) -> tuple[Path, FileAction, list[str]]:
    """Strip the managed block from ``AGENTS.md``, unless another agent needs it.

    ``AGENTS.md`` is a host-neutral convention rather than this target's private
    file: Codex and OpenCode read the same path in the same repo and manage the
    same marker block, and all three descriptors are right to claim it. Install
    is unaffected, because the block is marker-delimited and idempotent, so
    whichever agent writes last reports ``unchanged``. Uninstall is where the
    sharing bites, and the guard is the shared one rather than a third copy of
    the reasoning.

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

    path = instructions_path(repo_path)

    state = marker_block.inspect(path, DISTILL_MARKER_START, DISTILL_MARKER_END).state
    if state is BlockState.PRESENT:
        owners = other_managers_of(path, exclude=ID, scope=scope, repo_path=repo_path)
        if owners:
            return path, FileAction.KEPT, owners

    if marker_block.remove(path, DISTILL_MARKER_START, DISTILL_MARKER_END):
        return path, FileAction.REMOVED, []

    # ``remove`` returns False for several distinct reasons and they do not
    # mean the same thing to a reader. "There was nothing of ours here" is
    # not-found; "there is a file we deliberately did not touch" is kept.
    if state in (BlockState.ABSENT_FILE, BlockState.ABSENT):
        return path, FileAction.NOT_FOUND, []
    return path, FileAction.KEPT, []


def _reads_repowise() -> bool:
    """Whether the user config registers the repowise MCP server."""
    path = config_path()
    if not path.exists():
        return False
    try:
        doc = _load_config()
    except (OSError, ValueError):
        # ``UnicodeDecodeError`` is a ``ValueError`` and not an ``OSError``, and
        # a file we cannot decode tells us nothing either way.
        return False
    servers = doc.get(MCP_KEY)
    return isinstance(servers, dict) and SERVER_NAME in servers


def _load_config() -> dict:
    from ..formats import yaml_merge

    raw = _read_text(config_path())
    return yaml_merge.load_mapping("" if raw is None else raw)


def _has_managed_block(repo_path: Path) -> bool:
    from ..formats import marker_block
    from ..formats.marker_block import BlockState
    from ..instructions import DISTILL_MARKER_END, DISTILL_MARKER_START

    state = marker_block.inspect(
        instructions_path(repo_path), DISTILL_MARKER_START, DISTILL_MARKER_END
    ).state
    return state is BlockState.PRESENT


def detect(repo_path: Path | None = None) -> list[Registration]:
    """Every place Hermes is currently wired to repowise. Never raises.

    The user-scope registration is the MCP entry, which is the only thing that
    makes repowise reachable at all.

    **The project-scope registration is deliberately conditional on the user
    one**, and the reason is not bookkeeping. The repo half of this target is a
    managed block in ``AGENTS.md``, a file two other agents also write. Reading
    that block on its own as "Hermes is wired here" would report Hermes as an
    owner of ``AGENTS.md`` on a machine that has never had Hermes -- and
    ``registry.other_managers_of`` asks exactly that question before letting
    ``agents remove --target=codex`` strip the block, so removing Codex would
    start refusing on behalf of an agent that does not exist. Requiring the
    global MCP entry as well makes the claim true in both directions: Hermes
    can reach repowise, and this repo tells it to. That is also what lets
    ``agents refresh`` rewrite the repo block, since refresh skips a scope it
    was told is unwired.

    **There is no text-probe fallback for a config this cannot parse**, and the
    difference from ``opencode.py`` is the point rather than an omission. That
    target probes the raw text of a config it failed to parse, because its host
    accepts comments in JSON and a file ``json.loads`` rejects is usually a
    working config; answering "not wired" for one would strip a live agent's
    instructions. YAML has no such gap. Comments are part of the grammar and
    parse fine, and Hermes reads this file with the same UTF-8 strictness, so a
    ``config.yaml`` repowise cannot parse is one Hermes cannot parse either.
    There is no working agent behind it to protect.
    """
    found: list[Registration] = []
    if not _reads_repowise():
        return found

    config = config_path()
    found.append(Registration(method="direct", scope=Scope.USER, config_path=config))

    if repo_path is not None:
        try:
            has_block = _has_managed_block(repo_path)
        except (OSError, ValueError):
            has_block = False
        if has_block:
            found.append(
                Registration(
                    method="direct",
                    scope=Scope.PROJECT,
                    config_path=instructions_path(repo_path),
                    detail="managed instructions block",
                )
            )
    return found


class HermesTarget:
    """Descriptor for Hermes. See the module docstring."""

    id = ID
    display_name = DISPLAY_NAME
    docs_url = DOCS_URL
    hook_adapter = None
    session_adapter = None
    methods = METHODS
    project_file_id = PROJECT_FILE_ID

    def supports_scope(self, scope: Scope) -> bool:
        """Both, and each writes a different file.

        User scope is the MCP registration in the one ``config.yaml`` Hermes
        reads; there is no project config file to put it in. Project scope is
        the managed ``AGENTS.md`` block, which Hermes loads per repo. Neither
        scope is a subset of the other, so both are real.
        """
        return True

    def is_present(self, repo_path: Path | None = None) -> bool:
        """The ``hermes`` binary on PATH, or a Hermes home directory.

        No repo-local limb. Hermes keeps nothing repo-local of its own -- the
        only repo file in play is ``AGENTS.md``, which is a host-neutral
        convention two other agents also write, so reading it as evidence the
        user has Hermes would pre-tick this agent for anyone using Codex.
        """
        import shutil

        if shutil.which("hermes") is not None:
            return True
        return hermes_home().is_dir()

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

        if scope is Scope.USER:
            self._install_user(result)
        else:
            self._install_project(result, repo_path)
        return result

    def _install_user(self, result: WriteResult) -> None:
        # The write is guarded, and the guard is not theoretical. Nothing wraps
        # ``install`` -- ``agents add``, ``agents refresh`` and
        # ``doctor --repair`` all call it bare -- so anything escaping here
        # aborts the run after other agents' configs have already been written
        # and prints a traceback in place of the summary naming them.
        #
        # ``OSError`` sits alongside ``ValueError`` because the read, the parent
        # ``mkdir`` and the command resolution all live inside the guarded call.
        # ``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``, and is
        # the ordinary way a cp1252 config on Windows reaches this.
        path = config_path()
        try:
            written = write_mcp_config()
            result.record(written.path, written.action)
        except RemoteServerEntryError:
            result.record(path, FileAction.KEPT)
            result.note(
                f'{path} left unchanged: its "{SERVER_NAME}" entry names a remote '
                "server, and Hermes prefers a URL over a command even when both are "
                "present, so merging ours in would leave an entry that never launches. "
                "Run 'repowise agents remove --target=hermes' first if you want the "
                "local server instead."
            )
            return
        except (ValueError, OSError) as exc:
            result.record(path, FileAction.KEPT)
            # The reason is carried rather than asserted: this guard is wide
            # enough to catch things that are not a bad config file at all, and
            # a note that flatly said "not valid YAML" would send the user to
            # inspect a file that is fine.
            result.note(
                f"{path} left unchanged ({exc}). Run "
                f"'repowise agents print-config hermes' and paste the entry under "
                f'"{MCP_KEY}".'
            )
            return

        self._note_toolset_state(result)

    def _note_toolset_state(self, result: WriteResult) -> None:
        """Say so when the config filters repowise back out after a good write.

        Both cases are silent otherwise, and both look exactly like a working
        install right up to the point where the tools do not appear.
        """
        try:
            doc = _load_config()
        except (OSError, ValueError):
            return
        toolsets = doc.get(TOOLSETS_KEY)
        if not isinstance(toolsets, dict):
            return
        listed = toolsets.get(CLI_PLATFORM)
        if not isinstance(listed, list):
            return
        if NO_MCP in {str(item) for item in listed}:
            result.note(
                f"{config_path()} sets '{TOOLSETS_KEY}.{CLI_PLATFORM}' to include "
                f"'{NO_MCP}', which turns every MCP server off for the Hermes CLI. "
                "The repowise entry was written and will stay inert until that is "
                "removed."
            )

    def _install_project(self, result: WriteResult, repo_path: Path) -> None:
        try:
            written = write_instructions(repo_path)
            result.record(written.path, written.action)
            if written.action is FileAction.KEPT:
                # The helper refuses an orphaned or duplicated marker pair
                # rather than guessing at a repair that could swallow the
                # user's own text, so name the file to unpick instead of
                # reporting a write that did not happen.
                result.note(
                    f"{instructions_path(repo_path)} left unchanged: its Repowise "
                    "markers are unpaired or duplicated, or the file could not be "
                    "read. Fix it by hand and re-run."
                )
        except (OSError, ValueError) as exc:
            result.record(instructions_path(repo_path), FileAction.KEPT)
            result.note(f"AGENTS.md could not be written ({exc}).")

    def uninstall(self, scope: Scope, *, repo_path: Path | None = None) -> WriteResult:
        """Remove whatever install writes at this scope, and nothing else."""
        result = WriteResult()
        if scope is Scope.PROJECT and repo_path is None:
            raise ValueError("project-scope uninstall needs a repo_path")

        if scope is Scope.USER:
            path, action = _remove_server_entry()
            result.record(path, action)
            if action is FileAction.REMOVED:
                _prune_home()
            return result

        assert repo_path is not None  # narrowed by the guard above
        instructions, action, owners = _remove_instructions(scope, repo_path)
        result.record(instructions, action)
        if owners:
            result.note(
                f"{instructions} kept: {' and '.join(owners)} still reads the same "
                "managed block. Remove that agent too if you want the block gone."
            )
        return result

    def print_config(self, scope: Scope, *, repo_path: Path | None = None) -> str:
        """The entry to paste, in the shape Hermes reads.

        Only the ``mcp_servers`` block. ``platform_toolsets`` is deliberately
        not printed: on the configs where repowise declines to write it, adding
        it by hand does the same damage described in :func:`_mcp_allowlist`, and
        a snippet a user is invited to paste has no way to carry the condition.
        The one case where the entry is needed is the one where the user
        already maintains that list and knows what it is for.
        """
        from ..formats import yaml_merge

        return "\n".join(
            [f"{MCP_KEY}:", *yaml_merge.render_child(SERVER_NAME, server_entry(), 2)]
        )

    def describe_paths(self, scope: Scope, *, repo_path: Path | None = None) -> list[str]:
        if scope is Scope.USER:
            return [str(config_path())]
        return [str(instructions_path(repo_path or Path.cwd()))]

    def doctor(self) -> DoctorReport:
        """User-scope health, which is the only half a bare call can see.

        ``doctor()`` takes no repo path, so the repo's ``AGENTS.md`` is out of
        reach and reporting on its behalf would be a claim nothing checked.

        An unreadable config **is** reported as ``BROKEN`` here, which is where
        this differs from OpenCode. That target declines to call an unparseable
        file damaged because its host accepts comments in JSON, so the common
        cause of a parse failure is a perfectly legal file. YAML has no such
        excuse: comments are part of the grammar and parse fine, so a
        ``config.yaml`` that will not parse is genuinely damaged, and Hermes
        cannot read it either.
        """
        path = config_path()
        if _reads_repowise():
            return DoctorReport(target_id=ID, status=DoctorStatus.OK)

        if path.exists() and not _parses(path):
            return DoctorReport(
                target_id=ID,
                status=DoctorStatus.BROKEN,
                issues=(
                    f"{path} could not be read as YAML, so Hermes cannot read it "
                    "either. Fix or remove it and re-run.",
                ),
                # Refresh skips a target it cannot detect, and install declines
                # for the same reason this check failed, so ``--repair`` has
                # nothing to offer here.
                repairable=False,
                fix_command="repowise agents add --target=hermes",
            )

        return DoctorReport(
            target_id=ID,
            status=DoctorStatus.NOT_INSTALLED,
            issues=(
                "Hermes is not wired at user scope; the managed AGENTS.md block is "
                "repo-local, so run this from a repo to check that half.",
            ),
            fix_command="repowise agents add --target=hermes",
        )


def _parses(path: Path) -> bool:
    """Whether *path* can be read as a YAML mapping at all."""
    from ..formats import yaml_merge

    try:
        raw = _read_text(path)
        yaml_merge.load_mapping("" if raw is None else raw)
    except (OSError, ValueError):
        return False
    return True


TARGET = HermesTarget()
