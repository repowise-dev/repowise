"""YARP ``ReverseProxy`` config: which path a YARP gateway forwards to which service.

Each route under ``ReverseProxy:Routes`` sends calls matching ``Match:Path``
(``/api/catalog/{**catch-all}``) to its ``ClusterId``; the cluster's
``Destinations`` addresses name the service (``http://catalog-api``, or Aspire's
``https+http://catalog-api``) and any base path the forwarded path is appended
to. ``PathRemovePrefix``, ``PathPrefix`` and a ``PathPattern`` ending in the
route's own variables (:func:`.base.template_upstream`) shape what is
forwarded. The gateway is the repo holding the file.

A route the reader cannot rewrite through is still stated, with no forwarded
path, so it keeps a shorter prefix from claiming its calls: a ``Path`` not
ending in a catch-all (``{id}`` matches one segment, not a prefix), a ``Hosts``, ``Headers`` or ``QueryParameters``
match, ``PathSet`` or another path transform, a ``PathRemovePrefix`` reaching
into the wildcard part, or destinations with different base paths. A cluster
whose destinations name several hosts has no one target. ``Methods`` is not
read: a routed call links only to a provider that serves its own verb. A
catch-all ``{**catch-all}`` is skipped. Ceiling: routes built in code
(``LoadFromMemory``, Aspire ``AddYarp(...).WithConfiguration``) are not read.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import urlsplit

from .base import (
    PREFIX,
    Claim,
    Route,
    ServiceNameSource,
    SourceFile,
    host_of,
    literal_prefix,
    template_upstream,
)
from .json_config import field, load_json_config

#: Match rules that narrow which calls take a route.
_NARROWING = ("hosts", "headers", "queryparameters")


def _values(node: Any) -> list[Any]:
    """A config section's entries: a dict's values, or a list (older YARP configs)."""
    if isinstance(node, dict):
        return list(node.values())
    return node if isinstance(node, list) else []


def _path(value: Any) -> str:
    return "/" + str(value).strip().strip("/").lower() if str(value).strip("/ ") else ""


def base_path(address: str) -> str | None:
    """The path a destination address carries (``""`` for none), None when it does not parse."""
    try:
        return _path(urlsplit(address).path)
    except ValueError:
        return None


def catch_all(pattern: str) -> bool:
    """True when a YARP pattern ends in a catch-all (``{**rest}``, ``{*rest}``): only that
    matches every path under its prefix, while ``{id}`` matches one segment."""
    segments = [s for s in pattern.strip().split("/") if s]
    return bool(segments) and segments[-1].startswith("{*")


def _destination(cluster: Any) -> tuple[str, str | None]:
    """The one host a cluster's destinations name (``""`` if none or several), and their
    one base path (None when they differ)."""
    addresses = [str(field(d, "address") or "") for d in _values(field(cluster, "destinations"))]
    hosts = {host_of("address", a) or "" for a in addresses}
    bases = {base_path(a) for a in addresses}
    host = hosts.pop() if len(hosts) == 1 else ""
    return host, (bases.pop() if len(bases) == 1 else None)


def _forwarded(pattern: str, prefix: str, transforms: Any) -> str | None:
    """What *prefix* is forwarded as after the route's path transforms, or None."""
    current = prefix
    for transform in _values(transforms):
        for key, value in (transform.items() if isinstance(transform, dict) else ()):
            key = str(key).lower()
            if key == "pathremoveprefix":
                strip = _path(value)
                if strip.startswith(current + "/"):
                    return None  # removes part of the wildcard: the rest is unknown
                if current == strip or current.startswith(strip + "/"):
                    current = current[len(strip) :]
            elif key == "pathprefix":
                current = _path(value) + current
            elif key == "pathpattern":
                if (current := template_upstream(pattern, str(value))) is None:  # type: ignore[assignment]
                    return None
            elif key.startswith("path"):
                return None  # PathSet, or a path transform not read
    return current


def _routes(proxy: Any) -> Iterator[Claim]:
    section = field(proxy, "clusters")
    clusters = {str(k).lower(): v for k, v in section.items()} if isinstance(section, dict) else {}
    for route in _values(field(proxy, "routes")):
        match = field(route, "match")
        pattern = field(match, "path")
        if not isinstance(pattern, str):
            continue
        prefix, kind = literal_prefix(pattern)
        if not prefix:
            continue  # a catch-all is never more specific than a real route
        host, base = _destination(clusters.get(str(field(route, "clusterid") or "").lower()))
        readable = (
            kind == PREFIX
            and catch_all(pattern)
            and base is not None
            and not any(field(match, k) for k in _NARROWING)
        )
        forwarded = _forwarded(pattern, prefix, field(route, "transforms")) if readable else None
        upstream = None if forwarded is None else (base or "") + forwarded
        yield Claim(name=host, route=Route(prefix, upstream))


def read(file: SourceFile) -> Iterable[Claim]:
    yield from _routes(field(load_json_config(file.text), "reverseproxy"))


SOURCE = ServiceNameSource(
    name="yarp",
    filenames=("appsettings*.json", "yarp*.json"),
    marker="ReverseProxy",
    read=read,
)
