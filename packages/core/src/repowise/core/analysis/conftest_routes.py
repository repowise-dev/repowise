"""Which tests under a ``conftest.py`` a change reaching it through its imports can break.

pytest loads a conftest for every test at or below its directory, so the graph
links each of those tests to it, and a change anywhere in the conftest's import
closure used to select all of them. On a suite whose root conftest imports a
few hub modules, that is the whole suite for most changes.

A conftest contributes two things to a test, and a change that reaches it only
through its imports can break a test through either:

1. **Import time.** The conftest's imports run when pytest collects any test
   under it. A change that breaks them (a syntax error, a failing module body)
   breaks collection for every one of those tests, so running any one of them
   detects it. The selection adds one such test per conftest when none is
   selected already, chosen as the first by path (an import check). Autouse
   fixtures run for that test too, so a patch an autouse fixture applies to a
   module on the route (``monkeypatch.setattr(mod, "x", ...)``) that stops
   working fails there as well.
2. **What the conftest does with the module.** A fixture, a hook or
   module-level code that names something from a module on the route, in any
   way but as the ``setattr`` target in an autouse fixture or hook,
   can change what a test sees: calling it, reading a constant, comparing,
   iterating or formatting it all do. An autouse fixture, a ``pytest_*`` hook,
   module-level code (class bodies included) and a fixture or hook defined in
   a class keep every test. A fixture a test asks for affects the tests that
   ask for it, directly, through other fixtures, through ``usefixtures`` or by
   ``getfixturevalue``; the index records each of those requests as a fixture
   edge, and those tests are selected.

Granularity is the module, not the function: a fixture that names anything in
a module on the route to the change counts as reaching it, however deep the
change is. A binding the conftest never reads (``import app.models  #
registers them``) is imported for its side effects, which every test sees, so
it keeps every test.

Fails closed, keeping every test under the conftest with the reason, when the
conftest does not parse, loads plugins, star-imports a module on the route, has
a route the analysis cannot tie to one of its imports, when a test or conftest
asks for its fixtures in a way the index cannot record
(``pytest_edges.UNRECORDED_HINT``), when some file imports a conftest by a
name the index could not resolve, or when the index predates fixture-request
edges.

Ceiling: an import-time side effect of the changed module itself (it registers
itself in a shared registry) that a test observes without any import route of
its own to that module is seen only through the import check. Upgrade path: a
per-test map built from a coverage run, which records what each test executed.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ingestion.framework_edges.pytest_edges import (
    CONFTEST_HINT,
    FIXTURE_HINT,
    UNRECORDED_HINT,
    fixture_declaration,
)
from ..ingestion.languages.python_modules import module_parts
from .test_reachability import _in_clause
from .test_selection import is_runnable_test, scope_kind

# The call whose first argument is the object an attribute is patched on:
# naming a module there in an autouse fixture is caught by the import check.
# Probing (`hasattr`) or removing (`delattr`) depends on the module, so counts.
_TARGET_CALLS = frozenset({"setattr"})
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class ConftestUse:
    """What a conftest does with the modules on a change's route.

    *run_all* says why every test under it stays selected; otherwise
    *fixtures* are the requested fixtures (function names) that reach the
    change, and *declared* every module-level fixture it defines.
    """

    run_all: str | None = None
    fixtures: frozenset[str] = frozenset()
    declared: frozenset[str] = frozenset()


@dataclass
class _Unit:
    """A module-level function or class of the conftest, as parsed."""

    node: ast.AST
    params: list[str] = field(default_factory=list)
    fixture: str | None = None
    autouse: bool = False
    # A class holding a fixture or a hook, which pytest may run for any test.
    holds_hooks: bool = False
    refs: set[str] = field(default_factory=set)
    loads: list[ast.Name] = field(default_factory=list)
    imports: list[ast.Import | ast.ImportFrom] = field(default_factory=list)


@dataclass
class _Reach:
    """How one unit uses the modules on one route."""

    run: str | None = None
    patch: str | None = None
    imports: str | None = None

    def uses(self) -> str | None:
        return self.run or self.patch or self.imports


def _names(module: tuple[str, ...], entry: tuple[str, ...], *, package: bool) -> bool:
    """Whether *module* names *entry*: its tail, or with *package* a package above it."""
    n = len(module)
    return n > 0 and any(
        entry[k : k + n] == module and (package or k + n == len(entry))
        for k in range(len(entry) - n + 1)
    )


def _bindings(
    node: ast.Import | ast.ImportFrom, here: tuple[str, ...], entries: list[tuple[str, ...]]
) -> tuple[dict[str, tuple[str, ...]], set[tuple[str, ...]], bool]:
    """``({bound name: entry}, entries matched, star-imports an entry)`` for one import."""
    bound: dict[str, tuple[str, ...]] = {}
    matched: set[tuple[str, ...]] = set()
    star = False

    def hit(module: tuple[str, ...], *, package: bool) -> tuple[str, ...] | None:
        return next((e for e in entries if _names(module, e, package=package)), None)

    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = tuple(alias.name.split("."))
            prefixes = [parts[:i] for i in range(1, len(parts))]
            e = hit(parts, package=True) or next(
                (h for p in prefixes if (h := hit(p, package=False))), None
            )
            if e:
                bound[alias.asname or parts[0]] = e
                matched.add(e)
        return bound, matched, star

    base = here[: len(here) - (node.level - 1)] if node.level else ()
    module = base + tuple((node.module or "").split(".")) if node.module else base
    for alias in node.names:
        if alias.name == "*":
            e = hit(module, package=True)
            star = star or e is not None
            continue
        e = hit((*module, alias.name), package=True) or hit(module, package=False)
        if e:
            bound[alias.asname or alias.name] = e
            matched.add(e)
    return bound, matched, star


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    out: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


def _patch_target(name: ast.Name, parents: Mapping[int, ast.AST]) -> bool:
    """Whether the name is the first argument of an attribute patch or probe call."""
    par = parents.get(id(name))
    if not (isinstance(par, ast.Call) and par.args and par.args[0] is name):
        return False
    func = par.func
    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return called in _TARGET_CALLS


def _fixture_info(node: ast.AST) -> tuple[str | None, bool]:
    """``(registered name, autouse)`` for a fixture definition, ``(None, False)`` otherwise."""
    decorators = [f"@{ast.unparse(d)}" for d in getattr(node, "decorator_list", ())]
    declared = fixture_declaration(getattr(node, "name", ""), decorators)
    return declared if declared else (None, False)


def _import_time(stmt: ast.stmt) -> list[ast.AST]:
    """What a definition evaluates when the module is imported: decorators, defaults, bases."""
    out: list[ast.AST] = list(stmt.decorator_list)
    if isinstance(stmt, ast.ClassDef):
        out += [*stmt.bases, *(k.value for k in stmt.keywords)]
        # A class body runs at import; only its methods' bodies wait for a call.
        for inner in stmt.body:
            out += _import_time(inner) if isinstance(inner, _DEFS) else [inner]
    else:
        out += [d for d in [*stmt.args.defaults, *stmt.args.kw_defaults] if d]
    return out


def _module_level(body: list[ast.stmt]) -> tuple[list[ast.AST], list[ast.AST], list[ast.AST]]:
    """``(imports, definitions, import-time code)`` at module level, through compound statements."""
    imports: list[ast.AST] = []
    defs: list[ast.AST] = []
    code: list[ast.AST] = []
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imports.append(stmt)
        elif isinstance(stmt, _DEFS):
            defs.append(stmt)
            code.extend(_import_time(stmt))
        elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            # The block's own expressions run at import time; its statements
            # are module level like any other.
            code.extend(_header_expressions(stmt))
            nested = [
                *getattr(stmt, "body", []),
                *getattr(stmt, "orelse", []),
                *getattr(stmt, "finalbody", []),
                *(s for h in getattr(stmt, "handlers", []) for s in h.body),
            ]
            sub = _module_level(nested)
            imports += sub[0]
            defs += sub[1]
            code += sub[2]
        else:
            code.append(stmt)
    return imports, defs, code


def _header_expressions(stmt: ast.stmt) -> list[ast.AST]:
    """The expressions a compound statement evaluates outside its body."""
    if isinstance(stmt, (ast.If, ast.While)):
        return [stmt.test]
    if isinstance(stmt, ast.For):
        return [stmt.iter]
    if isinstance(stmt, ast.With):
        return [item.context_expr for item in stmt.items]
    return [h.type for h in getattr(stmt, "handlers", []) if h.type is not None]


def _body_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node inside a definition, its import-time decorators and defaults excluded."""
    skip = {id(d) for d in getattr(node, "decorator_list", ())}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        skip |= {id(d) for d in [*node.args.defaults, *node.args.kw_defaults] if d}
    stack = [c for c in ast.iter_child_nodes(node) if id(c) not in skip]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(ast.iter_child_nodes(cur))


def _params(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    plain = positional[: len(positional) - len(args.defaults)]
    keyword = [a for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is None]
    return [a.arg for a in plain + keyword]


def _holds_hooks(node: ast.AST) -> bool:
    return isinstance(node, ast.ClassDef) and any(
        isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (m.name.startswith("pytest_") or _fixture_info(m)[0] is not None)
        for m in node.body
    )


@dataclass
class ConftestFacts:
    """A conftest's parse, made once and asked about any number of routes (:meth:`use`)."""

    path: str
    error: str | None = None
    here: tuple[str, ...] = ()
    parents: dict[int, ast.AST] = field(default_factory=dict)
    imports: list[Any] = field(default_factory=list)
    units: dict[str, _Unit] = field(default_factory=dict)
    code_loads: list[ast.Name] = field(default_factory=list)
    exported: set[str] = field(default_factory=set)

    @classmethod
    def parse(cls, source: str, path: str) -> ConftestFacts:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return cls(path, error=f"{path} does not parse")
        facts = cls(path, here=module_parts(path)[:-1], parents=_parents(tree))
        imports, defs, code = _module_level(tree.body)
        facts.imports = imports
        for node in defs:
            unit = _Unit(node, params=_params(node), holds_hooks=_holds_hooks(node))
            unit.fixture, unit.autouse = _fixture_info(node)
            facts.units[node.name] = unit
        for name, unit in facts.units.items():
            for sub in _body_nodes(unit.node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    unit.imports.append(sub)
                elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    unit.loads.append(sub)
                    if sub.id in facts.units and sub.id != name:
                        unit.refs.add(sub.id)
        for node in code:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    facts.code_loads.append(sub)
                if isinstance(sub, ast.Name) and sub.id == "pytest_plugins":
                    facts.error = f"{path} loads pytest plugins"
                if isinstance(sub, ast.Name) and sub.id == "__all__":
                    # Re-exported for the tests that import the conftest, which
                    # the selection adds as its importers.
                    value = getattr(facts.parents.get(id(sub)), "value", None)
                    facts.exported |= {
                        e.value
                        for e in getattr(value, "elts", ())
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    }
        return facts

    def use(self, entries: Collection[str]) -> ConftestUse:
        """What the conftest does with *entries*, the files it imports on a route."""
        if self.error:
            return ConftestUse(run_all=self.error)
        targets = [module_parts(e) for e in entries]
        hot: dict[str, tuple[str, ...]] = {}
        matched: set[tuple[str, ...]] = set()
        for node in self.imports:
            bound, hits, star = _bindings(node, self.here, targets)
            if star:
                return ConftestUse(run_all=f"{self.path} star-imports a module on the route")
            hot.update(bound)
            matched |= hits

        reach: dict[str, _Reach] = {}
        loaded = set(self.exported)
        for name, unit in self.units.items():
            r = reach[name] = _Reach()
            local: dict[str, tuple[str, ...]] = {}
            for node in unit.imports:
                bound, hits, star = _bindings(node, self.here, targets)
                if star:
                    return ConftestUse(run_all=f"{self.path} star-imports a module on the route")
                local.update(bound)
                matched |= hits
            r.imports = next(iter(local), None)
            seen = {n.id for n in unit.loads}
            loaded |= seen
            for load in unit.loads:
                if load.id not in local and load.id not in hot:
                    continue
                if _patch_target(load, self.parents):
                    r.patch = r.patch or load.id
                else:
                    r.run = r.run or load.id
            # A local import nothing reads is there for its side effects.
            if r.run is None and (unread := sorted(set(local) - seen)):
                r.run = unread[0]

        if missed := [e for e in targets if e not in matched]:
            return ConftestUse(
                run_all=f"{self.path} reaches {'/'.join(missed[0])} by a route none of its "
                "imports names"
            )

        module_refs: set[str] = set()
        for load in self.code_loads:
            loaded.add(load.id)
            if load.id in hot and not _patch_target(load, self.parents):
                return ConftestUse(run_all=f"module-level code in {self.path} uses {load.id}")
            if load.id in self.units:
                module_refs.add(load.id)
        if unread := sorted(set(hot) - loaded):
            return ConftestUse(
                run_all=f"{self.path} imports {unread[0]} without using it, so for its side effects"
            )
        self._propagate(reach)
        return self._decide(reach, module_refs)

    def _propagate(self, reach: dict[str, _Reach]) -> None:
        """A unit uses what the units it names and the fixtures it requests use."""
        by_fixture = {u.fixture: n for n, u in self.units.items() if u.fixture}
        changed = True
        while changed:
            changed = False
            for name, unit in self.units.items():
                deps = [*unit.refs]
                if unit.fixture:
                    deps += [by_fixture[p] for p in unit.params if p in by_fixture]
                mine = reach[name]
                for dep in deps:
                    other = reach[dep]
                    # Calling a helper that patches a module runs that patch.
                    other_run = other.run or other.patch
                    if other_run and not mine.run:
                        mine.run, changed = other_run, True
                    if other.imports and not mine.imports:
                        mine.imports, changed = other.imports, True

    def _decide(self, reach: Mapping[str, _Reach], module_refs: set[str]) -> ConftestUse:
        fixtures: set[str] = set()
        for name, unit in self.units.items():
            r = reach[name]
            if name in module_refs and r.run:
                return ConftestUse(run_all=f"module-level code in {self.path} calls {name}")
            if unit.holds_hooks and r.uses():
                return ConftestUse(
                    run_all=f"{self.path}'s class {name} defines fixtures or hooks and uses "
                    f"{r.uses()}"
                )
            if r.run and (unit.autouse or name.startswith("pytest_")):
                kind = "autouse fixture" if unit.autouse else "hook"
                return ConftestUse(run_all=f"{self.path}'s {kind} {name} uses {r.run}")
            # A requested fixture reaches its users however it names the module;
            # a helper reaches tests through the units that call it or through
            # the tests importing the conftest, which the selection adds.
            if unit.fixture and not unit.autouse and r.uses():
                fixtures.add(name)
        return ConftestUse(
            fixtures=frozenset(fixtures),
            declared=frozenset(n for n, u in self.units.items() if u.fixture),
        )


def conftest_use(source: str, path: str, entries: Collection[str]) -> ConftestUse:
    """What the conftest at *path* (text *source*) does with *entries*. Pure."""
    return ConftestFacts.parse(source, path).use(entries)


@dataclass(frozen=True)
class _Edge:
    source: str
    target: str
    edge_type: str
    hint: str | None


async def _edges_into_scopes(
    session: AsyncSession, repo_id: str, targets: list[str]
) -> list[_Edge]:
    """Edges arriving at the conftests and their fixtures, and unresolved conftest imports.

    One query. The second part is a range on the indexed target column: an
    import of ``conftest`` the resolver could not place lands on an
    ``external:`` node, and the importer it hides must not be dropped.
    """
    if not targets:
        return []
    params: dict[str, Any] = {"repo_id": repo_id}
    tgt = _in_clause("p", targets, params)
    rows = await session.execute(
        text(
            "SELECT DISTINCT source_node_id, target_node_id, edge_type, hint_source "
            "FROM graph_edges WHERE repository_id = :repo_id AND ("
            f"(target_node_id IN ({tgt}) "
            "AND edge_type IN ('framework', 'framework_binds', 'calls')) "
            "OR (target_node_id >= 'external:' AND target_node_id < 'external;' "
            "AND target_node_id LIKE '%conftest' AND edge_type = 'imports'))"
        ),
        params,
    )
    return [_Edge(*row) for row in rows]


async def _records_fixture_requests(session: AsyncSession, repo_id: str) -> bool:
    """Whether the index was built by a version that stamps fixture requests."""
    row = await session.execute(
        text(
            "SELECT 1 FROM graph_edges WHERE repository_id = :repo_id "
            "AND edge_type = 'framework_binds' AND hint_source = :hint LIMIT 1"
        ),
        {"repo_id": repo_id, "hint": FIXTURE_HINT},
    )
    return row.first() is not None


def with_importers(
    picks: dict[str, str], parents: Mapping[str, Collection[str]], blocked: Collection[str] = ()
) -> list[tuple[str, str]]:
    """*picks* plus every test importing one of them, transitively, as ``import-graph``.

    A *blocked* file is never added: a conftest the change reaches only through
    its imports stands for the tests :func:`scoped_candidates` chose instead.
    """
    stack = list(picks)
    while stack:
        fresh = [
            p for p in sorted(parents.get(stack.pop(), ())) if p not in picks and p not in blocked
        ]
        picks.update(dict.fromkeys(fresh, "import-graph"))
        stack.extend(fresh)
    return list(picks.items())


@dataclass
class _Route:
    """One conftest on one target's routes."""

    entries: frozenset[str]
    # Found by a walk with no import route at all (a call).
    direct: bool
    use: ConftestUse | None = None
    reason: str | None = None


async def scoped_candidates(
    session: AsyncSession,
    repo_id: str,
    found: Mapping[str, dict[str, str]],
    entries: Mapping[str, Mapping[str, Collection[str]]],
    parents: Mapping[str, Collection[str]],
    parent_entries: Mapping[str, Mapping[str, Collection[str]]],
    test_files: Collection[str],
    read: Callable[[str], str | None],
    *,
    plugin_loader: str | None = None,
) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """Each target's ``[(test file, via)]`` with conftest routes narrowed, and the notes.

    *found* are the walks' picks per target and *entries* the files each was
    reached through; *parents* the test files importing each test file and
    *parent_entries* the files each importer reached it through; *read* returns
    a conftest's text. Every conftest on a route that this
    module cannot narrow keeps today's answer: every test under it.
    """
    routes: dict[str, dict[str, _Route]] = {}
    for target, picks in found.items():
        full = dict(picks)
        with_importers(full, parents)
        for conf in sorted(t for t in full if scope_kind(t) == "conftest" and t != target):
            via = set(entries.get(target, {}).get(conf, ()))
            for h in full:
                if h != conf and conf in parents.get(h, ()):
                    via |= set(parent_entries.get(h, {}).get(conf, ()))
            # A parent conftest is not an import: pytest links a nested
            # conftest to it, and that parent's own decision covers the route.
            own = frozenset(v for v in via if scope_kind(v) != "conftest")
            routes.setdefault(target, {})[conf] = _Route(own, conf in picks and not via)

    # One parse per conftest; each distinct route through it is matched against it.
    facts: dict[str, ConftestFacts | None] = {}
    uses: dict[tuple[str, frozenset[str]], ConftestUse] = {}
    for by_conf in routes.values():
        for conf, route in by_conf.items():
            if conf not in facts:
                source = read(conf)
                facts[conf] = None if source is None else ConftestFacts.parse(source, conf)
            parsed = facts[conf]
            if plugin_loader:
                route.reason = f"{plugin_loader} loads pytest plugins by name"
            elif not route.entries:
                route.reason = f"{conf} is reached by a call alone" if route.direct else None
            elif parsed is None:
                route.reason = f"{conf} could not be read"
            else:
                key = (conf, route.entries)
                if key not in uses:
                    uses[key] = parsed.use(route.entries)
                route.use = uses[key]
                route.reason = route.use.run_all

    narrowable = {
        conf
        for by_conf in routes.values()
        for conf, r in by_conf.items()
        if r.use is not None and r.reason is None
    }
    declared = {
        f"{conf}::{name}"
        for by_conf in routes.values()
        for conf, r in by_conf.items()
        if conf in narrowable and r.use is not None
        for name in r.use.declared
    }
    edges = await _edges_into_scopes(session, repo_id, sorted(narrowable | declared))
    stamped = {e.target for e in edges if e.hint == FIXTURE_HINT}
    unstamped = any(
        r.use is not None
        and r.use.fixtures
        and not any(f"{conf}::{n}" in stamped for n in r.use.declared)
        for by_conf in routes.values()
        for conf, r in by_conf.items()
        if conf in narrowable
    )
    # Asked only when a conftest's fixtures show no recorded request at all.
    recorded = await _records_fixture_requests(session, repo_id) if unstamped else True
    scope = _Scope(edges, parents, test_files, recorded)

    out: dict[str, list[tuple[str, str]]] = {}
    notes: dict[tuple[str, str], list[str]] = {}
    for target, picks in found.items():
        out[target] = _narrow(target, dict(picks), routes.get(target, {}), scope, notes)
    return out, [_note(key, targets) for key, targets in notes.items()]


def _note(key: tuple[str, str], targets: list[str]) -> str:
    """One line per conftest outcome, however many changed files share it."""
    who = targets[0] if len(targets) == 1 else f"{len(targets)} changed files (e.g. {targets[0]})"
    return f"{key[0]}{who}{key[1]}."


@dataclass(frozen=True)
class _Scope:
    """What every target's narrowing reads: the fetched edges and the test-file facts."""

    edges: list[_Edge]
    parents: Mapping[str, Collection[str]]
    test_files: Collection[str]
    # The index stamps fixture requests (see :func:`_records_fixture_requests`).
    recorded: bool

    def unresolved_conftest_import(self) -> str | None:
        return next((e.source for e in self.edges if e.target.startswith("external:")), None)


def _narrow(
    target: str,
    picks: dict[str, str],
    routes: Mapping[str, _Route],
    scope: _Scope,
    notes: dict[tuple[str, str], list[str]],
) -> list[tuple[str, str]]:
    """One target's picks, each narrowable conftest replaced by the tests it can break."""
    blocked: set[str] = set()
    added: dict[str, str] = {}
    narrowed: list[str] = []
    hidden_importer = scope.unresolved_conftest_import()
    for conf, route in routes.items():
        reason = route.reason
        if reason is None and route.use is not None and hidden_importer:
            reason = (
                f"{hidden_importer} imports a conftest by a name the index could not "
                "resolve, so that importer is not known"
            )
        users: set[str] = set()
        if reason is None and route.use is not None:
            found = _fixture_users(conf, route.use, scope)
            reason, users = (found, set()) if isinstance(found, str) else (None, found)
        if reason is not None:
            _add_note(notes, (f"{conf} keeps every test under it for ", f": {reason}"), target)
            continue
        if route.use is None:
            # Reached only through another conftest's tests, which decide it.
            blocked.add(conf)
            continue
        blocked.add(conf)
        narrowed.append(conf)
        framework = {
            e.source
            for e in scope.edges
            if e.target == conf
            and e.edge_type == "framework"
            and e.hint in (CONFTEST_HINT, UNRECORDED_HINT)
        }
        importers = set(scope.parents.get(conf, ())) - framework - {conf}
        for test in sorted(users | importers):
            added.setdefault(test, "conftest-fixture")
    kept = {t: v for t, v in picks.items() if t not in blocked}
    for test, via in added.items():
        kept.setdefault(test, via)
    result = dict(with_importers(kept, scope.parents, blocked))
    for conf in narrowed:
        prefix = _dir_prefix(conf)
        if any(t.startswith(prefix) and is_runnable_test(t) for t in result):
            check = None
        else:
            check = min(
                (t for t in scope.test_files if t.startswith(prefix) and is_runnable_test(t)),
                default=None,
            )
            if check:
                result.setdefault(check, "conftest-import-check")
        _add_note(notes, _narrow_note(conf, routes[conf], check), target)
    return list(result.items())


def _add_note(notes: dict[tuple[str, str], list[str]], key: tuple[str, str], target: str) -> None:
    """Record that *target* got the outcome *key* (the note's text around the target)."""
    targets = notes.setdefault(key, [])
    if target not in targets:
        targets.append(target)


def _dir_prefix(conf: str) -> str:
    d = str(PurePosixPath(conf).parent)
    return "" if d == "." else f"{d}/"


def _narrow_note(conf: str, route: _Route, check: str | None) -> tuple[str, str]:
    use = route.use
    fixtures = sorted(use.fixtures) if use else []
    what = (
        f"the tests requesting {', '.join(fixtures)}" if fixtures else "no fixture-requesting test"
    )
    tail = f"; {check} runs as its import check" if check else ""
    return "", f" reaches {conf} only through its imports: selected {what}{tail}"


def _fixture_users(conf: str, use: ConftestUse, scope: _Scope) -> set[str] | str:
    """Test files (and conftests, standing for their tests) using the reaching fixtures.

    A reason string instead when the users cannot be known. A conftest whose
    fixtures no recorded request names has no users: nothing asks for them.
    """
    if not use.fixtures:
        return set()
    if not scope.recorded:
        return "the index was built before fixture requests were recorded; run `repowise update`"
    hidden = next(
        (
            e.source
            for e in scope.edges
            if e.target == conf and e.edge_type == "framework" and e.hint == UNRECORDED_HINT
        ),
        None,
    )
    if hidden:
        return (
            f"{hidden} asks for fixtures in a way the index cannot record, and "
            f"{', '.join(sorted(use.fixtures))} reach the change"
        )
    wanted = {f"{conf}::{n}" for n in use.fixtures}
    users: set[str] = set()
    for edge in scope.edges:
        if edge.target not in wanted or edge.edge_type == "framework":
            continue
        source_file = edge.source.split("::", 1)[0]
        if source_file == conf:
            continue
        if is_runnable_test(source_file) or scope_kind(source_file) == "conftest":
            users.add(source_file)
        else:
            return f"{edge.source} uses its fixture {edge.target.rsplit('::', 1)[1]}"
    return users
