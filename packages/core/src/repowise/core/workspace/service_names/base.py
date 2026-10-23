"""What a service-name source declares, and what it hands the engine.

A source is data: the file names it reads (its gate), the directories they
must sit under, a marker the text must contain before it is parsed, and one
``read`` function that turns a file's text into :class:`Claim` rows. The engine
in :mod:`.` walks each repo once, evaluates every gate once per file, and
turns claims into ``name -> repo`` entries, so a new source is one module and
one registry line.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TypeVar

_P = TypeVar("_P", bound=PurePath)

#: A name a consumer uses as its URL host (``http://catalogue:8080``).
HOST = "host"
#: A config key whose value names a host (``CATALOGUE_URL: http://catalogue:8080``),
#: matched against a consumer's unresolved base token (``${CATALOGUE_URL}``).
ENV = "env"


@dataclass(frozen=True)
class Claim:
    """One file's statement that *name* is served by some repo.

    Where it points, tried in order: ``at`` (the directory the service is
    built from; the repo holding it serves *name*), ``aliases`` (repo aliases
    the file names for it, such as an image basename or a selector label; the
    first one in the workspace wins), ``ref`` (an :data:`ENV` key's host,
    resolved through the finished host map), and ``own_repo`` (the repo holding
    the file, kept only when it is the only name the source places in that
    repo this way, so a deploy repo listing every service claims none).
    """

    name: str
    kind: str = HOST
    at: Path | None = None
    aliases: tuple[str, ...] = ()
    ref: str | None = None
    own_repo: bool = False


@dataclass(frozen=True)
class SourceFile:
    """A gated file handed to a source's ``read``."""

    path: Path
    text: str


@dataclass(frozen=True)
class ServiceNameSource:
    """One place service names are declared: compose, Kubernetes, Spring, Aspire, ...

    ``filenames`` are case-insensitive globs on the file's own name; ``under``,
    when set, requires an ancestor directory (inside the repo) with one of
    those names; ``marker`` must occur in the text before ``read`` parses it.
    A gateway source would add a path rewrite to its claims for the matcher to
    apply; nothing here assumes a claim is a plain host.
    """

    name: str
    filenames: tuple[str, ...]
    read: Callable[[SourceFile], Iterable[Claim]]
    under: frozenset[str] = frozenset()
    marker: str = ""
    _gate: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        pattern = "|".join(fnmatch.translate(g.lower()) for g in self.filenames)
        object.__setattr__(self, "_gate", re.compile(pattern) if pattern else re.compile(r"(?!)"))

    def gates(self, filename: str, parent_dirs: tuple[str, ...]) -> bool:
        """True when a file with this name under these (lowercased) directories is read."""
        if not self._gate.match(filename.lower()):
            return False
        return not self.under or any(d in self.under for d in parent_dirs)


def join_config_path(base: _P, ref: str) -> _P:
    """*ref*, a path written in a config file, joined onto *base* on any OS.

    Config written on Windows (an AppHost's ``..\Catalog.API``, a compose
    ``build: .\cart``) is indexed on Linux too, where a backslash is not a
    separator, so both separators split it here.
    """
    parts = [p for p in ref.replace("\\", "/").split("/") if p not in ("", ".")]
    return base.joinpath(*parts)


_URL_HOST_RE = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/]*@)?([A-Za-z0-9][^/:?#]*)", re.IGNORECASE)
_HOST_PORT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*):\d+/?$")
_BARE_HOST_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*)$")
# A bare word is an address only under a key that says so: ``CART_HOST: cart``
# is, ``MYSQL_USER: gva`` is not.
_ADDRESS_KEY_RE = re.compile(
    r"(?:^|_)(?:HOST|HOSTNAME|URL|URI|ADDR|ADDRESS|ENDPOINT|SERVER|SERVICE)$", re.IGNORECASE
)


def host_of(key: str, value: str) -> str | None:
    """The host config entry *key* = *value* names, or None when it is not an address.

    ``http://cart:8080/x`` and ``cart:8080`` name ``cart`` under any key; a bare
    ``cart`` only under an address-like key (``CART_HOST``, ``CART_URL``).
    """
    value = value.strip().strip("'\"")
    if "${" in value or "{{" in value:
        return None
    m = _URL_HOST_RE.match(value) or _HOST_PORT_RE.match(value)
    if m is None and _ADDRESS_KEY_RE.search(key):
        m = _BARE_HOST_RE.match(value)
    return m.group(1).lower() if m else None


__all__ = ["ENV", "HOST", "Claim", "ServiceNameSource", "SourceFile", "host_of", "join_config_path"]
