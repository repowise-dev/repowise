"""Env files (``.env``, ``.env.*``, ``*.env``): the addresses a stack's env keys default to.

``CATALOGUE_URL=http://catalogue:8080`` makes ``CATALOGUE_URL`` a reference to
the host ``catalogue``, as a compose ``environment`` entry does; a compose
``env_file:`` points at these files. Only values :func:`.base.host_of` reads as
addresses become claims: every other value (a password, a token) is never kept.
A sample file's ``localhost`` names no workspace repo, so it resolves to nothing.

Ceiling: an ``env_file:`` with another file name (``api.conf``) is not read;
following each compose reference is the upgrade if estates use such names.
"""

from __future__ import annotations

from collections.abc import Iterable

from .base import ENV, Claim, ServiceNameSource, SourceFile, host_of, with_defaults


def read(file: SourceFile) -> Iterable[Claim]:
    for line in file.text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or key.startswith("#") or " " in key:
            continue
        host = host_of(key, with_defaults(value.split(" #", 1)[0]))
        if host:
            yield Claim(name=key.lower(), kind=ENV, ref=host)


SOURCE = ServiceNameSource(
    name="env_file",
    filenames=(".env", ".env.*", "*.env"),
    marker="=",
    read=read,
)
