"""nginx ``proxy_pass``: which path prefix an nginx front forwards to which service.

``location /api/catalogue/ { proxy_pass http://catalogue:8080/; }`` forwards
``/api/catalogue/products`` as ``/products``: a ``proxy_pass`` with a path
replaces the matched prefix with that path, one without passes the request
path unchanged. The upstream host names the service: a literal host, an
``upstream`` block whose servers all name one host, or an ``envsubst``
placeholder (``${CATALOGUE_HOST}`` in a ``*.conf.template``) resolved through
the env keys the workspace's config declares. The gateway is the repo holding
the file.

A location the reader cannot rewrite through is still stated, with no
forwarded path, so it keeps a shorter prefix from claiming its calls: one
served locally (``root``, ``return``, ``fastcgi_pass``, ...), an exact (``=``)
location, a ``rewrite`` in it or around it, a ``proxy_pass`` under ``if``, with
a variable path, or whose trailing slash differs from the location's, or a host
only known at runtime (``$var``). Regex and named locations are skipped
(ceiling: a regex location that overrides a prefix location is not seen), as
is the catch-all ``location /``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .base import ENV, EXACT, HOST, Claim, Route, ServiceNameSource, SourceFile, literal_prefix

# A word may hold ``${VAR}``, whose braces are not block delimiters, and a
# ``#`` that does not start it.
_WORD = r"(?:\$\{[^}\s]*\}|[^\s{};\"'#])(?:\$\{[^}\s]*\}|[^\s{};\"'])*"
_TOKEN_RE = re.compile(
    r"\s+|#[^\n]*|\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'|([{};])|(" + _WORD + ")"
)
_PROXY_RE = re.compile(r"^[a-z][a-z0-9+.-]*://([^/]+)(/.*)?$", re.IGNORECASE)
_ENVSUBST_RE = re.compile(r"^\$\{(\w+)\}$")
_HOSTNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]*$", re.IGNORECASE)
_PORT_RE = re.compile(r":\d+$")
_ODD_URI_RE = re.compile(r"[$?#]|/\.\.?(?:/|$)")


@dataclass
class _Block:
    """One directive: its name, its arguments and, for a block, its children."""

    name: str
    args: list[str]
    children: list[_Block] | None = None


def _parse(text: str) -> list[_Block]:
    """The file's directives as a tree; an unbalanced brace ends the parse quietly."""
    root: list[_Block] = []
    stack = [root]
    words: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        quoted = m.group(1) if m.group(1) is not None else m.group(2)  # "" is an argument
        punct, word = m.group(3), m.group(4)
        if word is not None or quoted is not None:
            words.append(word if word is not None else quoted)  # type: ignore[arg-type]
        elif punct == ";" and words:
            stack[-1].append(_Block(words[0], words[1:]))
            words = []
        elif punct == "{":
            block = _Block(words[0] if words else "", words[1:], children=[])
            stack[-1].append(block)
            stack.append(block.children)  # type: ignore[arg-type]
            words = []
        elif punct == "}":
            if len(stack) == 1:
                break
            stack.pop()
            words = []
    return root


def _host(address: str) -> str:
    """*address* without its ``:port``."""
    return _PORT_RE.sub("", address)


def _upstreams(blocks: list[_Block]) -> dict[str, set[str]]:
    """Each ``upstream`` block's name and the hosts its ``server`` lines name."""
    out: dict[str, set[str]] = {}
    for b in blocks:
        if b.children is None:
            continue
        if b.name == "upstream" and b.args:
            out[b.args[0].lower()] = {_host(s.args[0]) for s in b.children if s.name == "server" and s.args}
        else:
            out.update(_upstreams(b.children))
    return out


def _target(address: str, upstreams: dict[str, set[str]], envsubst: bool) -> tuple[str, str]:
    """``(kind, name)`` of the service a ``proxy_pass`` address names; ``(HOST, "")`` when unknown."""
    host = _host(address)
    if address.lower() in upstreams:  # an upstream group is named without a port
        servers = upstreams[address.lower()]
        if len(servers) != 1:
            return HOST, ""  # a pool of several hosts, or none: no one service
        [host] = servers
    if envsubst and (m := _ENVSUBST_RE.match(host)):
        return ENV, m.group(1).lower()
    if _HOSTNAME_RE.match(host):
        return HOST, host.lower()
    return HOST, ""  # an nginx variable or a unix socket: known only at runtime


def _rewrites(blocks: list[_Block]) -> bool:
    """True when these directives rewrite the path: a ``rewrite``, or one under ``if``."""
    return any(
        d.name == "rewrite" or (d.name == "if" and _rewrites(d.children or [])) for d in blocks
    )


def _proxy(location: _Block) -> tuple[_Block | None, bool]:
    """The location's own ``proxy_pass``, and whether a rewrite or ``if`` makes it unreadable."""
    children = location.children or []
    proxy = next((d for d in children if d.name == "proxy_pass" and d.args), None)
    unreadable = _rewrites(children) or any(
        d.name == "if" and any(c.name == "proxy_pass" for c in d.children or []) for d in children
    )
    return proxy, unreadable


def _locations(blocks: list[_Block], rewritten: bool = False) -> Iterator[tuple[_Block, bool]]:
    """Every location, and whether a ``rewrite`` around it changes the path it sees."""
    rewritten = rewritten or _rewrites(blocks)
    for b in blocks:
        if b.children is None:
            continue
        if b.name == "location":
            yield b, rewritten
        if b.name != "upstream":
            yield from _locations(b.children, rewritten)


def _forwarded(path: str, prefix: str, uri: str | None) -> str | None:
    """What a call under *path* is forwarded as: *uri* in place of the prefix, the path
    unchanged without one, None when nginx would splice it oddly.

    nginx appends the rest of the path to the URI as written (``/api/`` to ``/v2``
    forwards ``/v2x``), so only a URI ending as the location does is read.
    """
    if uri is None:
        return prefix
    if path.endswith("/") != uri.endswith("/") or _ODD_URI_RE.search(uri):
        return None
    return uri.rstrip("/").lower()


def _route(
    location: _Block, upstreams: dict[str, set[str]], envsubst: bool, rewritten: bool
) -> tuple[str, str, Route] | None:
    args = location.args
    modifier, path = (args[0], args[1]) if len(args) == 2 else ("", args[0] if args else "")
    if modifier not in ("", "^~", "=") or not path.startswith("/"):
        return None  # a regex or named location: which paths it takes is not read
    prefix, shape = literal_prefix(path)
    if not prefix:
        return None  # a catch-all is never more specific than a real route
    proxy, unreadable = _proxy(location)
    m = _PROXY_RE.match(proxy.args[0]) if proxy is not None else None
    if m is None:
        # Served here, or proxied somewhere unreadable: only blocks shorter prefixes.
        return HOST, "", Route(prefix, None)
    kind, name = _target(m.group(1), upstreams, envsubst)
    readable = not (unreadable or rewritten) and modifier != "=" and shape == EXACT
    return kind, name, Route(prefix, _forwarded(path, prefix, m.group(2)) if readable else None)


def read(file: SourceFile) -> Iterable[Claim]:
    # ``${VAR}`` is an envsubst placeholder only in a template; in a plain
    # config it is an nginx variable, known only at runtime.
    envsubst = file.path.name.lower().endswith((".template", ".tmpl"))
    blocks = _parse(file.text)
    upstreams = _upstreams(blocks)
    for location, rewritten in _locations(blocks):
        if (found := _route(location, upstreams, envsubst, rewritten)) is not None:
            kind, name, route = found
            yield Claim(name=name, kind=kind, route=route)


SOURCE = ServiceNameSource(
    name="nginx",
    filenames=("*.conf", "*.conf.template", "*.conf.tmpl"),
    marker="proxy_pass",
    read=read,
)
