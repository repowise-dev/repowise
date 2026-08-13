"""Whether a stored MCP server entry is one repowise can safely repoint.

One rule, shared, because getting it wrong has now produced the same bug in
three different config files. Every host that writes MCP servers as JSON
supports a local transport (a command repowise launches) and a remote one (a
URL someone else hosts), and repowise only ever writes the local kind.

The trap is that the merge helper is *careful*. It lets generated keys win and
keeps every other key the user added, which is right for ``command`` and
``args`` and for an ``env`` block carrying provider keys. Against a hand-wired
remote server it is exactly wrong: it forces ``type`` back to the local value
while faithfully preserving the ``url`` beside it, producing an entry that is
neither a valid local server nor a valid remote one. **The preservation rule is
what makes the result broken rather than merely overwritten**, and a
schema-validated config can reject the whole file over the stray key rather
than just the one entry.

So a remote entry is left alone and said so. It is a deliberate choice repowise
did not make, and converting it in place is not a conversion.
"""

from __future__ import annotations


class RemoteServerEntryError(ValueError):
    """The stored entry names a transport repowise did not write.

    A ``ValueError`` so it lands in the same handler as an unparseable file, and
    a distinct type so the caller can say which of the two happened. Both mean
    "left alone"; only one of them is a broken file.
    """


def is_remote_entry(entry: dict, *, local_type: str) -> bool:
    """Whether *entry* describes a transport repowise did not write.

    *local_type* is the host's spelling of "a command we launch", because the
    hosts disagree: VS Code and Cursor say ``stdio``, OpenCode says ``local``.
    Passing it in rather than accepting any of them keeps a config that names
    another host's word for it from being silently treated as ours.

    **The entry's own declared type decides.** A bare ``url`` counts only when
    there is no ``command`` beside it: reading ``"url" in entry`` first, ahead
    of the declared type, calls ``{"type": "stdio", "command": ..., "url": ...}``
    remote in contradiction of what the entry says about itself. That shape is
    the ordinary stale state ``agents add`` exists to repoint, and treating it
    as remote wedges it shut against every command that could have fixed it.
    """
    declared = entry.get("type")
    if declared is not None:
        return declared != local_type
    return "url" in entry and "command" not in entry
