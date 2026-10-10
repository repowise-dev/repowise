"""Remote URLs: parse one into a ``RemoteRef``, key it for lookups, scrub secrets.

Pure string work, no I/O. Accepts every shape git does for a network remote:
``https://``, ``ssh://``, ``git://``, scp-style ``user@host:path`` and a bare
``host/path`` as typed into a form. Local paths and ``file://`` are not remotes
and yield ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import unquote

from .base import ForgeKind, RemoteParts, RemoteRef, quote_segment, split_host_port
from .registry import all_forges, forge_for_host

_URL_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
_SSH_SCHEMES = frozenset({"ssh", "git+ssh", "ssh+git"})
#: Scheme -> the transport it names. Anything else is not a network remote.
_TRANSPORTS = {
    "https": "https",
    "http": "http",
    "git+https": "https",
    "git+http": "http",
    "ssh": "ssh",
    "git+ssh": "ssh",
    "ssh+git": "ssh",
    "git": "git",
}
# git's own rule: a colon before any slash makes ``host:path`` scp-style.
_SCP_RE = re.compile(r"(?:[^@/]+@)?(?P<host>\[[^\]/]+\]|[^:/@\[\]]+):(?P<path>.*)", re.DOTALL)
_QUERY_RE = re.compile(r"[?#]")


def strip_credentials(url: str) -> str:
    """The remote with every secret removed, safe to log or send to a browser.

    A token can sit in the userinfo (``https://user:glpat-x@host/...``,
    ``git+https://ghp_x@host/...``) or the query (``?private_token=``). Every
    scheme loses its query, fragment and userinfo, except that ssh keeps a bare
    username: ``ssh://git@host/...`` and scp-style ``git@host:path`` name an
    account, not a secret. The scheme keeps its case.
    """
    if not url:
        return url
    url = url.strip()
    scheme_match = _URL_SCHEME_RE.match(url)
    if not scheme_match:
        # scp-style `user[:password]@host:path`. Git never sends a password here,
        # but one typed in (even with a `/`) would still reach the browser. A
        # local path (`/srv/a:b@c`, `.\x`, `C:\x`) is not a remote, and without a
        # `host:` after the `@` the `@` is in the path (`host:org/repo@v1.git`).
        userinfo, at, rest = url.partition("@")
        local = "\\" in userinfo or userinfo[:1] in ("/", ".")
        if at and ":" in userinfo and ":" in rest and not local:
            return f"{userinfo.split(':', 1)[0]}@{rest}"
        return url
    scheme = scheme_match.group(1)
    rest = url[scheme_match.end() :]
    userinfo, hostpart = "", rest
    # Userinfo is found before the query is cut, so a `?` or `#` typed into a
    # password cannot split it and leave a prefix behind.
    head = rest.split("/", 1)[0]
    if "@" in head:
        userinfo, _, host = head.rpartition("@")
        hostpart = host + rest[len(head) :]
    elif ":" in head:
        # An unencoded `/` in a password moves the authority past the first
        # `/`, and `user:123/x` even passes for host:port. Such a password
        # always leaves a `:` before that `/`, so only then is a later `@` the
        # end of the userinfo; `host/repo@v1.git` keeps its path. Ceiling:
        # `host:8443/repo@v1.git` loses its host. A port plus a path `@` is
        # rare, and losing a host beats a leak.
        before_query = _QUERY_RE.split(rest, maxsplit=1)[0]
        if "@" in before_query:
            userinfo, _, hostpart = before_query.partition("@")
    hostpart = _QUERY_RE.split(hostpart, maxsplit=1)[0]
    user = userinfo.split(":", 1)[0] if scheme.lower() in _SSH_SCHEMES else ""
    return f"{scheme}://{user + '@' if user else ''}{hostpart}"


def _route_markers() -> frozenset[str]:
    return frozenset().union(*(f.route_markers for f in all_forges()))


def _locate(raw: str) -> tuple[str, str, str] | None:
    """``(transport, authority, path)`` of a scrubbed remote; ``None`` for a non-remote."""
    scheme_match = _URL_SCHEME_RE.match(raw)
    scp = None if scheme_match else _SCP_RE.fullmatch(raw)
    if scp and "@" not in raw.split(":", 1)[0] and scp.group("path").split("/", 1)[0].isdigit():
        scp = None  # `host:8443/g/p` as typed into a form: a port, not a path
    if scheme_match:
        transport = _TRANSPORTS.get(scheme_match.group(1).lower())
        if transport is None:
            return None
        authority, _, path = raw[scheme_match.end() :].partition("/")
    elif scp:
        transport, authority, path = "scp", scp.group("host"), scp.group("path")
    else:
        transport = "https"
        authority, _, path = _QUERY_RE.split(raw, maxsplit=1)[0].partition("/")
    return transport, authority.rpartition("@")[2], path


def _segments(path: str) -> tuple[str, ...]:
    """Unescaped path segments, web routes cut, ``.git`` dropped from the repo.

    A bare ``.git`` leaves an empty repo segment, which no forge parses.
    """
    segments = [unquote(s) for s in path.split("/") if s]
    markers = _route_markers()
    for i, seg in enumerate(segments):
        if seg in markers:
            del segments[i:]
            break
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
    return tuple(segments)


def split_remote(url: str) -> RemoteParts | None:
    """Host, unescaped path segments and web origin; ``None`` for a non-remote."""
    # Scrubbed first, so only an ssh or scp username can still precede the host.
    raw = strip_credentials((url or "").strip())
    if not raw or "\\" in raw:
        return None  # a Windows path, never a URL
    located = _locate(raw)
    hp = split_host_port(located[1]) if located else None
    if located is None or hp is None:
        return None
    transport, _, path = located
    host, port = hp
    shown = f"[{host}]" if ":" in host else host
    if transport in ("http", "https"):
        web_origin = f"{transport}://{shown}{':' + port if port else ''}"
    else:
        web_origin = f"https://{shown}"
    return RemoteParts(transport, host, _segments(path), web_origin)


def parse_remote(url: str, *, hosts: Mapping[str, ForgeKind] | None = None) -> RemoteRef | None:
    """The forge, namespace and repo a remote names, or ``None``.

    *hosts* maps self-managed hosts to their forge and wins over the host-name
    heuristics; ``forge_hosts(repo_root)`` builds it from config and env.
    ``None`` also when the path does not fit the forge's shape (a GitHub URL
    naming only an owner, an Azure URL without ``_git``).
    """
    parts = split_remote(url)
    if parts is None or not all(parts.segments):
        return None
    return forge_for_host(parts.host, hosts).parse(parts)


def canonical_key(url: str, *, hosts: Mapping[str, ForgeKind] | None = None) -> str | None:
    """A stable, lowercased ``host/path`` key: clone and web URLs of one repo match.

    Drops scheme, userinfo, port, query, ``.git``, trailing slashes and web
    routes after GitLab's ``/-/``. The whole key is lowercased because GitHub
    and GitLab resolve repo paths case-insensitively. Azure's three URL shapes
    (``dev.azure.com``, ``ssh.dev.azure.com:v3``, ``*.visualstudio.com``) fold
    to ``dev.azure.com/org/project/_git/repo``. *hosts* names self-managed
    forges as in ``parse_remote``; a host mapped to Bitbucket keys its Data
    Center clone and browse URLs as ``host/PROJ/repo``.
    """
    parts = split_remote(url)
    if parts is None:
        return None
    key = forge_for_host(parts.host, hosts).canonical_path(parts)
    if key is None:
        key = "/".join([parts.host, *(quote_segment(s) for s in parts.segments)])
    return key.rstrip("/").lower()
