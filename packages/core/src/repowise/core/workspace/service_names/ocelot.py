"""Ocelot routes: which path an Ocelot gateway forwards to which service.

Each entry of ``Routes`` (``ReRoutes`` before Ocelot 16) forwards calls matching
its ``UpstreamPathTemplate`` to ``DownstreamPathTemplate`` on the service its
``ServiceName`` (service discovery) or ``DownstreamHostAndPorts`` hosts name.
``/api/catalog/{everything}`` forwarded as ``/{everything}`` strips the prefix,
and any pair ending in the same variables rewrites it
(:func:`.base.template_upstream`). The gateway is the repo holding the file.

A route the reader cannot rewrite through is still stated, with no forwarded
path, so it keeps a shorter prefix from claiming its calls: a template whose
variables differ or sit before a literal (``/api/{version}/c/{everything}``),
a fully literal template (it serves one path, not a prefix), an
``UpstreamHost`` or ``UpstreamHeaderTemplates`` rule, or a
``DownstreamHttpMethod`` that changes the verb. ``UpstreamHttpMethod`` is not
read: a routed call links only to a provider that serves its own verb. A
catch-all ``/{everything}`` is skipped, and so are ``Aggregates`` and
``DynamicRoutes``.
"""

from __future__ import annotations

from collections.abc import Iterable

from .base import Claim, Route, ServiceNameSource, SourceFile, literal_prefix, template_upstream
from .json_config import field, load_json_config

#: Rules that narrow which calls take a route, or change the verb it forwards.
_UNREAD = ("upstreamhost", "upstreamheadertemplates", "downstreamhttpmethod")


def _target(route: dict) -> str:
    """The service a route forwards to: its ``ServiceName``, else its one downstream host."""
    service = field(route, "servicename")
    if isinstance(service, str) and service.strip():
        return service.strip().lower()
    entries = field(route, "downstreamhostandports")
    hosts = {
        str(field(h, "host") or "").strip().lower()
        for h in (entries if isinstance(entries, list) else [])
        if isinstance(h, dict)
    }
    return hosts.pop() if len(hosts) == 1 else ""  # several hosts are no one service


def read(file: SourceFile) -> Iterable[Claim]:
    config = load_json_config(file.text)
    routes = field(config, "routes") or field(config, "reroutes")
    for route in routes if isinstance(routes, list) else ():
        upstream, downstream = field(route, "upstreampathtemplate"), field(route, "downstreampathtemplate")
        if not isinstance(upstream, str) or not isinstance(downstream, str):
            continue
        prefix, _ = literal_prefix(upstream)
        if not prefix:
            continue  # a catch-all is never more specific than a real route
        readable = not any(field(route, k) for k in _UNREAD)
        forwarded = template_upstream(upstream, downstream) if readable else None
        yield Claim(name=_target(route), route=Route(prefix, forwarded))


SOURCE = ServiceNameSource(
    name="ocelot",
    # ``ocelot.json``, its environment and merge files, and the older ``configuration.json``.
    filenames=("ocelot*.json", "configuration.json"),
    marker="UpstreamPathTemplate",
    read=read,
)
