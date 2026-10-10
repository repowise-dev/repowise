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
   selected already, chosen as the first by path (an import check).
2. **Code the conftest runs.** A fixture, a hook or module-level code that
   calls into the changed code, or hands one of its objects on, can change
   what a test sees. An autouse fixture, a ``pytest_*`` hook or module-level
   code runs for every test, so those keep every test. A fixture a test asks
   for affects the tests that ask for it, directly, through other fixtures,
   through ``usefixtures`` or by ``getfixturevalue``; the index records each of
   those requests as a fixture edge, and those tests are selected.

Granularity is the module, not the function: a fixture that calls anything
in a module on the route to the change counts as reaching it, however deep the
change is. Reading a module attribute (a constant used in an expression, a
setting compared or iterated) or patching one is not running its code;
calling it, passing it on, assigning it or entering it as a context is.
A binding the conftest never reads (``import app.models  # registers them``)
is imported for its side effects, which every test sees, so it keeps every
test.

Fails closed, keeping every test under the conftest with the reason, when the
conftest does not parse, loads plugins, star-imports a module on the route, has
a route the analysis cannot tie to one of its imports, or the index predates
the fixture-request edges.

Ceiling: an import-time side effect (a module registering itself in a shared
registry) that a test observes without any import route of its own to the
changed module is seen only through the import check; so is an operator or
iteration hook defined on an object the conftest reads. Upgrade path: a
per-test map built from a coverage run, which records what each test executed.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ingestion.framework_edges.pytest_edges import CONFTEST_HINT, FIXTURE_HINT
from .test_reachability import _in_clause
from .test_selection import is_runnable_test, scope_kind

# Calls whose first argument is the object an attribute is set on, read from or
# tested on: naming a module there patches or probes it, it runs none of it.
_TARGET_CALLS = frozenset({"setattr", "delattr", "hasattr"})
# Nodes a value passes through unchanged on its way to where it is used.
_TRANSPARENT = (ast.IfExp, ast.BoolOp, ast.Starred, ast.Await, ast.NamedExpr)
# Uses that read a value in place rather than run it or hand it on.
_READS = (
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Subscript,
    ast.comprehension,
    ast.For,
    ast.AsyncFor,
    ast.If,
    ast.While,
    ast.Assert,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Expr,
)


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
    """A module-level function or class of the conftest."""

    node: ast.AST
    reach: str | None = None
    imports: str | None = None
    refs: set[str] = field(default_factory=set)
    params: list[str] = field(default_factory=list)
    fixture: str | None = None
    autouse: bool = False


def module_parts(path: str) -> tuple[str, ...]:
    """A Python file's dotted name parts from the repository root."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return tuple(parts)


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


def _runs(name: ast.Name, parents: Mapping[int, ast.AST]) -> bool:
    """Whether this use of a name runs or hands on what it names (see the module docstring)."""
    cur: ast.AST = name
    while True:
        par = parents.get(id(cur))
        through_attribute = isinstance(par, ast.Attribute) and par.value is cur
        passed_on = isinstance(par, _TRANSPARENT) and not (
            isinstance(par, ast.IfExp) and par.test is cur
        )
        if not (through_attribute or passed_on):
            break
        cur = par
    if isinstance(getattr(cur, "ctx", None), (ast.Store, ast.Del)):
        return False
    par = parents.get(id(cur))
    if isinstance(par, ast.Call) and par.func is not cur:
        func = par.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return not (par.args and par.args[0] is cur and called in _TARGET_CALLS)
    return not isinstance(par, _READS)


def _decorator_name(dec: ast.expr) -> str:
    target = dec.func if isinstance(dec, ast.Call) else dec
    return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")


def _fixture_info(node: ast.AST) -> tuple[str | None, bool]:
    """``(registered name, autouse)`` for a fixture definition, ``(None, False)`` otherwise."""
    for dec in getattr(node, "decorator_list", ()):
        if _decorator_name(dec) != "fixture":
            continue
        name, autouse = getattr(node, "name", None), False
        for kw in dec.keywords if isinstance(dec, ast.Call) else ():
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                name = str(kw.value.value)
            elif kw.arg == "autouse":
                # A computed autouse may be true.
                autouse = not (isinstance(kw.value, ast.Constant) and not kw.value.value)
        return name, autouse
    return None, False


def _module_level(body: list[ast.stmt]) -> tuple[list[ast.AST], list[ast.AST], list[ast.AST]]:
    """``(imports, definitions, other code)`` at module level, through ``if`` / ``try`` / ``with``."""
    imports: list[ast.AST] = []
    defs: list[ast.AST] = []
    code: list[ast.AST] = []
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imports.append(stmt)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(stmt)
            # Decorators, defaults and bases run at import time.
            code.extend(stmt.decorator_list)
            if isinstance(stmt, ast.ClassDef):
                code.extend([*stmt.bases, *(k.value for k in stmt.keywords)])
            else:
                code.extend(d for d in [*stmt.args.defaults, *stmt.args.kw_defaults] if d)
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


def conftest_use(source: str, path: str, entries: Collection[str]) -> ConftestUse:
    """What the conftest at *path* does with *entries*, the files it imports on a route.

    Pure: *source* is the conftest's text.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ConftestUse(run_all=f"{path} does not parse")
    here = module_parts(path)[:-1]
    targets = [module_parts(e) for e in entries]
    parents = _parents(tree)
    imports, defs, code = _module_level(tree.body)

    hot: dict[str, tuple[str, ...]] = {}
    matched: set[tuple[str, ...]] = set()
    for node in imports:
        bound, hits, star = _bindings(node, here, targets)
        if star:
            return ConftestUse(run_all=f"{path} star-imports a module on the route")
        hot.update(bound)
        matched |= hits

    units: dict[str, _Unit] = {}
    for node in defs:
        unit = _Unit(node)
        unit.fixture, unit.autouse = _fixture_info(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            positional = [*args.posonlyargs, *args.args]
            plain = positional[: len(positional) - len(args.defaults)]
            unit.params = [a.arg for a in plain + [
                a for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is None
            ]]
        units[node.name] = unit

    loaded: set[str] = set()
    for name, unit in units.items():
        local: dict[str, tuple[str, ...]] = {}
        for sub in ast.walk(unit.node):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                bound, hits, star = _bindings(sub, here, targets)
                if star:
                    return ConftestUse(run_all=f"{path} star-imports a module on the route")
                if bound:
                    unit.imports = unit.imports or next(iter(bound))
                local.update(bound)
                matched |= hits
        seen: set[str] = set()
        for sub in _body_nodes(unit.node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                seen.add(sub.id)
                if sub.id in (local.keys() | hot.keys()) and unit.reach is None:
                    if _runs(sub, parents):
                        unit.reach = sub.id
                elif sub.id in units and sub.id != name:
                    unit.refs.add(sub.id)
        loaded |= seen
        # A local import nothing reads is there for its side effects.
        if unit.reach is None and (unread := sorted(set(local) - seen)):
            unit.reach = unread[0]

    if missed := [e for e in targets if e not in matched]:
        return ConftestUse(
            run_all=f"{path} reaches {'/'.join(missed[0])} by a route none of its imports names"
        )

    module_refs: set[str] = set()
    for node in code:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                loaded.add(sub.id)
                if sub.id in hot and _runs(sub, parents):
                    return ConftestUse(run_all=f"module-level code in {path} uses {sub.id}")
                if sub.id in units:
                    module_refs.add(sub.id)
            if isinstance(sub, ast.Name) and sub.id == "pytest_plugins":
                return ConftestUse(run_all=f"{path} loads pytest plugins")
            if isinstance(sub, ast.Name) and sub.id == "__all__":
                # Re-exported for the tests that import the conftest, which
                # the selection adds as its importers.
                parent = parents.get(id(sub))
                value = getattr(parent, "value", None)
                loaded |= {
                    e.value
                    for e in getattr(value, "elts", ())
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
    if unread := sorted(set(hot) - loaded):
        return ConftestUse(
            run_all=f"{path} imports {unread[0]} without using it, so for its side effects"
        )

    _propagate(units)
    fixtures: set[str] = set()
    for name, unit in units.items():
        uses = unit.reach or unit.imports
        if name in module_refs and unit.reach:
            return ConftestUse(run_all=f"module-level code in {path} calls {name}")
        if unit.reach and (unit.autouse or name.startswith("pytest_")):
            kind = "autouse fixture" if unit.autouse else "hook"
            return ConftestUse(run_all=f"{path}'s {kind} {name} uses {unit.reach}")
        # A helper reaches tests through the units that call it or through the
        # tests importing the conftest, which the selection adds.
        if uses and unit.fixture and not unit.autouse:
            fixtures.add(name)
    return ConftestUse(
        fixtures=frozenset(fixtures),
        declared=frozenset(n for n, u in units.items() if u.fixture),
    )


def _body_nodes(node: ast.AST):
    """Every node inside a definition, its import-time decorators and defaults excluded."""
    skip = {id(d) for d in getattr(node, "decorator_list", ())}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        skip |= {id(d) for d in [*node.args.defaults, *node.args.kw_defaults] if d}
    stack = [c for c in ast.iter_child_nodes(node) if id(c) not in skip]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(ast.iter_child_nodes(cur))


def _propagate(units: dict[str, _Unit]) -> None:
    """A unit reaches what the units it calls and the fixtures it requests reach."""
    by_fixture = {u.fixture: n for n, u in units.items() if u.fixture}
    changed = True
    while changed:
        changed = False
        for unit in units.values():
            deps = [*unit.refs]
            if unit.fixture:
                deps += [by_fixture[p] for p in unit.params if p in by_fixture]
            for dep in deps:
                other = units[dep]
                if other.reach and not unit.reach:
                    unit.reach, changed = other.reach, True
                if other.imports and not unit.imports:
                    unit.imports, changed = other.imports, True


@dataclass(frozen=True)
class _Edge:
    source: str
    target: str
    edge_type: str
    hint: str | None


async def _edges_into_scopes(
    session: AsyncSession, repo_id: str, targets: list[str]
) -> list[_Edge]:
    """Framework and fixture edges arriving at the conftests and their fixtures: one query."""
    if not targets:
        return []
    params: dict[str, Any] = {"repo_id": repo_id}
    tgt = _in_clause("p", targets, params)
    rows = await session.execute(
        text(
            "SELECT DISTINCT source_node_id, target_node_id, edge_type, hint_source "
            "FROM graph_edges WHERE repository_id = :repo_id "
            f"AND target_node_id IN ({tgt}) "
            "AND edge_type IN ('framework', 'framework_binds', 'calls')"
        ),
        params,
    )
    return [_Edge(*row) for row in rows]


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

    cache: dict[tuple[str, frozenset[str]], ConftestUse] = {}
    for by_conf in routes.values():
        for conf, route in by_conf.items():
            if plugin_loader:
                route.reason = f"{plugin_loader} loads pytest plugins by name"
            elif not route.entries:
                route.reason = f"{conf} is reached by a call alone" if route.direct else None
            elif (source := read(conf)) is None:
                route.reason = f"{conf} could not be read"
            else:
                key = (conf, route.entries)
                if key not in cache:
                    cache[key] = conftest_use(source, conf, route.entries)
                route.use = cache[key]
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

    out: dict[str, list[tuple[str, str]]] = {}
    notes: dict[tuple[str, str], list[str]] = {}
    for target, picks in found.items():
        out[target] = _narrow(
            target, dict(picks), routes.get(target, {}), edges, parents, test_files, notes
        )
    return out, [_note(key, targets) for key, targets in notes.items()]


def _note(key: tuple[str, str], targets: list[str]) -> str:
    """One line per conftest outcome, however many changed files share it."""
    who = targets[0] if len(targets) == 1 else f"{len(targets)} changed files (e.g. {targets[0]})"
    return f"{key[0]}{who}{key[1]}."


def _narrow(
    target: str,
    picks: dict[str, str],
    routes: Mapping[str, _Route],
    edges: list[_Edge],
    parents: Mapping[str, Collection[str]],
    test_files: Collection[str],
    notes: dict[tuple[str, str], list[str]],
) -> list[tuple[str, str]]:
    """One target's picks, each narrowable conftest replaced by the tests it can break."""
    blocked: set[str] = set()
    added: dict[str, str] = {}
    narrowed: list[str] = []
    for conf, route in routes.items():
        if route.reason is not None:
            _add_note(notes, (f"{conf} keeps every test under it for ", f": {route.reason}"), target)
            continue
        if route.use is None:
            # Reached only through another conftest's tests, which decide it.
            blocked.add(conf)
            continue
        users = _fixture_users(conf, route.use, edges)
        if isinstance(users, str):
            _add_note(notes, (f"{conf} keeps every test under it for ", f": {users}"), target)
            continue
        blocked.add(conf)
        narrowed.append(conf)
        framework = {
            e.source
            for e in edges
            if e.target == conf and e.edge_type == "framework" and e.hint == CONFTEST_HINT
        }
        importers = set(parents.get(conf, ())) - framework - {conf}
        for test in sorted(users | importers):
            added.setdefault(test, "conftest-fixture")
    kept = {t: v for t, v in picks.items() if t not in blocked}
    for test, via in added.items():
        kept.setdefault(test, via)
    result = dict(with_importers(kept, parents, blocked))
    for conf in narrowed:
        prefix = _dir_prefix(conf)
        if any(t.startswith(prefix) and is_runnable_test(t) for t in result):
            check = None
        else:
            check = min(
                (t for t in test_files if t.startswith(prefix) and is_runnable_test(t)),
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


def _fixture_users(conf: str, use: ConftestUse, edges: list[_Edge]) -> set[str] | str:
    """Test files (and conftests, standing for their tests) using the reaching fixtures.

    A reason string instead when the users cannot be known.
    """
    if not use.fixtures:
        return set()
    declared = {f"{conf}::{n}" for n in use.declared}
    if not any(e.target in declared and e.hint == FIXTURE_HINT for e in edges):
        return (
            "the index does not record every fixture request (built by an older version); "
            "run `repowise update`"
        )
    wanted = {f"{conf}::{n}" for n in use.fixtures}
    users: set[str] = set()
    for edge in edges:
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
