"""What kind of run reaches a function: a request, a job, startup, a CLI, a test.

Performance ranking needs "is this on a hot path", which name-derived entry
files cannot answer: a CLI ``main`` and a web app reach the same code. Here
each role starts from its own seeds, read off what ingestion already put in
the graph (decorators, framework edges, route files, registrations), and the
hot roles spread with one breadth-first walk each over the reliable execution
edges. A function reached by several roles takes the hottest.

Startup and CLI never spread: a helper they call is cold only when every one
of its callers is. Seeds cover Python and TypeScript/JavaScript only. Anything
no seed reaches is ``unknown``, which is an answer ("no evidence"), never
"not reachable" and never cold.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal, get_args

from repowise.core.code_origin import path_origin
from repowise.core.ingestion.framework_routes import flask_routes, next_app_router_file
from repowise.core.test_paths import is_test_path

from .execution_graph import ExecutionGraphIndex, file_of_symbol

ExecutionRole = Literal[
    "request",
    "event_consumer",
    "scheduled_job",
    "startup",
    "cli",
    "ui",
    "tooling",
    "test",
    "unknown",
]
EXECUTION_ROLES: tuple[str, ...] = get_args(ExecutionRole)
"""Every role, hottest first. The order is the precedence when roles meet."""

LEADING_ROLES = frozenset({"request", "event_consumer"})
"""Roles whose work repeats per request or per message."""

COLD_ROLES = frozenset({"startup", "cli", "ui", "tooling", "test"})
"""Roles whose loops are not served: once per process or command, or on the
user's own machine (a browser, desktop or terminal UI)."""

_HOT: tuple[ExecutionRole, ...] = ("request", "event_consumer", "scheduled_job")
_SEEDED: tuple[ExecutionRole, ...] = (*_HOT, "startup", "cli")
_SEEDED_LANGUAGES = frozenset({"python", "typescript", "javascript"})
_TS_JS = frozenset({"typescript", "javascript"})
_CALLABLE_KINDS = frozenset({"function", "method"})

# Decorators no shared recogniser reads. ``flask_routes`` already reads the verb
# decorators FastAPI spells the same way (``@router.get("/x")``); the workspace
# HTTP dialects match whole files for route paths and sit in a layer analysis
# does not import. ``execution_flows``' name tiers score "looks like an entry"
# over every ``handle_``/``get_`` and are deliberately not reused as seeds.
_PY_REQUEST_DECORATOR = re.compile(
    r"^@\w+(?:\.\w+)*\.(?:api_route|websocket|head|options|tool)\s*\("
    r"|^@(?:\w+\.)*method\s*\(\s*['\"]"  # JSON-RPC ``@method("session.list")``
)
_TS_REQUEST_DECORATOR = re.compile(r"^@(?:Get|Post|Put|Patch|Delete|All|Sse|MessagePattern)\s*\(")
_STARTUP_DECORATOR = re.compile(
    r"^@\w+(?:\.\w+)*\.(?:on_event\s*\(\s*['\"](?:startup|shutdown)|on_startup|before_serving)"
)
_SCHEDULED_DECORATOR = re.compile(
    r"^@(?:\w+\.)*(?:task|shared_task|periodic_task|scheduled_job|cron)\b"
)
_CLI_DECORATOR = re.compile(r"^@(?:\w+\.)*(?:command|group)\s*\(")
_ROUTE_REGISTRAR_NAME = re.compile(
    r"(?i)^(?:register|mount|setup|install|attach)\w*(?:routes?|router|endpoints?)$"
)
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

# Imports under which a function named but not called is registered (``add_job(fn)``,
# ``.action(fn)``), or which corroborate a name. No import seeds a whole file.
_SCHEDULER_IMPORTS = frozenset(
    {
        "apscheduler",
        "celery",
        "schedule",
        "croniter",
        "aiocron",
        "crontab",
        "node-cron",
        "cron",
    }
)
# ``vscode``: ``registerCommand(id, fn)`` names a command a person runs.
_CLI_IMPORTS = frozenset({"commander", "yargs", "cac", "@oclif", "clipanion", "citty", "vscode"})
_EXPRESS_IMPORTS = frozenset({"express", "@nestjs"})
_LAMBDA_CONFIG_SUFFIXES = (".yml", ".yaml", ".json", ".tf")

# Name-derived seeds, each kept only with a corroborating place in the path.
_CONSUMER_TAILS = frozenset({"inbound", "webhook", "webhooks"})
_CONSUMER_PLACES = frozenset(
    {
        "gateway",
        "gateways",
        "inbound",
        "webhook",
        "webhooks",
        "channels",
        "consumers",
        "listeners",
        "adapters",
        "platforms",
        "monitor",
    }
)
_BACKGROUND_TAILS = frozenset({"poller", "watcher", "ticker", "heartbeat", "sweeper", "reaper"})
_BACKGROUND_PLACES = frozenset(
    {
        "worker",
        "workers",
        "jobs",
        "job",
        "scheduler",
        "cron",
        "daemon",
        "background",
        "tasks",
        "gateway",
        "watchers",
        "executor",
        "queue",
    }
)
_ROUTE_PLACES = frozenset({"routes", "route", "router", "routers", "server", "api", "http"})
# Client code by file: component files, and JS/TS under a UI, renderer or TUI tree.
_JS_EXTS = frozenset({"ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "cts", "vue", "svelte"})
_COMPONENT_EXTS = frozenset({"tsx", "jsx", "vue", "svelte"})
_UI_PLACES = frozenset(
    {
        "ui",
        "renderer",
        "components",
        "component",
        "views",
        "hooks",
        "desktop",
        "tui",
        "ink",
        "frontend",
        "webview",
    }
)


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


@cache
def _place_words(path: str) -> frozenset[str]:
    """Every word of *path*'s directories and file stem."""
    return frozenset(_words(path.rsplit(".", 1)[0]))


@cache
def _path_role(path: str) -> ExecutionRole | None:
    """``test``, ``tooling`` or ``ui`` when the file alone decides the role.

    A Next.js app-router file renders on the server per request, so it is
    never ``ui`` here.
    """
    if is_test_path(path):
        return "test"
    if path_origin(path) in ("tooling", "build"):
        return "tooling"
    ext = path.rsplit(".", 1)[-1].lower()
    if ext not in _JS_EXTS or next_app_router_file(path):
        return None
    return "ui" if ext in _COMPONENT_EXTS or _place_words(path) & _UI_PLACES else None


def _import_root(target: str) -> str | None:
    """``external:apscheduler.schedulers`` -> ``apscheduler``; ``@nestjs/core`` -> ``@nestjs``."""
    if not target.startswith("external:"):
        return None
    name = target[len("external:") :]
    return name.split("/", 1)[0].split(".", 1)[0]


@dataclass(slots=True)
class _Scan:
    """What one pass over the graph's nodes and edges says about each file."""

    symbols: dict[str, list[tuple[str, Mapping[str, Any]]]] = field(default_factory=dict)
    imports: dict[str, set[str]] = field(default_factory=dict)
    url_targets: set[str] = field(default_factory=set)
    binds: list[tuple[str, str]] = field(default_factory=list)
    references: list[tuple[str, str]] = field(default_factory=list)
    referenced: set[str] = field(default_factory=set)


def _scan(graph: Any) -> _Scan:
    scan = _Scan()
    for node, attrs in graph.nodes(data=True):
        if attrs.get("node_type") == "symbol" and attrs.get("kind") in _CALLABLE_KINDS:
            path = attrs.get("file_path") or file_of_symbol(node)
            scan.symbols.setdefault(path, []).append((node, attrs))
    for source, target, data in graph.edges(data=True):
        edge_type = (data or {}).get("edge_type")
        if edge_type == "imports":
            root = _import_root(target)
            if root is not None:
                scan.imports.setdefault(source, set()).add(root)
        elif edge_type == "framework" and source.endswith("urls.py"):
            scan.url_targets.add(target)
        elif edge_type == "framework_binds":
            scan.binds.append((source, target))
        elif edge_type == "references":
            scan.references.append((source, target))
            scan.referenced.add(target)
    return scan


def _decorator_role(decorators: Iterable[str], language: str) -> ExecutionRole | None:
    for text in decorators:
        if language == "python" and (any(flask_routes(text)) or _PY_REQUEST_DECORATOR.match(text)):
            return "request"
        if language in _TS_JS and _TS_REQUEST_DECORATOR.match(text):
            return "request"
        if _STARTUP_DECORATOR.match(text):
            return "startup"
        if _SCHEDULED_DECORATOR.match(text):
            return "scheduled_job"
        if language == "python" and _CLI_DECORATOR.match(text):
            return "cli"
    return None


def _strong_role(path: str, attrs: Mapping[str, Any], imports: set[str]) -> ExecutionRole | None:
    """The role a function's declaration or its file's framework convention states."""
    language = attrs.get("language") or ""
    role = _decorator_role(attrs.get("decorators") or (), language)
    if role is not None:
        return role
    if language in _TS_JS and (next_app_router_file(path) or "/pages/api/" in f"/{path}"):
        return "request"
    if attrs.get("name") == "main" and "argparse" in imports:
        return "cli"
    return None


def _named_role(
    path: str, attrs: Mapping[str, Any], imports: set[str], referenced: bool
) -> ExecutionRole | None:
    """The role a function's name implies, kept only when its file or a
    registration corroborates it; otherwise no evidence, so no seed."""
    name = attrs.get("name") or ""
    words = _words(name)
    if not words:
        return None
    places = _place_words(path)
    if name == "lifespan" or (name == "activate" and "vscode" in imports):
        return "startup"
    if _request_named(name, words, attrs, imports, places):
        return "request"
    if words[0] in ("handle", "on") and words[-1] in _CONSUMER_TAILS:
        return "event_consumer" if referenced or places & _CONSUMER_PLACES else None
    return "scheduled_job" if _background_named(words, imports, places, referenced) else None


def _background_named(
    words: list[str], imports: set[str], places: frozenset[str], referenced: bool
) -> bool:
    scheduler = bool(imports & _SCHEDULER_IMPORTS)
    if _background_shaped(words) and (referenced or places & _BACKGROUND_PLACES or scheduler):
        return True
    # ``setup_scheduler`` registering closures with ``add_job``: closures are no
    # symbols, so their loops belong to the function that builds the scheduler.
    return words[-1] == "scheduler" and scheduler


def _request_named(
    name: str,
    words: list[str],
    attrs: Mapping[str, Any],
    imports: set[str],
    places: frozenset[str],
) -> bool:
    # aiohttp registers ``self._handle_x`` with ``router.add_get``: a mention, never a call.
    if "aiohttp" in imports and words[0] == "handle":
        return True
    return bool(
        attrs.get("language") in _TS_JS
        and _ROUTE_REGISTRAR_NAME.match(name)
        and places & _ROUTE_PLACES
    )


def _background_shaped(words: list[str]) -> bool:
    """A poller, watcher or job runner by whole words: ``poll_once``,
    ``_notification_poller_loop``, ``execute_job``; never ``polling_config``."""
    if words[-1] in _BACKGROUND_TAILS:
        return True
    if words[-1] == "loop" and len(words) > 1 and words[-2] in {*_BACKGROUND_TAILS, "poll"}:
        return True
    if words[0] == "poll" and len(words) > 1:
        return True
    return words[0] in ("run", "execute", "process") and words[-1] in ("job", "jobs")


@dataclass(slots=True)
class Seeds:
    """Seed symbol ids per role, and the ones a declaration or framework states."""

    by_role: dict[str, set[str]]
    strong: set[str]

    def add(self, role: str, node: str, *, strong: bool) -> None:
        self.by_role[role].add(node)
        if strong:
            self.strong.add(node)


def _seed_symbols(scan: _Scan, seeds: Seeds) -> dict[str, Mapping[str, Any]]:
    callables: dict[str, Mapping[str, Any]] = {}
    for path, symbols in scan.symbols.items():
        imports = scan.imports.get(path, set())
        for node, attrs in symbols:
            callables[node] = attrs
            if attrs.get("language") not in _SEEDED_LANGUAGES:
                continue
            role = "request" if path in scan.url_targets else _strong_role(path, attrs, imports)
            if role is not None:
                seeds.add(role, node, strong=True)
                continue
            role = _named_role(path, attrs, imports, node in scan.referenced)
            if role is not None:
                seeds.add(role, node, strong=False)
    return callables


def _seed_binds(scan: _Scan, callables: Mapping[str, Mapping[str, Any]], seeds: Seeds) -> None:
    for source, target in scan.binds:
        attrs = callables.get(target)
        if attrs is None:
            continue
        source_file = file_of_symbol(source)
        if source_file.endswith(_LAMBDA_CONFIG_SUFFIXES):
            seeds.add("event_consumer", target, strong=True)
            continue
        express = scan.imports.get(source_file, set()) & _EXPRESS_IMPORTS
        if attrs.get("language") in _TS_JS and express:
            seeds.add("request", target, strong=True)


def _seed_references(scan: _Scan, callables: Mapping[str, Any], seeds: Seeds) -> None:
    """Functions a file registers by name: ``add_job(fn)`` for a scheduler,
    ``set_defaults(func=cmd)`` or ``.action(cmd)`` for a CLI. Named, never
    called, so only a reference shows it."""
    for source, target in scan.references:
        if target not in callables:
            continue
        imports = scan.imports.get(file_of_symbol(source), set())
        if imports & _SCHEDULER_IMPORTS:
            seeds.add("scheduled_job", target, strong=True)
        elif "argparse" in imports or imports & _CLI_IMPORTS:
            seeds.add("cli", target, strong=True)


def seed_roles(graph: Any) -> Seeds:
    """Seed symbol ids per role, from one scan of *graph*."""
    scan = _scan(graph)
    seeds = Seeds({role: set() for role in _SEEDED}, set())
    callables = _seed_symbols(scan, seeds)
    _seed_binds(scan, callables, seeds)
    _seed_references(scan, callables, seeds)
    return seeds


def _settle_cold(index: ExecutionGraphIndex, reached: dict[str, ExecutionRole]) -> None:
    """Mark cold every function whose known callers are all cold.

    A caller in a test or tooling file counts as cold. One caller nothing
    classified keeps the function ``unknown``: that caller is no evidence.
    """
    remaining: dict[str, int] = {}
    queue = deque(node for node, role in reached.items() if role in COLD_ROLES)
    while queue:
        node = queue.popleft()
        for target in index.forward.get(node, ()):
            if target in reached:
                continue
            if target not in remaining:
                remaining[target] = sum(
                    1
                    for caller in index.reverse.get(target, ())
                    if _path_role(file_of_symbol(caller)) is None
                )
            remaining[target] -= 1
            if remaining[target] <= 0:
                reached[target] = reached[node]
                queue.append(target)


@dataclass(frozen=True, slots=True)
class ExecutionRoles:
    """The hottest role reaching each function, ready to look up.

    *index* is the graph the roles were walked over, kept so a stored finding
    that predates ``role_owner`` can still be keyed on its loop owner.
    """

    reached: Mapping[str, ExecutionRole]
    index: ExecutionGraphIndex | None = field(default=None, compare=False, repr=False)

    @classmethod
    def build(cls, graph: Any, index: ExecutionGraphIndex) -> ExecutionRoles:
        """Seed once, walk each hot role, then settle what only cold roles call.

        A hot walk stops at another role's declared seed (a route handler, a
        registered job): that is where the other role's work begins. A seed
        named only by convention does not stop it, because calling a function
        runs it whatever it is called.
        """
        seeds = seed_roles(graph)
        reached: dict[str, ExecutionRole] = {}
        for role in _HOT:
            if seeds.by_role[role]:
                stop_at = seeds.strong - seeds.by_role[role]
                for node in index.forward_reachable(seeds.by_role[role], stop_at=stop_at):
                    reached.setdefault(node, role)
        for role in ("startup", "cli"):
            for node in seeds.by_role[role]:
                reached.setdefault(node, role)
        _settle_cold(index, reached)
        return cls(reached, index)

    def owner_of(self, file_path: str, line: int | None, details: Mapping[str, Any]) -> str | None:
        """The function that owns a performance finding's loop.

        The path's first node when the cost crosses functions, else the
        symbol holding the finding's line.
        """
        path = details.get("path")
        if isinstance(path, list) and path:
            return path[0]
        return self.index.resolve_function(file_path, line or 0) if self.index else None

    def role_of(self, symbol: str | None, file_path: str) -> ExecutionRole:
        """The role of the function *symbol* in *file_path*.

        A test or tooling file is that role whatever reaches it: a loop in a
        test is a test's cost.
        """
        by_path = _path_role(file_path)
        if by_path is not None:
            return by_path
        return self.reached.get(symbol, "unknown") if symbol else "unknown"


_GROUP_ORDER: tuple[ExecutionRole, ...] = (
    "request",
    "event_consumer",
    "scheduled_job",
    "unknown",
    "startup",
    "cli",
    "ui",
    "tooling",
    "test",
)


def hottest_role(roles: Iterable[str | None]) -> ExecutionRole:
    """The role of a group of functions: one request member makes it a request,
    and one member nothing reached keeps it from reading as cold."""
    present = {role or "unknown" for role in roles}
    return next((role for role in _GROUP_ORDER if role in present), "unknown")


__all__ = [
    "COLD_ROLES",
    "EXECUTION_ROLES",
    "LEADING_ROLES",
    "ExecutionRole",
    "ExecutionRoles",
    "Seeds",
    "hottest_role",
    "seed_roles",
]
