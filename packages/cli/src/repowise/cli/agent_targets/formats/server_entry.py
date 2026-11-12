"""The one MCP server entry a writer puts in a host's server map, and its name.

One rule, shared, because getting it wrong has now produced the same bug in
three different config files. Every host that writes MCP servers as JSON
supports a local transport (a command repowise launches) and a remote one (a
URL someone else hosts).

The trap is that the merge helper is *careful*. It lets generated keys win and
keeps every other key the user added, which is right for ``command`` and
``args`` and for an ``env`` block carrying provider keys. Against a hand-wired
remote server it is exactly wrong: it forces ``type`` back to the local value
while faithfully preserving the ``url`` beside it, producing an entry that is
neither a valid local server nor a valid remote one. **The preservation rule is
what makes the result broken rather than merely overwritten**, and a
schema-validated config can reject the whole file over the stray key rather
than just the one entry.

So a local write never lands on a remote entry: it is left alone and said so.

The hosted server (``agents add --hosted``) goes through the same writers with
a hosted :class:`ServerSpec`. Its name follows one rule for every host: it is
``repowise`` when nothing local is called that, and ``repowise-hosted`` when a
local server already is. A local entry is never overwritten by a hosted one,
and a re-run finds the entry it wrote by URL, so it is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..types import FileAction, FileWrite, WriteResult

#: The name every local repowise server is registered under.
LOCAL_NAME = "repowise"

#: The hosted server's name when a local server already holds :data:`LOCAL_NAME`.
HOSTED_NAME = "repowise-hosted"

#: What to do about a remote ``repowise`` entry when the local server wants that
#: name. One sentence, shared, so every host and ``init`` say the same thing.
RENAME_HINT = (
    f'its "{LOCAL_NAME}" entry names a remote server (Repowise hosted). '
    f'Rename that entry to "{HOSTED_NAME}" and re-run to add the local server beside it'
)


class RemoteServerEntryError(ValueError):
    """The stored entry names a transport repowise did not write.

    A ``ValueError`` so it lands in the same handler as an unparseable file, and
    a distinct type so the caller can say which of the two happened. Both mean
    "left alone"; only one of them is a broken file.
    """


def remote_url(entry: Mapping) -> str | None:
    """The URL a remote entry points at. Windsurf spells the key ``serverUrl``."""
    url = entry.get("url") or entry.get("serverUrl")
    return url if isinstance(url, str) else None


def is_remote_entry(entry: Mapping, *, local_type: str) -> bool:
    """Whether *entry* describes a remote transport rather than a launched command.

    *local_type* is the host's spelling of "a command we launch", because the
    hosts disagree: VS Code and Cursor say ``stdio``, OpenCode says ``local``.
    Passing it in rather than accepting any of them keeps a config that names
    another host's word for it from being silently treated as ours.

    **The entry's own declared type decides.** A bare URL counts only when
    there is no ``command`` beside it: reading ``"url" in entry`` first, ahead
    of the declared type, calls ``{"type": "stdio", "command": ..., "url": ...}``
    remote in contradiction of what the entry says about itself. That shape is
    the ordinary stale state ``agents add`` exists to repoint, and treating it
    as remote wedges it shut against every command that could have fixed it.
    """
    declared = entry.get("type")
    if declared is not None:
        return declared != local_type
    return remote_url(entry) is not None and "command" not in entry


@dataclass(frozen=True)
class ServerSpec:
    """The entry a writer was asked to put in a server map: local or hosted.

    One writer per host takes either, so the hosted server is a variant of the
    local write rather than a second set of writers. The spec decides the name
    (:meth:`name_in`); the host decides the entry's shape and where it lives.
    """

    entry: dict
    hosted: bool = False

    def name_in(
        self,
        servers: Mapping,
        *,
        local_type: str = "stdio",
        local_elsewhere: bool = False,
    ) -> str:
        """The key this entry goes under in *servers*.

        Local: always :data:`LOCAL_NAME`, refusing (``RemoteServerEntryError``)
        when that key holds a remote entry.

        Hosted, in order:

        * the key already holding a remote entry for the same URL (a re-run);
        * :data:`LOCAL_NAME` when it holds a remote entry and nothing local is
          loaded elsewhere (repointed in place);
        * :data:`HOSTED_NAME` when a local server holds :data:`LOCAL_NAME`, in
          this map or anywhere else the host loads it from (*local_elsewhere*),
          or when a remote entry already holds :data:`HOSTED_NAME`;
        * :data:`LOCAL_NAME` otherwise.
        """

        def remote(name: str) -> dict | None:
            stored = servers.get(name)
            if isinstance(stored, dict) and is_remote_entry(stored, local_type=local_type):
                return stored
            return None

        if not self.hosted:
            if remote(LOCAL_NAME) is not None:
                raise RemoteServerEntryError(f"its '{LOCAL_NAME}' entry names a remote server")
            return LOCAL_NAME

        url = remote_url(self.entry)
        for name in (LOCAL_NAME, HOSTED_NAME):
            stored = remote(name)
            if stored is not None and remote_url(stored) == url:
                return name
        if remote(LOCAL_NAME) is not None and not local_elsewhere:
            return LOCAL_NAME
        if local_elsewhere or LOCAL_NAME in servers or remote(HOSTED_NAME) is not None:
            return HOSTED_NAME
        return LOCAL_NAME

    def merge_base(self, stored: dict) -> dict:
        """What of the *stored* entry a merge keeps before this spec's keys land.

        Everything for a local spec (an ``env`` block survives). For a hosted
        spec the transport keys it does not set go first (``type``, ``url``,
        ``serverUrl``): a remote entry repointed in place must not keep the old
        URL under another host's spelling beside the new one. The keys it does
        set are overwritten where they stand, so a re-run keeps the key order
        and the bytes. Other keys (``headers``) stay.
        """
        if not self.hosted:
            return stored
        dropped = {"type", "url", "serverUrl"} - set(self.entry)
        return {k: v for k, v in stored.items() if k not in dropped}

    def repointed_from(self, stored: object) -> str | None:
        """A note when a hosted write moves a remote entry off another URL.

        A per-user config (Windsurf's) holds one entry per name for every repo,
        so connecting a second repo moves it. Said, never silent.
        """
        if not self.hosted or not isinstance(stored, dict):
            return None
        old = remote_url(stored)
        if old is None or old == remote_url(self.entry):
            return None
        return f"repointed from {old}"


def write_json_servers(
    path: Path,
    container_key: str,
    spec: ServerSpec,
    *,
    local_type: str = "stdio",
    local_elsewhere: bool = False,
    merge: Callable[[dict, dict], object] | None = None,
) -> tuple[str, FileWrite]:
    """Upsert *spec* into the ``container_key`` map of a JSON config.

    The write every host keyed as ``{container_key: {name: entry}}`` shares:
    read strictly (``ValueError`` for a file that is not strict JSON or not
    shaped as expected, so the caller can decline rather than destroy a
    commented config), pick the name, merge, and write only when the bytes
    would move. Returns the name used and what happened to the file.

    *merge* defaults to :func:`~.json_merge.merge_server_entries`, which keeps
    every key the user added to an existing entry. A host with its own merge
    rule (OpenCode's ``enabled``) passes one; it updates the map in place.
    """
    from .json_merge import load_json_object_or_value_error, merge_server_entries, write_json_config

    existing = load_json_object_or_value_error(path, path.name) if path.exists() else {}
    servers = existing.get(container_key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path.name} '{container_key}' must be a JSON object")
    servers = dict(servers)
    name = spec.name_in(servers, local_type=local_type, local_elsewhere=local_elsewhere)
    stored = servers.get(name)
    reason = spec.repointed_from(stored)
    if isinstance(stored, dict):
        servers[name] = spec.merge_base(stored)
    (merge or merge_server_entries)(servers, {name: spec.entry})
    existing[container_key] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    return name, FileWrite(path=path, action=write_json_config(path, existing), reason=reason)


def install_hosted(
    path: Path,
    write: Callable[[ServerSpec], tuple[str, FileWrite]],
    entry: dict,
) -> WriteResult:
    """Run one host's writer with the hosted *entry*, as an install result.

    The hosted half of every ``install``: the host supplies its own writer (the
    same one its local install uses) and the remote entry in its own shape, and
    this records what happened. A file the writer declines (not strict JSON or
    TOML, unreadable, wrong shape) is ``KEPT`` with the reason, never rewritten.
    """
    import click

    result = WriteResult()
    try:
        name, written = write(ServerSpec(entry=entry, hosted=True))
    except (ValueError, OSError, click.ClickException) as exc:
        detail = exc.format_message() if isinstance(exc, click.ClickException) else str(exc)
        result.record(path, FileAction.KEPT, f"could not be rewritten ({detail})")
        return result
    result.record(written.path, written.action, written.reason)
    result.server_name = name
    return result
