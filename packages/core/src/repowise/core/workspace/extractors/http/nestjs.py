"""NestJS HTTP provider dialect: ``@Controller`` classes and their verb decorators.

``@Get(':id')`` names the verb and the sub-path; the ``@Controller('cats')``
above it names the prefix (a string, an array of them, or ``{ path }``).
Where the app serves them comes from its bootstrap: ``app.setGlobalPrefix``
(with its ``exclude`` list) and URI versioning (``app.enableVersioning``,
``@Version``, ``@Controller({ version })``). Those calls sit in ``main.ts`` or
a helper it calls, so they travel through the orchestrator's mount pass keyed
by the app's root: the path above the declaring file's first ``src``
directory, else its directory. A ``NestFactory.create`` file marks a root too,
so an app with no prefix of its own does not inherit one from a root above it.
A controller takes the nearest root above it; one under no root (an Nx
library) takes the setting every app in the repo shares, and is refused when
they differ.

Ceilings: an ``exclude`` list this cannot read (an imported array) excludes
nothing, so those few routes read as prefixed; ``RouterModule`` paths are not
read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..base import line_at
from ..calls import Call, call_sites, decorated_member, file_strings
from ..langs import JS_TS
from ..strings import Arg, on_comment_line, select_argument
from .dialect import build_provider_contract
from .mounts import compose_prefix, declare_mount, declared_mount, declared_names
from .paths import normalize_http_path

if TYPE_CHECKING:
    from collections.abc import Mapping

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings

_NEST_COMMON = ("@nestjs/common",)
_FIRST = Arg(pos=0)
_PATH_KEY = Arg(keys=("path",))
_NEUTRAL = "VERSION_NEUTRAL"

_APP_MOUNT = "nest-app:"
_PREFIX_MOUNT = "nest-prefix:"
_VERSION_MOUNT = "nest-version:"

# `@Sse('events')` serves a GET. `@All`, `@Head`, `@Options` and `@Search`
# name no verb the contract layer records.
_VERBS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Sse": "GET"}

_CONTROLLER = Call(re.compile(r"@Controller\s*\("), JS_TS, _NEST_COMMON)
_ROUTE = Call(re.compile(rf"@(?P<verb>{'|'.join(_VERBS)})\s*\("), JS_TS, _NEST_COMMON)
_VERSION = Call(re.compile(r"@Version\s*\("), JS_TS, _NEST_COMMON)
_GLOBAL_PREFIX = Call(re.compile(r"\.setGlobalPrefix\s*\("), JS_TS, ("setGlobalPrefix",))
_VERSIONING = Call(re.compile(r"\.enableVersioning\s*\("), JS_TS, ("enableVersioning",))
_FACTORY = "NestFactory.create"

# `exclude: ['admin/(.*)']` and `'files/*'` cover what is below a path; Nest
# 11's `'files/{*splat}'` covers the path itself too.
_WILDCARD_TAIL_RE = re.compile(r"/?(?:\(\.\*\)|\*[\w$]*|(?P<splat>\{\*[\w$]*\}))$")


@dataclass(frozen=True)
class _Controller:
    offset: int
    name: str
    paths: list[str] | None  # None: unreadable
    member: int  # offset of the class name, which a class `@Version` decorates
    version: list[str] | None | bool  # False: not given


@dataclass(frozen=True)
class _Route:
    offset: int
    verb: str
    paths: list[str] | None
    handler: str | None
    member: int


def _paths(args: list[str], strings: FileStrings) -> list[str] | None:
    """The paths a decorator's first argument names, ``[""]`` for none; ``None`` if unreadable.

    ``{ path, host, version }`` carries its path under a key; an object with
    no ``path`` serves at the root, as no argument does.
    """
    if not args:
        return [""]
    values, refused = strings.resolve(args[:1], _PATH_KEY if args[0].startswith("{") else _FIRST)
    return None if refused else values or [""]


def _versions(raws: list[str], strings: FileStrings) -> list[str] | None:
    """The versions *raws* name (``""``: version-neutral), or ``None`` if unreadable."""
    out: list[str] = []
    for raw in raws:
        if raw.endswith(_NEUTRAL):
            out.append("")
            continue
        values, refused = strings.resolve([raw], _FIRST)
        if refused or not values:
            return None
        out.extend(values)
    return out


def _app_root(rel_path: str) -> str:
    parts = rel_path.split("/")[:-1]
    if "src" in parts:
        parts = parts[: parts.index("src")]
    return "/".join(parts)


def _root_setting(mounts: Mapping[str, str], root: str) -> tuple[str | None, str | None] | None:
    """The ``(prefix, versioning)`` mount values *root* declares (``None``: not declared).

    ``None`` overall when one it declares cannot be read, or two files disagree.
    """
    out: list[str | None] = []
    for kind in (_PREFIX_MOUNT, _VERSION_MOUNT):
        declared, value = declared_mount(mounts, kind + root)
        if declared and value is None:
            return None
        out.append(value)
    return out[0], out[1]


def _is_root(mounts: Mapping[str, str], root: str) -> bool:
    if _APP_MOUNT + root in mounts:
        return True
    return any(declared_mount(mounts, kind + root)[0] for kind in (_PREFIX_MOUNT, _VERSION_MOUNT))


def _app_settings(mounts: Mapping[str, str], rel_path: str) -> tuple[str | None, str | None] | None:
    """The ``(prefix, versioning)`` of the app serving *rel_path*; ``None`` to refuse it."""
    parts = rel_path.split("/")[:-1]
    for depth in range(len(parts), -1, -1):
        root = "/".join(parts[:depth])
        if _is_root(mounts, root):
            return _root_setting(mounts, root)
    roots = {k[len(_APP_MOUNT) :] for k in mounts if k.startswith(_APP_MOUNT)}
    roots.update(declared_names(mounts, _PREFIX_MOUNT), declared_names(mounts, _VERSION_MOUNT))
    if not roots:
        return None, None
    settings = {_root_setting(mounts, root) for root in roots}
    return settings.pop() if len(settings) == 1 else None


def _excluded(route: str, method: str, exclude: list[list[str]]) -> bool:
    """Whether the global prefix's ``exclude`` list names *route*."""
    norm = normalize_http_path(route)
    for verb, pattern in exclude:
        if verb and verb != method:
            continue
        tail = _WILDCARD_TAIL_RE.search(pattern)
        if tail is None:
            if normalize_http_path(pattern) == norm:
                return True
            continue
        head = normalize_http_path(pattern[: tail.start()])
        below = head == "/" or norm.startswith(head + "/")
        if below or (tail.group("splat") and norm == head):
            return True
    return False


def _global_prefix(args: list[str], strings: FileStrings) -> str | None:
    """``setGlobalPrefix(args)`` as mount JSON: the prefix and its readable excludes."""
    values, refused = strings.resolve(args[:1], _FIRST)
    if refused or len(values) != 1:
        return None
    exclude: list[list[str]] = []
    for raw in select_argument(args[1:], Arg(keys=("exclude",))):
        # `{ path, method: RequestMethod.GET }` or a bare path.
        paths, _ = strings.resolve([raw], _PATH_KEY if raw.startswith("{") else _FIRST)
        method = select_argument([raw], Arg(keys=("method",))) if raw.startswith("{") else []
        verb = method[0].rpartition(".")[2].upper() if method else ""
        exclude.extend([verb if verb != "ALL" else "", p] for p in paths)
    return json.dumps({"prefix": values[0], "exclude": exclude})


def _versioning(args: list[str], strings: FileStrings) -> str | None:
    """``enableVersioning(args)`` as mount JSON when it versions by URI.

    ``""`` when it versions by header or media type, which leaves paths alone;
    ``None`` when what shapes the path cannot be read (options held in a variable).
    """
    if args and not args[0].startswith("{"):
        return None
    kind = select_argument(args, Arg(keys=("type",)))
    if kind and not kind[0].endswith("URI"):
        return ""
    prefix = select_argument(args, Arg(keys=("prefix",)))
    segment: str | None = "v"
    if prefix:
        values, refused = strings.resolve(prefix, _FIRST)
        segment = "" if prefix[0] == "false" else None if refused or not values else values[0]
    default = _versions(select_argument(args, Arg(keys=("defaultVersion",))), strings)
    if segment is None or default is None:
        return None
    return json.dumps({"prefix": segment, "default": default})


def _route_versions(
    route: _Route, owner: _Controller, decorated: dict[int, list[str] | None], default: list[str]
) -> list[str] | None:
    """The versions a route serves: its own ``@Version``, else its controller's, else the default."""
    if route.member in decorated:
        return decorated[route.member]
    if owner.member in decorated:
        return decorated[owner.member]
    return default if owner.version is False else owner.version


class NestDialect:
    name = "nestjs"
    extensions = JS_TS

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        out: dict[str, str] = {}
        root = _app_root(ctx.rel_path)
        if _FACTORY in ctx.content:
            out[_APP_MOUNT + root] = ""
        for shape, args, _m, strings in call_sites(ctx, (_GLOBAL_PREFIX, _VERSIONING), empty=True):
            if shape is _GLOBAL_PREFIX:
                out.update(declare_mount(_PREFIX_MOUNT + root, _global_prefix(args, strings)))
            elif (setting := _versioning(args, strings)) != "":
                out.update(declare_mount(_VERSION_MOUNT + root, setting))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if "@Controller" not in content or _NEST_COMMON[0] not in content:
            return []
        settings = _app_settings(ctx.mounts, ctx.rel_path)
        strings = file_strings(ctx)
        if settings is None or strings is None:
            return []  # the app declares where it serves, and this cannot read it
        prefix = json.loads(settings[0]) if settings[0] is not None else None
        versioning = json.loads(settings[1]) if settings[1] is not None else None

        controllers: list[_Controller] = []
        routes: list[_Route] = []
        decorated: dict[int, list[str] | None] = {}  # member offset -> its `@Version`
        for shape, args, m, _ in call_sites(ctx, (_CONTROLLER, _ROUTE, _VERSION), strings, empty=True):
            member = decorated_member(content, m.start())
            if member is None or on_comment_line(content, m.start()):
                continue
            if shape is _ROUTE:
                routes.append(
                    _Route(
                        m.start(),
                        _VERBS[m.group("verb")],
                        _paths(args, strings),
                        None if member.group("cls") else member.group("name"),
                        member.start("name"),
                    )
                )
            elif shape is _VERSION:
                decorated[member.start("name")] = _versions(select_argument(args, _FIRST), strings)
            elif member.group("cls"):
                version = select_argument(args[:1], Arg(keys=("version",)))
                controllers.append(
                    _Controller(
                        m.start(),
                        member.group("name"),
                        _paths(args, strings),
                        member.start("name"),
                        _versions(version, strings) if version else False,
                    )
                )
        controllers.sort(key=lambda c: c.offset)

        out: list[Contract] = []
        for route in routes:
            owner = next((c for c in reversed(controllers) if c.offset < route.offset), None)
            # No controller above, or a path this cannot read.
            if owner is None or owner.paths is None or route.paths is None:
                continue
            segments = [""]
            if versioning is not None:
                versions = _route_versions(route, owner, decorated, versioning["default"])
                if versions is None:
                    continue
                segments = [versioning["prefix"] + v if v else "" for v in versions] or [""]
            line = line_at(content, route.offset)
            handler = f"{owner.name}.{route.handler}" if route.handler else None
            for base in owner.paths:
                for sub in route.paths:
                    path = compose_prefix(base, sub)
                    head = ""
                    if prefix is not None and not _excluded(path, route.verb, prefix["exclude"]):
                        head = prefix["prefix"]
                    for segment in segments:
                        full = compose_prefix(compose_prefix(head, segment), path)
                        c = build_provider_contract(
                            ctx,
                            method=route.verb,
                            path_raw=full if full.startswith("/") else "/" + full,
                            framework=self.name,
                            line=line,
                            handler=handler,
                        )
                        if c is not None:
                            out.append(c)
        return out
