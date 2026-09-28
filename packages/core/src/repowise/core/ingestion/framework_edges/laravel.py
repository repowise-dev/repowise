"""Laravel convention edges: routes, registrations, discovery, Eloquent.

The files Laravel loads with no importer (route files, config, commands,
migrations, factories, ...) are named in ``framework_facts.LARAVEL`` and
anchored to ``framework:laravel`` here. Everything the framework wires up
*because some file registered it* is an edge from that file instead, so a
listener, policy or provider nothing registers still reads as unused:

* route files to the controllers and middleware (by alias) they name;
* ``$listen`` / ``$subscribe`` / ``$policies`` / ``$commands`` / middleware
  alias arrays, ``Event::listen``, ``Gate::policy``, ``withProviders`` /
  ``withCommands``, scheduled jobs, ``bootstrap/providers.php`` and
  ``config/app.php`` to the classes they name;
* a scheduled or called artisan command, by signature, to its class;
* event discovery: a listener in ``app/Listeners`` whose ``handle`` takes an
  event is linked from that event (or anchored, for a framework event);
* policy discovery: ``Models/Post`` to ``Policies/PostPolicy``;
* service-provider bindings and Eloquent relationships.

Class names resolve the way the import graph resolves them
(:func:`..resolvers.php.resolve_php_class`), so a vendor class never binds to
a local file of the same short name. The ``Route::`` recogniser lives in
``ingestion.framework_routes``, shared with the contract extractor.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ..composer import repo_composer_manifests
from ..framework_facts import LARAVEL
from ..framework_routes import (
    LARAVEL_SCHEDULED_JOB,
    laravel_route_file,
    laravel_routes,
    match_paren,
)
from ..languages.php_same_namespace import (
    PHP_CLASS_NAME,
    file_namespace,
    php_use_aliases,
    qualify_php_name,
)
from ..resolvers import ResolverContext
from ..resolvers.php import resolve_php_class
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_entry_edges,
    anchor_edge,
    source_text,
)

if TYPE_CHECKING:
    import networkx as nx


_NAME = PHP_CLASS_NAME
_CLASS_REF_RE = re.compile(rf"(?<![\w\\$])(?P<name>{_NAME})\s*::\s*class\b")
_NEW_RE = re.compile(rf"\bnew\s+(?P<name>{_NAME})")

_BIND_RE = re.compile(
    rf"->\s*(?:bind|singleton|instance)\s*\(\s*(?P<a>{_NAME})::class\s*,\s*(?P<b>{_NAME})::class"
)
_ELOQUENT_RE = re.compile(
    r"\$this->\s*(?:hasMany|hasOne|belongsTo|belongsToMany|morphMany|morphOne|morphTo)"
    rf"\s*\(\s*(?P<name>{_NAME})::class"
)

# Registration sites whose class arguments the framework instantiates. The
# property arrays live on the event/auth providers and the console/HTTP kernels.
_REGISTRY_RE = re.compile(
    r"\$(?:listen|subscribe|policies|commands|middlewareAliases|routeMiddleware"
    r"|middlewareGroups|middleware|observers)\s*=\s*(?P<open>\[)"
    r"|(?:\bEvent::(?:listen|subscribe)|\bGate::policy|->\s*with(?:Providers|Commands|Events)"
    rf"|{LARAVEL_SCHEDULED_JOB})\s*(?P<call>\()"
)

# Middleware aliases: `'auth' => Authenticate::class` inside `->alias([...])`
# (bootstrap/app.php) or a kernel's alias array.
_ALIAS_SITE_RE = re.compile(
    r"(?:->\s*alias\s*\(|\$(?:middlewareAliases|routeMiddleware)\s*=)\s*(?P<open>\[)"
)
_ALIAS_ENTRY_RE = re.compile(rf"""["'](?P<alias>[\w.-]+)["']\s*=>\s*(?P<name>{_NAME})::class""")
# `->middleware('auth:sanctum')`, `middleware(['a', 'b'])`, `'middleware' => ...`.
_MIDDLEWARE_USE_RE = re.compile(
    r"""(?:\bmiddleware\s*\(|["']middleware["']\s*=>)\s*(?P<value>\[[^\]]*\]|["'][^"']*["'])"""
)
_MIDDLEWARE_NAME_RE = re.compile(r"""["'](?P<name>[\w.-]+)(?::[^"']*)?["']""")

# Artisan commands by signature: the first token of `$signature`, or the
# `#[AsCommand(name: ...)]` attribute; called by the scheduler
# (`Schedule::command('x')`, `$schedule->command('x')`), `Artisan::call` /
# `queue`, and `$this->call` from another command.
_SIGNATURE_RE = re.compile(r"""\$signature\s*=\s*["'](?P<name>[\w:.-]+)""")
_AS_COMMAND_RE = re.compile(r"""#\[AsCommand\(\s*(?:name\s*:\s*)?["'](?P<name>[\w:.-]+)""")
_COMMAND_CALL_RE = re.compile(
    r"(?:(?:->\s*|\bSchedule::)command|\bArtisan::(?:call|queue)|->\s*call(?:Silently|Silent)?)"
    r"""\s*\(\s*["'](?P<name>[\w:.-]+)"""
)

# Event discovery reads the type of a listener method's first parameter; a
# union type names one event per member.
_LISTENER_METHOD_RE = re.compile(
    rf"function\s+(?:handle\w*|__invoke)\s*\(\s*(?P<types>{_NAME}(?:\s*\|\s*{_NAME})*)\s+\$"
)

# Substrings one of which a file must contain for each regex to run: none of
# these patterns has a literal prefix, so the regex engine would otherwise try
# every offset of every PHP file.
_REGISTRY_HINTS = (
    "$listen", "$subscribe", "$policies", "$commands", "$middleware", "$routeMiddleware",
    "$observers", "Event::", "Gate::", "->with", "Schedule::", "->job",
)  # fmt: skip
_COMMAND_CALL_HINTS = ("command", "Artisan::", "call")

# Whole-file registries: every class they name is loaded by the framework.
_REGISTRY_FILES = ("bootstrap/providers.php", "config/app.php")

# Where a legacy string handler (`'Admin\DashboardController@index'`) lives
# when no group names a namespace: the framework's default controller namespace.
_DEFAULT_CONTROLLER_NAMESPACE = "App\\Http\\Controllers\\"


def _manifest_roots(ctx: ResolverContext) -> list[str]:
    """Directories of every first-party manifest that requires the framework."""
    return [m.rel_dir for m in repo_composer_manifests(ctx) if LARAVEL.matches(m)]


def _span(text: str, m: re.Match[str], group: str) -> str:
    start = m.start(group)
    end = match_paren(text, start, hash_comments=True)
    return text[start : end + 1] if end != -1 else ""


class _PhpFile:
    """One PHP file's text and the names its ``use`` clauses and namespace bind."""

    __slots__ = ("aliases", "namespace", "path", "text")

    def __init__(self, path: str, parsed: Any, source_map: dict[str, bytes]) -> None:
        self.path = path
        self.text = source_text(path, parsed, source_map)
        self.namespace = file_namespace(self.text) if self.text else None
        self.aliases = php_use_aliases(parsed)

    def qualify(self, name: str) -> str:
        return qualify_php_name(name, self.namespace, self.aliases)


class _Linker:
    def __init__(self, graph: nx.DiGraph, ctx: ResolverContext) -> None:
        self.graph = graph
        self.ctx = ctx
        self.count = 0

    def file_of(self, f: _PhpFile, name: str) -> str | None:
        return resolve_php_class(f.qualify(name), self.ctx)

    def edge(self, source: str, target: str | None) -> None:
        if target and _add_edge_if_new(self.graph, source, target):
            self.count += 1

    def names(self, f: _PhpFile, names: Iterable[str]) -> None:
        for name in names:
            self.edge(f.path, self.file_of(f, name))

    def refs(self, f: _PhpFile, text: str) -> None:
        """Link *f* to every ``X::class`` and ``new X`` in *text*."""
        self.names(f, (m.group("name") for m in _CLASS_REF_RE.finditer(text)))
        self.names(f, (m.group("name") for m in _NEW_RE.finditer(text)))

    def anchor(self, target: str) -> None:
        if anchor_edge(self.graph, LARAVEL, target):
            self.count += 1


def _registrations(link: _Linker, f: _PhpFile) -> None:
    text = f.text
    if f.path.endswith("ServiceProvider.php"):
        for m in _BIND_RE.finditer(text):
            link.names(f, (m.group("a"), m.group("b")))
    if "$this->" in text:
        link.names(f, (m.group("name") for m in _ELOQUENT_RE.finditer(text)))
    if any(h in text for h in _REGISTRY_HINTS):
        for m in _REGISTRY_RE.finditer(text):
            link.refs(f, _span(text, m, "open" if m.group("open") else "call"))


def _route_handlers(link: _Linker, f: _PhpFile) -> None:
    for route in laravel_routes(f.text):
        handler = route.handler
        if not handler:
            continue  # a closure route names no controller
        target = link.file_of(f, handler)
        if target is None and not handler.startswith("\\"):
            target = resolve_php_class(_DEFAULT_CONTROLLER_NAMESPACE + handler, link.ctx)
        link.edge(f.path, target)


def _aliases(link: _Linker, files: list[_PhpFile]) -> dict[str, str]:
    """``middleware alias -> file`` from every alias registration."""
    out: dict[str, str] = {}
    for f in files:
        if "alias" not in f.text and "iddleware" not in f.text:
            continue
        for site in _ALIAS_SITE_RE.finditer(f.text):
            for m in _ALIAS_ENTRY_RE.finditer(_span(f.text, site, "open")):
                target = link.file_of(f, m.group("name"))
                if target:
                    out.setdefault(m.group("alias"), target)
    return out


def _commands(files: list[_PhpFile]) -> dict[str, str]:
    """``artisan signature -> file`` for every command class."""
    out: dict[str, str] = {}
    for f in files:
        for pattern in (_SIGNATURE_RE, _AS_COMMAND_RE):
            for m in pattern.finditer(f.text):
                out.setdefault(m.group("name"), f.path)
    return out


def _discovered_listeners(link: _Linker, f: _PhpFile) -> None:
    """Link a discovered listener from each event its methods take."""
    for m in _LISTENER_METHOD_RE.finditer(f.text):
        for name in m.group("types").split("|"):
            event = link.file_of(f, name.strip())
            if event:
                link.edge(event, f.path)
            else:
                link.anchor(f.path)  # a framework or package event


def _discovered_policies(link: _Linker, roots: list[str], path_set: set[str]) -> None:
    """``Models/Admin/Post.php`` to ``Policies/Admin/PostPolicy.php``.

    Laravel guesses ``Policies`` beside the model's namespace and under
    ``app``, keeping the model's sub-namespace, and the model may sit in
    ``app/Models`` or directly in ``app``.
    """
    for root in roots:
        app = posixpath.join(root, "app")
        for policies in (f"{app}/Policies/", f"{app}/Models/Policies/"):
            for path in path_set:
                if not (path.startswith(policies) and path.endswith("Policy.php")):
                    continue
                sub = path[len(policies) : -len("Policy.php")]  # `Admin/Post`
                for model in (f"{app}/Models/{sub}.php", f"{app}/{sub}.php"):
                    if model in path_set:
                        link.edge(model, path)
                        break


def _add_laravel_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    roots = _manifest_roots(ctx)
    link = _Linker(graph, ctx)

    # ---- files the framework loads by convention (routes, config, ...) ----
    # Only where a manifest requires the framework: ``config/*.php`` alone
    # says nothing about Laravel.
    entry = sum(add_entry_edges(graph, LARAVEL, root, path_set) for root in roots)

    source_map = getattr(ctx, "source_map", None) or {}
    files = [
        f
        for path, parsed in parsed_files.items()
        if parsed.file_info.language == "php"
        and (f := _PhpFile(path, parsed, source_map)).text
    ]
    aliases = _aliases(link, files)
    commands = _commands(files)

    listener_dirs = tuple(posixpath.join(root, "app/Listeners/") for root in roots)
    registry_files = {posixpath.join(root, name) for root in roots for name in _REGISTRY_FILES}

    for f in files:
        if laravel_route_file(f.path):
            _route_handlers(link, f)
        if aliases and "iddleware" in f.text:
            for use in _MIDDLEWARE_USE_RE.finditer(f.text):
                for m in _MIDDLEWARE_NAME_RE.finditer(use.group("value")):
                    link.edge(f.path, aliases.get(m.group("name")))
        if f.path in registry_files:
            link.refs(f, f.text)
        _registrations(link, f)
        if commands and any(h in f.text for h in _COMMAND_CALL_HINTS):
            for m in _COMMAND_CALL_RE.finditer(f.text):
                link.edge(f.path, commands.get(m.group("name")))
        if listener_dirs and f.path.startswith(listener_dirs):
            _discovered_listeners(link, f)

    _discovered_policies(link, roots, path_set)
    return entry + link.count


class _LaravelHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return (
            "laravel" in dctx.stack_lower
            or "routes/web.php" in dctx.path_set
            or "routes/api.php" in dctx.path_set
            or bool(_manifest_roots(dctx.ctx))
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_laravel_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_LaravelHandler()]
