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
class Route:
    """A gateway's path rule: a request under ``prefix`` is forwarded with that prefix
    replaced by ``upstream`` (``""`` strips it, ``prefix`` keeps it).

    Both are lowercase literal paths without a trailing slash. ``upstream`` is
    None when the route's forwarded path cannot be read (a filter or predicate
    the source does not model): no call is rewritten through it, and it still
    keeps a shorter prefix from claiming the paths under it. ``gateway`` names
    the gateway service when the file configures an app that lives elsewhere (a
    config server's ``gateway.yml``), as candidate names tried in order through
    the host map and the repo aliases; empty means the repo holding the file.
    """

    prefix: str
    upstream: str | None = ""
    gateway: tuple[str, ...] = ()


@dataclass(frozen=True)
class Claim:
    """One file's statement that *name* is served by some repo.

    A claim with a ``base_path`` says instead that the module at ``at`` (or,
    without ``at``, the service *name*) serves every route under that path.

    Where it points, tried in order: ``at`` (the directory the service is
    built from; the repo holding it serves *name*), ``aliases`` (repo aliases
    the file names for it, such as an image basename or a selector label; the
    first one in the workspace wins), ``ref`` (an :data:`ENV` key's host,
    resolved through the finished host map), and ``own_repo`` (the repo holding
    the file, kept only when it is the only name the source places in that
    repo this way, so a deploy repo listing every service claims none).

    A claim with a ``route`` says instead that a gateway forwards that path
    prefix to *name*; *name* resolves like a ``ref``, through the finished host
    map and then the repo aliases, or with ``kind`` :data:`ENV` through the
    finished env keys (an nginx template's ``${CATALOGUE_HOST}``).
    """

    name: str
    kind: str = HOST
    base_path: str | None = None
    at: Path | None = None
    aliases: tuple[str, ...] = ()
    ref: str | None = None
    own_repo: bool = False
    route: Route | None = None


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
    A gateway source states :class:`Route` claims, which the matcher applies as
    path rewrites.
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
    r"""*ref*, a path written in a config file, joined onto *base* on any OS.

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


_PATTERN_SEGMENT_RE = re.compile(r"[*{}?<>()\[\]$^]")
_WHOLE_SEGMENT_RE = re.compile(r"^(?:\*{1,2}|\{[^{}]*\})$")

#: :func:`literal_prefix` kinds: a whole wildcard or variable segment follows the
#: prefix (a prefix route), the pattern is all literal, or a segment mixes text
#: and a wildcard (``orders*``, ``v{n}``), so only the part before it is known.
PREFIX = "prefix"
EXACT = "exact"
PARTIAL = "partial"


def literal_prefix(pattern: str) -> tuple[str, str]:
    """The literal leading path of a route pattern, and what follows it.

    ``/api/vet/**`` is ``("/api/vet", PREFIX)``, ``/orders/{id}`` is
    ``("/orders", PREFIX)``, ``/health`` is ``("/health", EXACT)`` and
    ``/api/orders*`` is ``("/api", PARTIAL)``. Lowercase, no trailing slash;
    ``""`` when the pattern starts with a wildcard.
    """
    literal: list[str] = []
    for seg in (s for s in pattern.strip().split("/") if s):
        if _PATTERN_SEGMENT_RE.search(seg):
            kind = PREFIX if _WHOLE_SEGMENT_RE.match(seg) else PARTIAL
            return ("/" + "/".join(literal) if literal else ""), kind
        literal.append(seg.lower())
    return ("/" + "/".join(literal) if literal else ""), EXACT


def _variable(seg: str) -> str:
    """A whole-segment variable by name: ``{**rest}``, ``{*rest}`` and ``{rest:int}`` are ``{rest}``."""
    return "{" + seg[1:-1].lstrip("*").split(":")[0].split("=")[0].rstrip("?").lower() + "}"


def template_upstream(pattern: str, forwarded: str) -> str | None:
    """What a gateway forwarding path template *pattern* as *forwarded* puts in place of
    *pattern*'s literal prefix, or None when that is not a prefix rewrite.

    ``/api/c/{everything}`` forwarded as ``/{everything}`` is ``""`` (stripped);
    ``/posts/{id}`` as ``/api/posts/{id}`` is ``/api/posts``. Both must end in the
    same whole-segment variables, in order. A literal after a variable
    (``/api/{version}/c/{all}``), a segment mixing text and a variable, or a fully
    literal pattern (it serves one path, not a prefix) is None.
    """
    heads: list[str] = []
    tails: list[list[str]] = []
    for template in (pattern, forwarded):
        head, kind = literal_prefix(template)
        tail = [s for s in template.strip().split("/") if s][head.count("/") :]
        if kind != PREFIX or not all(_WHOLE_SEGMENT_RE.match(s) for s in tail):
            return None
        heads.append(head)
        tails.append([_variable(s) for s in tail])
    return heads[1] if tails[0] == tails[1] else None


_DEFAULT_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^${}]*)\}")


def with_defaults(value: str) -> str:
    """*value* with each ``${VAR-default}`` / ``${VAR:-default}`` read as its default.

    The value compose (and a shell) uses when nothing sets the variable. Any other
    placeholder stays, so :func:`host_of` still refuses it.
    """
    return _DEFAULT_RE.sub(r"\1", value) if "${" in value else value


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


__all__ = [
    "ENV",
    "EXACT",
    "HOST",
    "PARTIAL",
    "PREFIX",
    "Claim",
    "Route",
    "ServiceNameSource",
    "SourceFile",
    "host_of",
    "join_config_path",
    "literal_prefix",
    "template_upstream",
    "with_defaults",
]
