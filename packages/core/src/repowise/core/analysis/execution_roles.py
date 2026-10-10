"""What kind of run reaches a function: a request, a job, startup, a CLI, a test.

Performance ranking needs "is this on a hot path", which name-derived entry
files cannot answer: a CLI ``main`` and a web app reach the same code. Here
each role starts from its own seeds, read off what ingestion already put in
the graph (decorators, framework edges, imports of a scheduler or CLI library,
route files), and spreads with one breadth-first walk over the reliable
execution edges. A function reached by several roles takes the hottest.

Seeds cover Python and TypeScript/JavaScript only. Anything no seed reaches is
``unknown``, which is an answer ("no evidence"), never "not reachable".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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
    "tooling",
    "test",
    "unknown",
]
EXECUTION_ROLES: tuple[str, ...] = get_args(ExecutionRole)
"""Every role, hottest first. The order is the precedence when roles meet."""

LEADING_ROLES = frozenset({"request", "event_consumer"})
"""Roles whose work repeats per request or per message."""

COLD_ROLES = frozenset({"startup", "cli", "tooling", "test"})
"""Roles that run once per process or per command: their loops are not served."""

_PROPAGATED: tuple[ExecutionRole, ...] = (
    "request",
    "event_consumer",
    "scheduled_job",
    "startup",
    "cli",
)
_SEEDED_LANGUAGES = frozenset({"python", "typescript", "javascript"})
_TS_JS = frozenset({"typescript", "javascript"})
_CALLABLE_KINDS = frozenset({"function", "method"})

# Route decorators the Flask recogniser does not read: FastAPI's other verbs and
# agent tool registrations (``@mcp.tool``), which run once per request.
_PY_REQUEST_DECORATOR = re.compile(
    r"^@\w+(?:\.\w+)*\.(?:api_route|websocket|head|options|tool)\s*\("
)
_TS_REQUEST_DECORATOR = re.compile(r"^@(?:Get|Post|Put|Patch|Delete|All|MessagePattern)\s*\(")
_STARTUP_DECORATOR = re.compile(
    r"^@\w+(?:\.\w+)*\.(?:on_event\s*\(\s*['\"](?:startup|shutdown)|on_startup|before_serving)"
)
_SCHEDULED_DECORATOR = re.compile(
    r"^@(?:\w+\.)*(?:task|shared_task|periodic_task|scheduled_job|cron)\b"
)
_CLI_DECORATOR = re.compile(r"^@(?:\w+\.)*(?:command|group)\s*\(")
_CONSUMER_NAME = re.compile(r"(?i)^_?(?:handle|on)_?\w*?(?:inbound|webhook)\w*$")
_POLLER_NAME = re.compile(r"(?i)(?:^_?poll|poll(?:er|ing)?(?:_?loop)?$|ticker$|watcher$|heartbeat$)")
_HANDLER_NAME = re.compile(r"(?i)^_?handle_?")
# A background job's runner: ``execute_job``, ``_run_index_job``, ``process_jobs``.
_JOB_RUNNER_NAME = re.compile(r"(?i)^_?(?:run|execute|process)_?\w*?_?jobs?$")
_ROUTE_REGISTRAR_NAME = re.compile(
    r"(?i)^(?:register|mount|setup|install|attach)\w*(?:routes?|router|endpoints?)$"
)

# Import roots that make every function in the importing file a seed.
_SCHEDULER_IMPORTS = frozenset(
    {"apscheduler", "celery", "schedule", "croniter", "aiocron", "crontab", "node-cron", "cron",
     "node-schedule"}
)
_CLI_IMPORTS = frozenset({"commander", "yargs", "cac", "@oclif", "clipanion", "citty"})
_EXPRESS_IMPORTS = frozenset({"express", "@nestjs"})
_LAMBDA_CONFIG_SUFFIXES = (".yml", ".yaml", ".json", ".tf")


def _import_root(target: str) -> str | None:
    """``external:apscheduler.schedulers`` -> ``apscheduler``; ``@nestjs/core`` -> ``@nestjs``."""
    if not target.startswith("external:"):
        return None
    name = target[len("external:") :]
    return name.split("/", 1)[0].split(".", 1)[0]


@dataclass(slots=True)
class _Scan:
    """What one pass over the graph's nodes and edges says about each file."""

    symbols: dict[str, list[tuple[str, Mapping[str, Any]]]]
    imports: dict[str, set[str]]
    url_targets: set[str]
    binds: list[tuple[str, str]]
    references: list[tuple[str, str]]


def _scan(graph: Any) -> _Scan:
    scan = _Scan({}, {}, set(), [], [])
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


def _symbol_role(path: str, attrs: Mapping[str, Any], imports: set[str]) -> ExecutionRole | None:
    """The role one function seeds from its own declaration and its file."""
    language = attrs.get("language") or ""
    name = attrs.get("name") or ""
    role = _decorator_role(attrs.get("decorators") or (), language)
    if role is not None:
        return role
    if language in _TS_JS and (
        next_app_router_file(path)
        or "/pages/api/" in f"/{path}"
        or _ROUTE_REGISTRAR_NAME.match(name)
    ):
        return "request"
    # aiohttp registers ``self._handle_x`` with ``router.add_get``: a mention, never a call.
    if "aiohttp" in imports and _HANDLER_NAME.match(name):
        return "request"
    return _name_role(name, imports)


def _name_role(name: str, imports: set[str]) -> ExecutionRole | None:
    """The role a function's name, or a library its file imports, implies."""
    if _CONSUMER_NAME.match(name):
        return "event_consumer"
    if imports & _SCHEDULER_IMPORTS or _POLLER_NAME.search(name) or _JOB_RUNNER_NAME.match(name):
        return "scheduled_job"
    if name == "lifespan":
        return "startup"
    if imports & _CLI_IMPORTS or (name == "main" and "argparse" in imports):
        return "cli"
    return None


def seed_roles(graph: Any) -> dict[str, set[str]]:
    """Seed symbol ids per propagated role, from one scan of *graph*."""
    scan = _scan(graph)
    seeds: dict[str, set[str]] = {role: set() for role in _PROPAGATED}
    callables: dict[str, Mapping[str, Any]] = {}
    for path, symbols in scan.symbols.items():
        imports = scan.imports.get(path, set())
        for node, attrs in symbols:
            callables[node] = attrs
            if attrs.get("language") not in _SEEDED_LANGUAGES:
                continue
            role = "request" if path in scan.url_targets else _symbol_role(path, attrs, imports)
            if role is not None:
                seeds[role].add(node)
    for source, target in scan.binds:
        attrs = callables.get(target)
        if attrs is None:
            continue
        source_file = file_of_symbol(source)
        if source_file.endswith(_LAMBDA_CONFIG_SUFFIXES):
            seeds["event_consumer"].add(target)
        elif attrs.get("language") in _TS_JS and scan.imports.get(source_file, set()) & _EXPRESS_IMPORTS:
            seeds["request"].add(target)
    for source, target in scan.references:
        # ``set_defaults(func=cmd)``: argparse names its commands, never calls them.
        if target in callables and "argparse" in scan.imports.get(file_of_symbol(source), set()):
            seeds["cli"].add(target)
    return seeds


@dataclass(frozen=True, slots=True)
class ExecutionRoles:
    """The hottest role reaching each function, ready to look up."""

    reached: Mapping[str, ExecutionRole]

    @classmethod
    def build(cls, graph: Any, index: ExecutionGraphIndex) -> ExecutionRoles:
        """Seed once, then one forward walk per role, hottest role first.

        Another role's seed is where that role begins, so a walk stops there:
        a request that hands work to a job runner does not run the job's loops.
        """
        seeds = seed_roles(graph)
        seeded = set().union(*seeds.values())
        reached: dict[str, ExecutionRole] = {}
        for role in _PROPAGATED:
            if seeds[role]:
                stop_at = seeded - seeds[role]
                for node in index.forward_reachable(seeds[role], stop_at=stop_at):
                    reached.setdefault(node, role)
        return cls(reached)

    def role_of(self, symbol: str | None, file_path: str) -> ExecutionRole:
        """The role of the function *symbol* in *file_path*.

        A test or tooling file is that role whatever reaches it: a loop in a
        test is a test's cost.
        """
        if is_test_path(file_path):
            return "test"
        if path_origin(file_path) in ("tooling", "build"):
            return "tooling"
        return self.reached.get(symbol, "unknown") if symbol else "unknown"


_GROUP_ORDER: tuple[ExecutionRole, ...] = (
    "request",
    "event_consumer",
    "scheduled_job",
    "unknown",
    "startup",
    "cli",
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
    "hottest_role",
    "seed_roles",
]
