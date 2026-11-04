"""Dockerfile ``ENV``: the addresses an image's defaults give its env keys.

``ENV CATALOGUE_HOST=catalogue`` makes ``CATALOGUE_HOST`` a reference to the
host ``catalogue``, as a compose ``environment`` entry does; an nginx template
or a consumer's ``${CATALOGUE_HOST}`` resolves through it. Both the
``KEY=value ...`` and the legacy ``KEY value`` forms are read, and only values
:func:`.base.host_of` reads as addresses. A compose or deploy value that sets
the same key to another host makes the key ambiguous, so it is dropped rather
than resolved to the image default.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Iterator

from .base import ENV, Claim, ServiceNameSource, SourceFile, host_of

_CONTINUATION_RE = re.compile(r"\\\r?\n")
_ENV_RE = re.compile(r"^\s*ENV\s+(.+)$", re.IGNORECASE | re.MULTILINE)


def _pairs(args: str) -> Iterator[tuple[str, str]]:
    try:
        words = shlex.split(args, comments=False)
    except ValueError:
        return
    if not words:
        return
    if "=" not in words[0]:
        yield words[0], " ".join(words[1:])  # legacy ``ENV KEY value``
        return
    for word in words:
        key, sep, value = word.partition("=")
        if sep:
            yield key, value


def read(file: SourceFile) -> Iterable[Claim]:
    for m in _ENV_RE.finditer(_CONTINUATION_RE.sub(" ", file.text)):
        for key, value in _pairs(m.group(1)):
            host = host_of(key, value)
            if host:
                yield Claim(name=key.lower(), kind=ENV, ref=host)


SOURCE = ServiceNameSource(
    name="dockerfile_env",
    filenames=("Dockerfile", "Dockerfile.*", "*.Dockerfile"),
    marker="ENV",
    read=read,
)
