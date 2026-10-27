"""Env files (``.env``, ``.env.*``, ``*.env``): the addresses a stack's env keys default to.

``CATALOGUE_URL=http://catalogue:8080`` makes ``CATALOGUE_URL`` a reference to
the host ``catalogue``, as a compose ``environment`` entry does; a compose
``env_file:`` points at these files. Only values :func:`.base.host_of` reads as
addresses become claims: every other value (a password, a token) is never kept.
A sample file's ``localhost`` names no workspace repo, so it resolves to nothing.
A key an env file and a compose file (or two env files) point at different
repos is dropped as ambiguous, as for every other source.

Ceiling: an ``env_file:`` with another file name (``api.conf``) is not read;
following each compose reference is the upgrade if estates use such names.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .base import ENV, Claim, ServiceNameSource, SourceFile, host_of, with_defaults

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=(.*)$")
_COMMENT_RE = re.compile(r"\s#")


def read(file: SourceFile) -> Iterable[Claim]:
    for line in file.text.splitlines():
        m = _LINE_RE.match(line)
        if m is None:
            continue
        key, value = m.groups()
        host = host_of(key, with_defaults(_COMMENT_RE.split(value, maxsplit=1)[0]))
        if host:
            yield Claim(name=key.lower(), kind=ENV, ref=host)


SOURCE = ServiceNameSource(
    name="env_file",
    filenames=(".env", ".env.*", "*.env"),
    marker="=",
    read=read,
)
