"""Spring Cloud Gateway and Netflix Zuul routes: which path prefix a gateway forwards where.

Read from YAML or ``.properties``, flattened to Spring's dotted keys so both
spellings go through one reader:

- ``spring.cloud.gateway[.server.webflux|.server.webmvc|.mvc].routes[i]``: the
  ``uri`` (``lb://name`` or ``http://host:port``) names the service, a ``Path``
  predicate the prefix, and ``StripPrefix``, ``PrefixPath`` and the
  ``RewritePath=/x/?(?<s>.*), /y/${s}`` shape what is forwarded;
- ``zuul.routes.<id>``: ``path``, then ``serviceId``, ``url`` or the route id,
  with ``stripPrefix`` (default true) and the global ``zuul.prefix``.

The gateway is the repo holding an ``application`` or ``bootstrap`` file that
sits in its module's own resources or ``config/`` directory, or for any other
file (a config server's ``gateway.yml``) the app its name spells, a profile
suffix dropped.

A route the reader cannot rewrite through (a fully literal or partly
wildcarded ``Path``, a Host, Method or Header predicate, ``SetPath``, another
``RewritePath``, a ``forward:`` target) is still stated, with no forwarded
path, so it keeps a shorter prefix from claiming its calls. A catch-all
``/**`` is skipped. A config-server file is read only when its name holds
``gateway``, ``zuul``, ``proxy`` or ``edge``; widening that gate is one glob.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from .base import PREFIX, Claim, Route, ServiceNameSource, SourceFile, host_of, literal_prefix
from .yaml_docs import load_yaml_docs

_SCG_RE = re.compile(
    r"^spring\.cloud\.gateway(?:\.server\.webflux|\.server\.webmvc|\.mvc)?\.routes\[(\d+)\]\.(.+)$"
)
_ZUUL_RE = re.compile(r"^zuul\.routes\.([^.\[]+)(?:\.([^.]+))?$")
_PROPERTY_RE = re.compile(r"^\s*([^#!\s=:][^=:\s]*)\s*[=:]\s*(.*?)\s*$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^(predicates|filters)\[(\d+)\](?:\.(.+))?$")
_REWRITE_RE = re.compile(r"^(/[^()*?+\\$^\[]*?)/?\??\(\?<(\w+)>\.\*\)$")
_REPLACEMENT_RE = re.compile(r"^(/?[^$]*?)/?\$\\?\{(\w+)\}$")
_PATH_FILTERS = frozenset({"setpath", "rewritepath"})


def _flatten(node: Any, key: str, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(v, f"{key}.{k}" if key else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _flatten(v, f"{key}[{i}]", out)
    elif node is not None and key:
        out[key] = str(node)


def _flat(file: SourceFile) -> list[dict[str, str]]:
    if file.path.suffix.lower() == ".properties":
        return [dict(_PROPERTY_RE.findall(file.text))]
    docs: list[dict[str, str]] = []
    for doc in load_yaml_docs(file.text):
        flat: dict[str, str] = {}
        _flatten(doc, "", flat)
        docs.append(flat)
    return docs


def _key(name: str) -> str:
    """Relaxed binding: ``serviceId``, ``service-id`` and ``service_id`` are one key."""
    return name.replace("-", "").replace("_", "").lower()


def _items(fields: dict[str, str], kind: str) -> list[tuple[str, list[str]]]:
    """``(name, args)`` of each predicate or filter, shortcut (``Path=/a/**``) or long form."""
    grouped: dict[int, dict[str, str]] = defaultdict(dict)
    for sub, value in fields.items():
        m = _LIST_ITEM_RE.match(sub)
        if m and m.group(1) == kind:
            grouped[int(m.group(2))][m.group(3) or ""] = value
    items: list[tuple[str, list[str]]] = []
    for _, parts in sorted(grouped.items()):
        if "" in parts:
            name, _, args = parts[""].partition("=")
            items.append((name.strip(), [a.strip() for a in args.split(",") if a.strip()]))
        else:
            args = [v for k, v in sorted(parts.items()) if k.startswith("args.")]
            items.append((parts.get("name", "").strip(), args))
    return items


def _rewrite(current: str, regex: str, replacement: str) -> str | None:
    """``current`` under ``RewritePath=/x/?(?<s>.*), /y/${s}``, or None for any other shape."""
    m, r = _REWRITE_RE.match(regex.strip()), _REPLACEMENT_RE.match(replacement.strip())
    if m is None or r is None or m.group(2) != r.group(2):
        return None
    head = m.group(1).rstrip("/").lower()
    if current != head and not current.startswith(head + "/"):
        return None
    return r.group(1).rstrip("/").lower() + current[len(head) :]


def _forwarded(prefix: str, filters: list[tuple[str, list[str]]]) -> str | None:
    """What *prefix* is forwarded as after *filters*, or None when a filter cannot be read."""
    current = prefix
    for name, args in filters:
        key = _key(name)
        if key == "stripprefix":
            n = int(args[0]) if args and args[0].isdigit() else 1
            segments = [s for s in current.split("/") if s]
            if n > len(segments):
                return None  # strips into the wildcard part: the rest is unknown
            current = "/" + "/".join(segments[n:]) if segments[n:] else ""
        elif key == "prefixpath" and args:
            current = args[0].rstrip("/").lower() + current
        elif key == "rewritepath":
            if len(args) != 2 or (current := _rewrite(current, *args)) is None:  # type: ignore[assignment]
                return None
        elif key in _PATH_FILTERS:
            return None
    return current


def _service(uri: str) -> str | None:
    uri = uri.strip()
    if uri.lower().startswith("lb:"):
        return uri.split("://", 1)[-1].split("/")[0].split(":")[0].lower() or None
    return host_of("uri", uri) if "://" in uri else None


def _route(pattern: str, forwarded: Callable[[str], str | None], readable: bool = True) -> Route | None:
    """The route a path pattern makes: a rewrite for a readable prefix route, else a blocker."""
    prefix, kind = literal_prefix(pattern)
    if not prefix:
        return None  # a catch-all is never more specific than a real route
    upstream = forwarded(prefix) if kind == PREFIX and readable else None
    return Route(prefix, upstream)


def _gateway_routes(flat: dict[str, str]) -> Iterator[tuple[str, Route]]:
    routes: dict[str, dict[str, str]] = defaultdict(dict)
    for key, value in flat.items():
        if m := _SCG_RE.match(key):
            routes[m.group(1)][m.group(2)] = value
    for fields in routes.values():
        # A forward:/ or no://op route serves locally: it only blocks shorter prefixes.
        service = _service(fields.get("uri", "")) or ""
        filters = _items(fields, "filters")
        predicates = _items(fields, "predicates")
        # A Host, Method or Header predicate narrows which calls take the route.
        readable = all(_key(name) == "path" for name, _ in predicates)
        for name, args in predicates:
            if _key(name) != "path":
                continue
            for pattern in args:
                route = _route(pattern, lambda p, f=filters: _forwarded(p, f), readable)
                if route is not None:
                    yield service, route


def _zuul_routes(flat: dict[str, str]) -> Iterator[tuple[str, Route]]:
    routes: dict[str, dict[str, str]] = defaultdict(dict)
    settings: dict[str, str] = {}
    for key, value in flat.items():
        if m := _ZUUL_RE.match(key):
            routes[m.group(1)][_key(m.group(2) or "path")] = value
        elif key.startswith("zuul."):
            settings[_key(key)] = value
    global_prefix = literal_prefix(settings.get("zuul.prefix", ""))[0]
    global_strip = settings.get("zuul.stripprefix", "true").lower() != "false"
    for route_id, fields in routes.items():
        if "url" in fields and "serviceid" not in fields:
            service = _service(fields["url"]) or ""  # forward:/local only blocks
        else:
            service = fields.get("serviceid") or route_id
        strip = fields.get("stripprefix", "true").lower() != "false"
        route = _route(
            fields.get("path", f"/{route_id}/**"),
            lambda p, strip=strip: ("" if global_strip else global_prefix) + ("" if strip else p),
        )
        if route is not None:
            yield service.lower(), Route(global_prefix + route.prefix, route.upstream)


#: Spring profile suffixes a config server's ``<app>-<profile>.yml`` may carry.
_PROFILES = frozenset(
    {"default", "dev", "development", "local", "test", "qa", "stage", "staging", "uat",
     "prod", "production", "docker", "k8s", "kubernetes", "cloud", "native"}
)
#: Where a module's own ``application`` file sits: ``src/main/resources``, its
#: ``config/`` child, or a ``config/`` beside the jar. Anywhere else (a config
#: server's ``shared/`` tree) it configures every app, not this repo's gateway.
_OWN_CONFIG_DIRS = frozenset({"resources", "config"})


def _gateway_names(path: Path) -> tuple[str, ...] | None:
    """The app a config file configures: ``()`` for its own module's file, its name
    otherwise (a profile suffix dropped), or None when the file is not a gateway's."""
    stem = path.stem.lower()
    if stem.startswith(("application", "bootstrap")):
        return () if path.parent.name.lower() in _OWN_CONFIG_DIRS else None
    app, _, profile = stem.rpartition("-")
    return (stem, app) if app and profile in _PROFILES else (stem,)


def read(file: SourceFile) -> Iterable[Claim]:
    gateway = _gateway_names(file.path)
    if gateway is None:
        return
    for flat in _flat(file):
        for service, route in (*_gateway_routes(flat), *_zuul_routes(flat)):
            yield Claim(name=service, route=Route(route.prefix, route.upstream, gateway))


SOURCE = ServiceNameSource(
    name="spring_gateway",
    filenames=tuple(
        f"{stem}.{ext}"
        for stem in ("application*", "bootstrap*", "*gateway*", "*zuul*", "*proxy*", "*edge*")
        for ext in ("yml", "yaml", "properties")
    ),
    marker="routes",
    read=read,
)
