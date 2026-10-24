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
   working fails there as well. So does a cache reset an autouse fixture runs
   (below) that starts raising.
2. **What the conftest does with the module.** A fixture, a hook or
   module-level code that names something from a module on the route, in any
   way but as the ``setattr`` target (the module or an attribute of it) or a
   cache reset (:func:`_cache_reset`) in an autouse fixture or hook,
   can change what a test sees: calling it, reading a constant, comparing,
   iterating or formatting it all do. An autouse fixture, a ``pytest_*`` hook,
   module-level code (class bodies included) and a fixture or hook defined in
   a class keep every test. A fixture a test asks for affects the tests that
   ask for it, directly, through other fixtures, through ``usefixtures`` or by
   ``getfixturevalue``; the index records each of those requests as a fixture
   edge, and those tests are selected.

Granularity is the module, not the function: a fixture that names anything in
a module on the route to the change counts as reaching it, however deep the
change is. One exception: reading a literal constant of a module on the route,
or calling a function of it that uses nothing the module imports beyond standard
modules that cannot load code by name (:func:`local_names`), depends on that
module's text alone, so it reaches a change only in that module, not one in a
module it merely imports. The same holds for such a name a module on the route
re-exports (``from .console import NAMES as NAMES``): it reaches a change only
in the module that defines it (:func:`entry_names`). A binding the conftest never reads (``import
app.models  # registers them``) is imported for its side effects, which every
test sees, so it keeps every test.

Fails closed, keeping every test under the conftest with the reason, when the
conftest does not parse, loads plugins, star-imports a module on the route, has
a route the analysis cannot tie to one of its imports, when a test or conftest
asks for its fixtures in a way the index cannot record
(``pytest_edges.UNRECORDED_HINT``), when some file imports a conftest by a
name the index could not resolve, or when the index predates fixture-request
edges.

Ceiling: an import-time side effect of the changed module itself (it registers
itself in a shared registry) that a test observes without any import route of
its own to that module is seen only through the import check. So is an
import-time side effect (plugin registration, a patch) elsewhere in the closure
of a module whose local name the conftest reads, since that read still
imports it. A local function that reads or runs repo files by path (``open``,
``Path.read_text``, ``os.system``) is judged by its imports only, as the module
grain already was. A function named as a cache reset that does more than
empty a cache is trusted by its name (:func:`_cache_reset`). Upgrade path: a per-test map built from a coverage run,
which records what each test executed.
"""

from __future__ import annotations

import ast
import builtins
import re
import symtable
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.pyparse import parse_python, symtable_python

from ..ingestion.framework_edges.pytest_edges import (
    CONFTEST_HINT,
    FIXTURE_HINT,
    UNRECORDED_HINT,
    fixture_declaration,
)
from ..ingestion.languages.python_modules import module_parts
from ..ingestion.languages.python_strings import PY_DYNAMIC_LOAD_MARKERS
from .test_reach.db import _in_clause
from .test_selection import BROADCAST_VIA, is_runnable_test, scope_kind

# The call whose first argument is the object an attribute is patched on:
# naming a module there in an autouse fixture is caught by the import check.
# Probing (`hasattr`) or removing (`delattr`) depends on the module, so counts.
_TARGET_CALLS = frozenset({"setattr"})
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# Builtins that run code or reach any module by name (``breakpoint`` and ``help``
# import one named by the environment or the argument).
_RUNS_BY_NAME = frozenset(
    {"eval", "exec", "compile", "globals", "vars", "breakpoint", "help", "__import__"}
)
# Standard modules a local function may use: none of them imports or runs a
# module named by a string, unlike ``importlib``, ``runpy``, ``pickle``,
# ``pydoc``, ``unittest.mock``, ``logging.config`` or ``sys.modules``. An
# allowlist so anything not vetted keeps the module grain.
_NO_LOAD_BY_NAME = frozenset(
    {
        "__future__", "abc", "dataclasses", "enum", "types", "typing",  # declarations
        "collections", "functools", "itertools", "operator", "heapq", "bisect",
        "copy", "weakref", "contextlib",  # values and callables passed in
        "re", "string", "textwrap", "json", "base64", "struct", "hashlib", "uuid",
        "math", "random", "statistics", "decimal", "fractions", "numbers",  # data
        "datetime", "time", "calendar", "threading", "warnings",
        "os", "pathlib", "shutil", "tempfile", "glob", "fnmatch", "stat", "errno",
        "io",  # files and processes by path: see the module ceiling
    }
)  # fmt: skip
# Attributes through which an allowed module still runs code by name or hops to
# a denied one (``os.system('python -m app')``, ``shutil.os``, ``re.sys``); any
# dunder attribute and any ``exec*``/``spawn*`` one count as well.
_RUNS_BY_ATTRIBUTE = frozenset(
    {
        "system", "popen", "startfile", "posix_spawn", "posix_spawnp",
        "register_archive_format", "register_unpack_format",
        "sys", "_sys", "os", "modules",
    }
)  # fmt: skip
# The ``functools`` decorators whose ``cache_clear`` empties a function's cache.
_CACHE_DECORATORS = frozenset({"cache", "lru_cache"})
# Callee names :func:`_cache_reset` reads as emptying a cache.
_CACHE_RESET_RE = re.compile(r"(?:clear|reset|invalidate)_\w*cache\w*")


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


def _first_named(
    module: tuple[str, ...], entries: list[tuple[str, ...]], *, package: bool
) -> tuple[str, ...] | None:
    """The first of *entries* that *module* names (see :func:`_names`)."""
    return next((e for e in entries if _names(module, e, package=package)), None)


def _imported_entry(
    parts: tuple[str, ...], entries: list[tuple[str, ...]]
) -> tuple[str, ...] | None:
    """The entry ``import a.b.c`` reaches: in or under the module, or a package above it."""
    prefixes = [parts[:i] for i in range(1, len(parts))]
    return _first_named(parts, entries, package=True) or next(
        (h for p in prefixes if (h := _first_named(p, entries, package=False))), None
    )


def _from_module(node: ast.ImportFrom, here: tuple[str, ...]) -> tuple[str, ...]:
    """The module a ``from`` import reads, its relative dots resolved against *here*."""
    base = here[: len(here) - (node.level - 1)] if node.level else ()
    return base + tuple((node.module or "").split(".")) if node.module else base


def _bindings(
    node: ast.Import | ast.ImportFrom, here: tuple[str, ...], entries: list[tuple[str, ...]]
) -> tuple[dict[str, tuple[str, ...]], set[tuple[str, ...]], bool]:
    """``({bound name: the dotted path it names}, entries matched, star-imports an entry)``."""
    bound: dict[str, tuple[str, ...]] = {}
    matched: set[tuple[str, ...]] = set()
    star = False
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = tuple(alias.name.split("."))
            if e := _imported_entry(parts, entries):
                bound[alias.asname or parts[0]] = parts if alias.asname else parts[:1]
                matched.add(e)
        return bound, matched, star

    module = _from_module(node, here)
    for alias in node.names:
        if alias.name == "*":
            e = _first_named(module, entries, package=True)
            star = star or e is not None
            continue
        e = _first_named((*module, alias.name), entries, package=True) or _first_named(
            module, entries, package=False
        )
        if e:
            bound[alias.asname or alias.name] = (*module, alias.name)
            matched.add(e)
    return bound, matched, star


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    out: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


def _patch_target(name: ast.Name, parents: Mapping[int, ast.AST]) -> bool:
    """Whether the name, or an attribute chain read from it (``mod.Cls``), is the
    first argument of an attribute patch call."""
    node, _ = _dotted_read(name, parents)
    par = parents.get(id(node))
    if not (isinstance(par, ast.Call) and par.args and par.args[0] is node):
        return False
    func = par.func
    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return called in _TARGET_CALLS


def _cache_reset(name: ast.Name, parents: Mapping[int, ast.AST]) -> bool:
    """Whether the name, or an attribute chain read from it, is called as a cache reset.

    The call is a statement of its own (its result unused) with no arguments,
    and the callee is named ``clear_*cache*``, ``reset_*cache*`` or
    ``invalidate_*cache*`` (``cache_clear`` keeps its :func:`local_names`
    rule). Emptying a cache changes no value its owner serves, only how
    fresh it is, so a test that can see the
    difference reaches the owner through its own imports and is selected for
    it; a reset that starts raising fails the import check. Ceiling: the name
    is trusted, so a "reset" that also rebuilds or reconfigures state other
    modules read reaches tests without an import route of their own only
    through the import check.
    """
    node, attrs = _dotted_read(name, parents)
    call = parents.get(id(node))
    return (
        _CACHE_RESET_RE.fullmatch(attrs[-1] if attrs else name.id) is not None
        and not any(_runs_by_attribute(a) for a in attrs)
        and isinstance(call, ast.Call)
        and call.func is node
        and not call.args
        and not call.keywords
        and isinstance(parents.get(id(call)), ast.Expr)
    )


def _is_literal(node: ast.AST, bound: Collection[str]) -> bool:
    """Whether *node* builds an immutable value from literals alone (``frozenset`` the one call)."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.operand, ast.Constant)
    if isinstance(node, ast.Tuple):
        return all(_is_literal(e, bound) for e in node.elts)
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"
            and "frozenset" not in bound
            and not node.keywords
            and len(node.args) <= 1
            and all(
                isinstance(a, (ast.Set, ast.Tuple, ast.List))
                and all(_is_literal(e, bound) for e in a.elts)
                for a in node.args
            )
        )
    return False


def _bound_names(node: ast.AST) -> list[str]:
    """The names *node* binds in its scope (``["*"]`` for a star import)."""
    if isinstance(node, ast.Name):
        return [] if isinstance(node.ctx, ast.Load) else [node.id]
    if isinstance(node, (*_DEFS, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return [node.name] if node.name else []
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return node.names
    if isinstance(node, ast.MatchMapping):
        return [node.rest] if node.rest else []
    if isinstance(node, ast.alias):
        return [node.asname or node.name.split(".")[0]]
    return []


def _assigned(stmt: ast.stmt) -> tuple[str, ast.AST] | None:
    """``(name, value)`` for a ``NAME = value`` or ``NAME: T = value`` statement."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
    elif isinstance(stmt, ast.AnnAssign) and stmt.simple:
        target = stmt.target
    else:
        return None
    value = getattr(stmt, "value", None)
    return (target.id, value) if isinstance(target, ast.Name) and value is not None else None


def _stdlib_import(stmt: ast.stmt) -> bool:
    """Whether *stmt* imports only from :data:`_NO_LOAD_BY_NAME`, by absolute name.

    Ceiling: a repo module shadowing one of them on the import path is not seen.
    """
    if isinstance(stmt, ast.Import):
        tops = [a.name.split(".")[0] for a in stmt.names]
    elif isinstance(stmt, ast.ImportFrom) and not stmt.level and stmt.module:
        tops = [stmt.module.split(".")[0]]
    else:
        return False
    return all(t in _NO_LOAD_BY_NAME for t in tops)


def _is_reset(attr: ast.AST, method: str, parents: Mapping[int, ast.AST]) -> bool:
    """Whether *attr* is ``<x>.method`` called with no arguments as a statement."""
    call = parents.get(id(attr))
    return (
        isinstance(attr, ast.Attribute)
        and attr.attr == method
        and isinstance(call, ast.Call)
        and call.func is attr
        and not call.args
        and not call.keywords
        and isinstance(parents.get(id(call)), ast.Expr)
    )


def _functools_bindings(body: list[ast.stmt]) -> tuple[set[str], set[str]]:
    """``(names bound to functools.cache or lru_cache, names bound to functools)``."""
    decorators: set[str] = set()
    modules: set[str] = set()
    for stmt in body:
        if isinstance(stmt, ast.ImportFrom) and not stmt.level and stmt.module == "functools":
            decorators |= {a.asname or a.name for a in stmt.names if a.name in _CACHE_DECORATORS}
        elif isinstance(stmt, ast.Import):
            modules |= {a.asname or a.name for a in stmt.names if a.name == "functools"}
    return decorators, modules


def _cached(
    node: ast.FunctionDef | ast.AsyncFunctionDef, outside: Collection[str], body: list[ast.stmt]
) -> bool:
    """Whether *node*'s one decorator is ``functools.cache`` or ``functools.lru_cache``."""
    if len(node.decorator_list) != 1:
        return False
    deco = node.decorator_list[0]
    target = deco.func if isinstance(deco, ast.Call) else deco
    decorators, modules = _functools_bindings(body)
    named = (
        target.id in decorators
        if isinstance(target, ast.Name)
        else isinstance(target, ast.Attribute)
        and target.attr in _CACHE_DECORATORS
        and getattr(target.value, "id", None) in modules
    )
    return named and all(n.id in outside for n in ast.walk(deco) if isinstance(n, ast.Name))


@dataclass(frozen=True)
class _ModuleNames:
    """How a module binds its top-level names, as :func:`local_names` reads them."""

    parents: Mapping[int, ast.AST]
    # Names bound once at top level to an immutable literal.
    literals: frozenset[str]
    # Names that resolve to the standard library or a builtin.
    outside: frozenset[str]
    # Names whose standard-library state one method empties, and that method.
    resets: Mapping[str, str]
    functions: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef]


def _runs_by_attribute(attr: str) -> bool:
    """Whether reading *attr* may run or reach code by name (:data:`_RUNS_BY_ATTRIBUTE`)."""
    return (
        attr in _RUNS_BY_ATTRIBUTE
        or attr.startswith(("exec", "spawn"))
        or (attr.startswith("__") and attr.endswith("__"))
    )


def _globals_used(
    node: ast.FunctionDef | ast.AsyncFunctionDef, scope: symtable.SymbolTable
) -> set[str] | None:
    """The module names *node* reads, or None when it imports or assigns outside itself."""
    if any(m in ast.unparse(node) for m in PY_DYNAMIC_LOAD_MARKERS) or any(
        (isinstance(sub, (ast.Import, ast.ImportFrom)) and not _stdlib_import(sub))
        or (isinstance(sub, ast.Attribute) and _runs_by_attribute(sub.attr))
        for sub in ast.walk(node)
    ):
        return None
    # Decorators and defaults run in the module's scope, the body in its own.
    defaults = [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]
    used = {sub.id for e in defaults if e for sub in ast.walk(e) if isinstance(sub, ast.Name)}
    tables = [scope]
    while tables:
        table = tables.pop()
        tables += table.get_children()
        for sym in (s for s in table.get_symbols() if s.is_global()):
            if sym.is_assigned():
                return None
            if sym.is_referenced():
                used.add(sym.get_name())
    return used


def _only_resets(node: ast.AST, name: str, names: _ModuleNames) -> bool:
    """Whether every use of *name* in *node* empties it with its reset method."""
    method = names.resets.get(name)
    return method is not None and all(
        _is_reset(names.parents.get(id(sub)), method, names.parents)
        for sub in ast.walk(node)
        if isinstance(sub, ast.Name) and sub.id == name
    )


def _function_deps(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    scope: symtable.SymbolTable | None,
    names: _ModuleNames,
) -> set[str] | None:
    """The module functions *node* uses, or None when it uses anything not local."""
    used = _globals_used(node, scope) if scope is not None else None
    if used is None:
        return None
    deps: set[str] = set()
    for name in used:
        if _only_resets(node, name, names):
            continue
        if name in names.functions:
            deps.add(name)
        elif name not in names.literals and name not in names.outside:
            return None
    return deps


def _module_names(tree: ast.Module, counts: Mapping[str, int]) -> _ModuleNames:
    """How *tree* binds its top-level names, given how often each is bound."""
    once = {n for n, c in counts.items() if c == 1}
    assigned = dict(a for stmt in tree.body if (a := _assigned(stmt)) and a[0] in once)
    # Dunder names (``__loader__``, ``__spec__``, ``__builtins__``) reach module machinery.
    plain = (n for n in dir(builtins) if not (n.startswith("__") and n.endswith("__")))
    outside = {n for n in plain if n not in counts and n not in _RUNS_BY_NAME} | {
        n
        for stmt in tree.body
        if _stdlib_import(stmt)
        for alias in getattr(stmt, "names", ())
        if (n := _bound_names(alias)[0]) in once
    }
    literals = {n for n, value in assigned.items() if _is_literal(value, counts)}
    functions = {
        s.name: s
        for s in tree.body
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name in once
    }
    # A container's import-time contents could come from anywhere, so the module
    # may only name it where it builds it.
    at_import = Counter(
        sub.id for node in _module_level(tree.body)[2] for sub in ast.walk(node)
        if isinstance(sub, ast.Name)
    )  # fmt: skip
    resets = {
        n: "clear"
        for n, value in assigned.items()
        if n not in literals
        and at_import[n] == 1
        and isinstance(value, (ast.Call, ast.Dict, ast.List, ast.Set))
        and all(s.id in outside for s in ast.walk(value) if isinstance(s, ast.Name))
    } | {n: "cache_clear" for n, node in functions.items() if _cached(node, outside, tree.body)}
    return _ModuleNames(_parents(tree), frozenset(literals), frozenset(outside), resets, functions)


def _not_local(deps: Mapping[str, set[str] | None]) -> set[str]:
    """The functions that use something not local, directly or through another function."""
    bad = {n for n, d in deps.items() if d is None}
    grew = True
    while grew:
        grew = False
        for name, d in deps.items():
            if name not in bad and d and d & bad:
                bad.add(name)
                grew = True
    return bad


# Keyed on the text alone, so an edited file is a new key and a long-lived MCP
# server stops re-walking the same unchanged entries on every selection.
@lru_cache(maxsize=1024)
def local_names(source: str) -> frozenset[str]:
    """Names a Python module binds once, at its top level, whose use is its own text alone.

    A literal constant (a literal, a tuple or a ``frozenset`` of literals), and
    a function whose decorators, defaults and body name only its own locals,
    builtins, modules in :data:`_NO_LOAD_BY_NAME` and other such names of the
    module: reading or calling either runs nothing the module imports from
    elsewhere. Emptying state the standard library holds leaves the same state
    whatever filled it, so ``NAME.cache_clear`` for a ``functools`` cache and
    ``NAME.clear`` for a container the module names only where it builds it
    are listed too (dotted), and a local function may call them. A name bound
    anywhere else in the module (a local, a ``global``, an import) is left out,
    and so is everything when the module star-imports. Ceiling: another module
    rebinding it at runtime (``mod.NAME = ...``) is not seen, nor is an
    import-time side effect that patches the standard library.
    """
    try:
        tree = parse_python(source)
        table = symtable_python(source)
    except (SyntaxError, ValueError):
        return frozenset()
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        for n in _bound_names(node):
            counts[n] = counts.get(n, 0) + 1
    if "*" in counts:
        return frozenset()
    names = _module_names(tree, counts)
    functions = (t for t in table.get_children() if t.get_type() == "function")
    scopes = {(t.get_name(), t.get_lineno()): t for t in functions}
    deps = {
        name: _function_deps(node, scopes.get((name, node.lineno)), names)
        for name, node in names.functions.items()
    }
    local = set(names.functions) - _not_local(deps)
    return frozenset(
        names.literals | local | {f"{n}.{method}" for n, method in names.resets.items()}
    )


@dataclass(frozen=True)
class EntryNames:
    """A route entry's names whose use depends on one file's text (:func:`entry_names`)."""

    own: frozenset[str] = frozenset()
    # {the file a name is re-exported from: those names, as the entry binds them}
    borrowed: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def stable(self, target: str) -> frozenset[str]:
        """The names whose use cannot reach a change in *target*."""
        return self.own.union(*(n for origin, n in self.borrowed.items() if origin != target))


def _reexports(source: str, here: tuple[str, ...]) -> dict[str, tuple[tuple[str, ...], str]]:
    """``{bound name: (module, the name imported)}`` for each name *source* binds once,
    by a top-level ``from`` import."""
    try:
        tree = parse_python(source)
    except (SyntaxError, ValueError):
        return {}
    counts = Counter(n for node in ast.walk(tree) for n in _bound_names(node))
    if "*" in counts:
        return {}
    return {
        alias.asname or alias.name: (_from_module(stmt, here), alias.name)
        for stmt in tree.body
        if isinstance(stmt, ast.ImportFrom)
        for alias in stmt.names
        if counts[alias.asname or alias.name] == 1
    }


def _module_file(
    module: tuple[str, ...], entry: str, read: Callable[[str], str | None]
) -> str | None:
    """The one readable file *module* names, from the repository root or a source root
    above *entry*; ``None`` when none is, or several are.

    Ceiling: a module under another source root (a namespace package split
    across distributions) is not found, so its names stay unresolved.
    """
    if not module:
        return None
    parts = PurePosixPath(entry).parts
    roots = [()] + [parts[:i] for i in range(1, len(parts)) if parts[i] == module[0]]
    found = {
        path
        for root in roots
        for path in ("/".join((*root, *module)) + ".py", "/".join((*root, *module, "__init__.py")))
        if read(path) is not None
    }
    return found.pop() if len(found) == 1 else None


def entry_names(path: str, read: Callable[[str], str | None]) -> EntryNames:
    """The :func:`local_names` of the file at *path*, and those it re-exports.

    A name the module binds once by ``from X import N`` reads ``X.N``, so when
    ``N`` is a local name of ``X`` its use depends on ``X``'s text alone. One
    hop: a name ``X`` itself re-exports stays unresolved.
    """
    source = read(path) or ""
    parts = module_parts(path)
    here = parts if PurePosixPath(path).name == "__init__.py" else parts[:-1]
    borrowed: dict[str, set[str]] = {}
    for bound, (module, name) in _reexports(source, here).items():
        origin = _module_file(module, path, read)
        if origin is None or origin == path:
            continue
        names = local_names(read(origin) or "")
        mine = {bound + n[len(name) :] for n in names if n == name or n.startswith(f"{name}.")}
        if mine:
            borrowed.setdefault(origin, set()).update(mine)
    return EntryNames(local_names(source), {o: frozenset(n) for o, n in borrowed.items()})


def _dotted_read(name: ast.Name, parents: Mapping[int, ast.AST]) -> tuple[ast.AST, list[str]]:
    """The outermost ``name.a.b`` attribute chain read from *name*, and its attributes."""
    node: ast.AST = name
    attrs: list[str] = []
    while isinstance(par := parents.get(id(node)), ast.Attribute) and par.value is node:
        node = par
        attrs.append(par.attr)
    return node, attrs


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
            tree = parse_python(source)
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
            facts._read_unit_body(name, unit)
        for node in code:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    facts._read_module_name(sub)
        return facts

    def _read_unit_body(self, name: str, unit: _Unit) -> None:
        """Record the imports in *unit*'s body, the names it reads, and the units it names."""
        for sub in _body_nodes(unit.node):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                unit.imports.append(sub)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                unit.loads.append(sub)
                if sub.id in self.units and sub.id != name:
                    unit.refs.add(sub.id)

    def _read_module_name(self, sub: ast.Name) -> None:
        """Record what one name in module-level code tells: a read, plugin loading, ``__all__``."""
        if isinstance(sub.ctx, ast.Load):
            self.code_loads.append(sub)
        if sub.id == "pytest_plugins":
            self.error = f"{self.path} loads pytest plugins"
        if sub.id == "__all__":
            # Re-exported for the tests that import the conftest, which
            # the selection adds as its importers.
            value = getattr(self.parents.get(id(sub)), "value", None)
            self.exported |= {
                e.value
                for e in getattr(value, "elts", ())
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }

    def use(
        self, entries: Collection[str], local: Mapping[str, Collection[str]] | None = None
    ) -> ConftestUse:
        """What the conftest does with *entries*, the files it imports on a route.

        *local* are, per entry the change is not in, its local names
        (:func:`local_names`): reading or calling one does not reach the change.
        """
        if self.error:
            return ConftestUse(run_all=self.error)
        # Sorted: an import binds the first entry it names, so order must not vary.
        targets = [module_parts(e) for e in sorted(entries)]
        stable = {module_parts(e): names for e, names in (local or {}).items()}
        star_import = ConftestUse(run_all=f"{self.path} star-imports a module on the route")
        hot, matched, star = self._bind(self.imports, targets)
        if star:
            return star_import

        reach: dict[str, _Reach] = {}
        loaded = set(self.exported)
        for name, unit in self.units.items():
            local, hits, star = self._bind(unit.imports, targets)
            if star:
                return star_import
            matched |= hits
            reach[name] = self._unit_reach(unit, local, hot, stable)
            loaded |= {n.id for n in unit.loads}

        if missed := [e for e in targets if e not in matched]:
            return ConftestUse(
                run_all=f"{self.path} reaches {'/'.join(missed[0])} by a route none of its "
                "imports names"
            )

        module_refs: set[str] = set()
        for load in self.code_loads:
            loaded.add(load.id)
            if (
                load.id in hot
                and not _patch_target(load, self.parents)
                and not self._reads_local(load, hot[load.id], stable)
            ):
                return ConftestUse(run_all=f"module-level code in {self.path} uses {load.id}")
            if load.id in self.units:
                module_refs.add(load.id)
        if unread := sorted(set(hot) - loaded):
            return ConftestUse(
                run_all=f"{self.path} imports {unread[0]} without using it, so for its side effects"
            )
        self._propagate(reach)
        return self._decide(reach, module_refs)

    def _bind(
        self, nodes: list[Any], targets: list[tuple[str, ...]]
    ) -> tuple[dict[str, tuple[str, ...]], set[tuple[str, ...]], bool]:
        """``({bound name: the dotted path it names}, entries matched, star-imports an entry)``.

        Stops at the first star import, which the caller answers on its own.
        """
        bound: dict[str, tuple[str, ...]] = {}
        matched: set[tuple[str, ...]] = set()
        for node in nodes:
            names, hits, star = _bindings(node, self.here, targets)
            if star:
                return bound, matched, True
            bound.update(names)
            matched |= hits
        return bound, matched, False

    def _unit_reach(
        self,
        unit: _Unit,
        local: Mapping[str, tuple[str, ...]],
        hot: Mapping[str, tuple[str, ...]],
        stable: Mapping[tuple[str, ...], Collection[str]],
    ) -> _Reach:
        """How *unit* uses the route, given its own (*local*) and module (*hot*) bindings."""
        r = _Reach(imports=next(iter(local), None))
        for load in unit.loads:
            path = local.get(load.id) or hot.get(load.id)
            if path is None:
                continue
            if _patch_target(load, self.parents) or _cache_reset(load, self.parents):
                r.patch = r.patch or load.id
            elif not self._reads_local(load, path, stable):
                r.run = r.run or load.id
        # A local import nothing reads is there for its side effects.
        if r.run is None and (unread := sorted(set(local) - {n.id for n in unit.loads})):
            r.run = unread[0]
        return r

    def _reads_local(
        self,
        load: ast.Name,
        path: tuple[str, ...],
        stable: Mapping[tuple[str, ...], Collection[str]],
    ) -> bool:
        """Whether *load* (bound to *path*) only reads or calls a local name of a *stable* entry."""
        outer, attrs = _dotted_read(load, self.parents)
        if any(_runs_by_attribute(a) for a in attrs):
            return False
        full = (*path, *attrs)
        # ``NAME.cache_clear()`` and the like: listed dotted, and only as the call itself.
        reset = len(full) > 2 and _is_reset(outer, full[-1], self.parents)
        return isinstance(getattr(outer, "ctx", None), ast.Load) and any(
            (full[k] in names or (reset and k == len(full) - 2 and ".".join(full[k:]) in names))
            and _names(full[:k], entry, package=False)
            for entry, names in stable.items()
            for k in range(1, len(full))
        )

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


def conftest_use(
    source: str,
    path: str,
    entries: Collection[str],
    local: Mapping[str, Collection[str]] | None = None,
) -> ConftestUse:
    """What the conftest at *path* (text *source*) does with *entries*. Pure."""
    return ConftestFacts.parse(source, path).use(entries, local)


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
    ``external:`` node, and the importer it hides must not be dropped. A
    type-only import (``if TYPE_CHECKING:``) never runs, so it hides nothing.
    """
    if not targets:
        return []
    params: dict[str, Any] = {"repo_id": repo_id, "type_only": False}
    tgt = _in_clause("p", targets, params)
    rows = await session.execute(
        text(
            "SELECT DISTINCT source_node_id, target_node_id, edge_type, hint_source "
            "FROM graph_edges WHERE repository_id = :repo_id AND ("
            f"(target_node_id IN ({tgt}) "
            "AND edge_type IN ('framework', 'framework_binds', 'calls')) "
            "OR (target_node_id >= 'external:' AND target_node_id < 'external;' "
            "AND target_node_id LIKE '%conftest' AND edge_type = 'imports' "
            "AND type_only = :type_only))"
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
    read_source: Callable[[str], str | None] | None = None,
) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """Each target's ``[(test file, via)]`` with conftest routes narrowed, and the notes.

    *found* are the walks' picks per target and *entries* the files each was
    reached through; *parents* the test files importing each test file and
    *parent_entries* the files each importer reached it through; *read* returns
    a conftest's text and *read_source* any file's, for the local names a
    conftest reads (without it, every read counts as reaching). Every conftest
    on a route that this module cannot narrow keeps today's answer: every test
    under it.
    """
    routes = _conftest_routes(found, entries, parents, parent_entries)
    _judge_routes(routes, read, plugin_loader, read_source)
    scope = await _load_scope(session, repo_id, routes, parents, test_files)
    out: dict[str, list[tuple[str, str]]] = {}
    notes: dict[tuple[str, str], list[str]] = {}
    for target, picks in found.items():
        out[target] = _narrow(target, dict(picks), routes.get(target, {}), scope, notes)
    return out, [_note(key, targets) for key, targets in notes.items()]


def _conftest_routes(
    found: Mapping[str, dict[str, str]],
    entries: Mapping[str, Mapping[str, Collection[str]]],
    parents: Mapping[str, Collection[str]],
    parent_entries: Mapping[str, Mapping[str, Collection[str]]],
) -> dict[str, dict[str, _Route]]:
    """``{target: {conftest: its route}}`` for every conftest a target's picks reach."""
    # Test files each conftest imports, asked once per conftest per target.
    imported: dict[str, list[str]] = {}
    for test, importers in parents.items():
        for importer in importers:
            imported.setdefault(importer, []).append(test)
    routes: dict[str, dict[str, _Route]] = {}
    for target, picks in found.items():
        full = dict(picks)
        with_importers(full, parents)
        for conf in sorted(t for t in full if scope_kind(t) == "conftest" and t != target):
            via = set(entries.get(target, {}).get(conf, ()))
            via.update(
                v
                for h in imported.get(conf, ())
                if h != conf and h in full
                for v in parent_entries.get(h, {}).get(conf, ())
            )
            # A parent conftest is not an import: pytest links a nested
            # conftest to it, and that parent's own decision covers the route.
            own = frozenset(v for v in via if scope_kind(v) != "conftest")
            routes.setdefault(target, {})[conf] = _Route(own, conf in picks and not via)
    return routes


def _judge_routes(
    routes: Mapping[str, Mapping[str, _Route]],
    read: Callable[[str], str | None],
    plugin_loader: str | None,
    read_source: Callable[[str], str | None] | None = None,
) -> None:
    """Set each route's use and reason. One parse per conftest and per entry; each distinct
    route through it is matched against it once."""
    facts: dict[str, ConftestFacts | None] = {}
    consts: dict[str, EntryNames] = {}
    uses: dict[tuple, ConftestUse] = {}
    for target, conf, route in ((t, c, r) for t, by in routes.items() for c, r in by.items()):
        if conf not in facts:
            source = read(conf)
            facts[conf] = None if source is None else ConftestFacts.parse(source, conf)
        # The changed file's own local names may be what changed.
        others = route.entries - {target}
        for e in others - set(consts) if read_source else ():
            consts[e] = entry_names(e, read_source)
        stable = {e: names for e in others if e in consts and (names := consts[e].stable(target))}
        _judge(conf, route, facts[conf], plugin_loader, uses, stable)


def _judge(
    conf: str,
    route: _Route,
    parsed: ConftestFacts | None,
    plugin_loader: str | None,
    uses: dict[tuple, ConftestUse],
    stable: Mapping[str, frozenset[str]],
) -> None:
    if plugin_loader:
        route.reason = f"{plugin_loader} loads pytest plugins by name"
    elif not route.entries:
        route.reason = f"{conf} is reached by a call alone" if route.direct else None
    elif parsed is None:
        route.reason = f"{conf} could not be read"
    else:
        key = (conf, route.entries, frozenset(stable.items()))
        if key not in uses:
            uses[key] = parsed.use(route.entries, stable)
        route.use = uses[key]
        route.reason = route.use.run_all


async def _load_scope(
    session: AsyncSession,
    repo_id: str,
    routes: Mapping[str, Mapping[str, _Route]],
    parents: Mapping[str, Collection[str]],
    test_files: Collection[str],
) -> _Scope:
    """The edges into every narrowable conftest and its fixtures, read once for all targets."""
    judged = [(conf, r) for by_conf in routes.values() for conf, r in by_conf.items()]
    narrowable = {conf for conf, r in judged if r.use is not None and r.reason is None}
    declared = {
        f"{conf}::{name}"
        for conf, r in judged
        if conf in narrowable and r.use is not None
        for name in r.use.declared
    }
    edges = await _edges_into_scopes(session, repo_id, sorted(narrowable | declared))
    stamped = {e.target for e in edges if e.hint == FIXTURE_HINT}
    unstamped = any(
        r.use is not None
        and r.use.fixtures
        and not any(f"{conf}::{n}" in stamped for n in r.use.declared)
        for conf, r in judged
        if conf in narrowable
    )
    # Asked only when a conftest's fixtures show no recorded request at all.
    recorded = await _records_fixture_requests(session, repo_id) if unstamped else True
    return _Scope(edges, parents, test_files, recorded)


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
    _first: dict[str, str | None] = field(default_factory=dict, compare=False)

    def unresolved_conftest_import(self) -> str | None:
        return next((e.source for e in self.edges if e.target.startswith("external:")), None)

    def first_test_under(self, prefix: str) -> str | None:
        """The first runnable test file by path under *prefix*, asked once per conftest."""
        if prefix not in self._first:
            self._first[prefix] = min(
                (t for t in self.test_files if t.startswith(prefix) and is_runnable_test(t)),
                default=None,
            )
        return self._first[prefix]


def _narrow(
    target: str,
    picks: dict[str, str],
    routes: Mapping[str, _Route],
    scope: _Scope,
    notes: dict[tuple[str, str], list[str]],
) -> list[tuple[str, str]]:
    """One target's picks, each narrowable conftest replaced by the tests it can break.

    A test only a conftest keeping every test under it adds is labelled
    :data:`BROADCAST_VIA`.
    """
    blocked: set[str] = set()
    spread: set[str] = set()
    added: dict[str, str] = {}
    narrowed: list[str] = []
    hidden_importer = scope.unresolved_conftest_import()
    for conf, route in routes.items():
        reason, users = _route_users(conf, route, scope, hidden_importer)
        if reason is not None:
            _add_note(notes, (f"{conf} keeps every test under it for ", f": {reason}"), target)
            spread.add(conf)
            continue
        if route.use is None:
            # Reached only through another conftest's tests, which decide it.
            blocked.add(conf)
            continue
        blocked.add(conf)
        narrowed.append(conf)
        for test in sorted(users | _test_importers(conf, scope)):
            added.setdefault(test, "conftest-fixture")
    kept = {t: v for t, v in picks.items() if t not in blocked}
    for test, via in added.items():
        kept.setdefault(test, via)
    # Before the walk below, which adds to the dict it is given.
    start = {t: v for t, v in kept.items() if t not in spread}
    result = dict(with_importers(kept, scope.parents, blocked))
    if spread:
        inner = dict(with_importers(start, scope.parents, blocked | spread))
        for t in [t for t in result if t not in inner and t not in spread]:
            result[t] = BROADCAST_VIA
    for conf in narrowed:
        check = _add_import_check(conf, result, scope)
        _add_note(notes, _narrow_note(conf, routes[conf], check), target)
    return list(result.items())


def _route_users(
    conf: str, route: _Route, scope: _Scope, hidden_importer: str | None
) -> tuple[str | None, set[str]]:
    """``(why the route cannot narrow, or None; the fixture users)`` for one conftest."""
    reason = route.reason
    if reason is None and route.use is not None and hidden_importer:
        reason = (
            f"{hidden_importer} imports a conftest by a name the index could not "
            "resolve, so that importer is not known"
        )
    if reason is None and route.use is not None:
        found = _fixture_users(conf, route.use, scope)
        return (found, set()) if isinstance(found, str) else (None, found)
    return reason, set()


def _test_importers(conf: str, scope: _Scope) -> set[str]:
    """The files importing *conf* itself, not the ones pytest links to it as their conftest."""
    framework = {
        e.source
        for e in scope.edges
        if e.target == conf
        and e.edge_type == "framework"
        and e.hint in (CONFTEST_HINT, UNRECORDED_HINT)
    }
    return set(scope.parents.get(conf, ())) - framework - {conf}


def _add_import_check(conf: str, result: dict[str, str], scope: _Scope) -> str | None:
    """Add a test under *conf* to import it when the selection holds none; the test added."""
    prefix = _dir_prefix(conf)
    if any(t.startswith(prefix) and is_runnable_test(t) for t in result):
        return None
    check = scope.first_test_under(prefix)
    if check:
        result.setdefault(check, "conftest-import-check")
    return check


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
