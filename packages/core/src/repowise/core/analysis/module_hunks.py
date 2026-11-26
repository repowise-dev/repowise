"""Which tests a module-level change to a measured Python file needs.

A per-test map never credits a module-level line (an import, a constant, a
class body, a decorator, a default value) to a test: it runs once, at import,
while pytest collects, and coverage records it under no test's context. So a
module-level change cannot be matched by line. What it changes is the value of
the names its statements bind, and a test can only see that value by running
code that reads the name or by naming it itself.

:func:`module_change` reads which names the change rebinds or removes, or says
why it cannot be told (a call run for its effect, a store into another object,
control flow, a decorator that may register, a new import that leads back to
the file). :func:`readers` follows those names through every importer, re-
exports and module-level derived names included. The tests the change needs are
then the tests whose measured runs executed any line of the file or of a module
reading a changed name, plus the test files importing one. A measured test that
did neither ran with the old value without reading it.

Ceiling: Python only. A name read through ``getattr`` with a string, a value a
module-level call stores elsewhere, and a memo or once-per-process function
seen only by the graph's call edges are what the guards below cover; anything
they do not name keeps the graph's answer.
"""

from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..pyparse import parse_python
from .function_hunks import Body, function_bodies, innermost

#: Decorators that wrap or mark a function without registering it anywhere.
_PURE_DECORATORS = frozenset(
    {
        "abstractmethod",
        "asynccontextmanager",
        "cache",
        "cached_property",
        "classmethod",
        "contextmanager",
        "dataclass",
        "deleter",
        "final",
        "getter",
        "lru_cache",
        "overload",
        "override",
        "property",
        "runtime_checkable",
        "setter",
        "singledispatch",
        "singledispatchmethod",
        "staticmethod",
        "total_ordering",
        "unique",
        "wraps",
    }
)
#: Decorators whose function runs once for every later caller in the process.
_MEMO_DECORATORS = frozenset({"cache", "cached_property", "lru_cache"})


@dataclass(frozen=True)
class ModuleChange:
    """The module-level names a change rebinds (its own new names included) and removes."""

    names: frozenset[str]
    removed: frozenset[str]
    # The functions (enclosing names) whose bodies the change touches too.
    bodies: frozenset[tuple[str, ...]] = frozenset()


@dataclass(frozen=True)
class Importer:
    """A file importing a module: the names it takes, empty for the module itself."""

    path: str
    names: frozenset[str]


@dataclass(frozen=True)
class Readers:
    """Who can see a module-level change: production modules whose runs read a changed
    name (the file itself included), and test files importing one."""

    modules: frozenset[str]
    tests: frozenset[str]
    names: frozenset[str]


class _Side:
    """One side of a changed file, parsed once."""

    def __init__(self, text: str) -> None:
        self.tree = parse_python(text)
        self.lines = [line.removesuffix("\r") for line in text.split("\n")]
        self.bodies: list[Body] = function_bodies(self.tree)
        self.strings = frozenset(
            line
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant | ast.JoinedStr)
            and (node.end_lineno or node.lineno) > node.lineno
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1)
        )

    def module_level(self, lines: Iterable[int]) -> list[int]:
        """Those of *lines* holding code outside every function body."""
        out = []
        for line in lines:
            if not 0 < line <= len(self.lines):
                continue
            text = self.lines[line - 1].strip()
            code = line in self.strings or (text and not text.startswith("#"))
            if code and innermost(self.bodies, line) is None:
                out.append(line)
        return out

    def in_bodies(self, lines: Iterable[int]) -> set[tuple[str, ...]]:
        """The functions whose bodies hold a code line of *lines*."""
        out = set()
        for line in lines:
            if not 0 < line <= len(self.lines):
                continue
            text = self.lines[line - 1].strip()
            code = line in self.strings or (text and not text.startswith("#"))
            if code and (body := innermost(self.bodies, line)) is not None:
                out.add(body.chain)
        return out

    def statements(self, lines: Collection[int]) -> list[ast.stmt]:
        """The top-level statements holding any of *lines*, decorators included."""
        return [s for s in self.tree.body if any(line in _span(s) for line in lines)]


def _span(stmt: ast.stmt) -> range:
    first = min([stmt.lineno, *(d.lineno for d in getattr(stmt, "decorator_list", ()))])
    return range(first, (stmt.end_lineno or stmt.lineno) + 1)


def _parse(text: str | None) -> _Side | None:
    if text is None:
        return None
    try:
        return _Side(text)
    except (SyntaxError, ValueError):
        return None


def module_change(
    old: str | None, new: str | None, hunks, imports_back: Callable[[], bool]
) -> ModuleChange | str:
    """The names a change to a Python module's top level binds, or why that cannot be told.

    Every touched line outside function bodies is read on its own side (the
    old file for removed lines, the new file for added ones). A docstring, a
    ``TYPE_CHECKING`` block of imports and comments bind nothing that runs.
    Imports, assignments to plain names, ``def`` and ``class`` statements with
    only pure decorators bind their names. *imports_back* says whether an
    import the new side gained leads back to this file, which could make a
    circular import fail for some importers and not others.
    """
    before, after = _parse(old), _parse(new)
    if before is None or after is None:
        return "it does not parse on both sides"
    names: set[str] = set()
    bodies: set[tuple[str, ...]] = set()
    for old_start, old_count, new_start, new_count in hunks:
        for side, lines in (
            (before, range(old_start, old_start + old_count)),
            (after, range(new_start, new_start + new_count)),
        ):
            bodies |= side.in_bodies(lines)
            touched = side.module_level(lines)
            for stmt in side.statements(touched):
                why = _unbindable(stmt, touched)
                if why:
                    return why
                if (
                    isinstance(stmt, ast.Import | ast.ImportFrom)
                    and side is after
                    and imports_back()
                ):
                    return "an import it gained leads back to it"
                names |= _binds(stmt) or set()
    removed = _bound(before.tree) - _bound(after.tree)
    return ModuleChange(frozenset(names), frozenset(removed), frozenset(bodies))


def _unbindable(stmt: ast.stmt, touched: Collection[int]) -> str | None:
    """Why a touched top-level statement does more than bind names, else ``None``."""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return None  # a docstring
    if isinstance(stmt, ast.If) and _type_checking(stmt.test):
        imports = all(isinstance(s, ast.Import | ast.ImportFrom) for s in stmt.body + stmt.orelse)
        return None if imports else "a TYPE_CHECKING block runs code"
    if _binds(stmt) is None:
        return f"a module-level {type(stmt).__name__.lower()} runs at import"
    if isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if not all(_plain_target(t) for t in targets):
            return "it stores into an attribute or an item"
    if isinstance(stmt, ast.ClassDef) and stmt.keywords:
        return "a class with a metaclass or class keywords"
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and not _pure(
        _decorators(stmt, touched)
    ):
        return "a decorator may register what it decorates"
    if isinstance(stmt, ast.ImportFrom) and any(a.name == "*" for a in stmt.names):
        return "a star import binds unknown names"
    return _runs_code(stmt)


#: Calls that build a value and touch nothing else, so binding their result is a
#: binding: builtin containers and conversions, ``re.compile``, paths, typing and
#: dataclass helpers, loggers.
_PURE_CALLS = frozenset(
    {
        "Counter", "OrderedDict", "Path", "PurePath", "PurePosixPath", "PureWindowsPath",
        "TypeVar", "NewType", "NamedTuple", "MappingProxyType", "bool", "bytes", "cast",
        "compile", "defaultdict", "deque", "dict", "enumerate", "escape", "field", "float",
        "frozenset", "getLogger", "get_logger", "getenv", "int", "list", "namedtuple",
        "object", "range", "set", "sorted", "str", "timedelta", "tuple", "zip",
    }
)  # fmt: skip


def _runs_code(stmt: ast.stmt) -> str | None:
    """Why *stmt* runs repo code at import (a call, an ``await``, a walrus), else ``None``.

    Function bodies are left out: they run when called. A call to a pure builder
    or a pure decorator is not code that can store anything elsewhere.
    """
    bodies = {
        id(n)
        for f in ast.walk(stmt)
        if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
        for n in (f.body if isinstance(f.body, list) else [f.body])
    }
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        if id(node) in bodies:
            continue
        if isinstance(node, ast.Await | ast.NamedExpr | ast.Yield | ast.YieldFrom):
            return f"a module-level {type(node).__name__.lower()} runs at import"
        if isinstance(node, ast.Call):
            name = _decorator_name(node.func)
            if name not in _PURE_CALLS and name not in _PURE_DECORATORS:
                return f"a module-level call to {name or 'an expression'} runs at import"
        stack.extend(ast.iter_child_nodes(node))
    return None


def _plain_target(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Tuple | ast.List):
        return all(_plain_target(e) for e in node.elts)
    return isinstance(node, ast.Starred) and _plain_target(node.value)


def _type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _binds(stmt: ast.stmt) -> set[str] | None:
    """The names a top-level statement binds; ``None`` for one that does more."""
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {stmt.name}
    if isinstance(stmt, ast.Import | ast.ImportFrom):
        return {a.asname or a.name.split(".")[0] for a in stmt.names if a.name != "*"}
    if isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    if isinstance(stmt, ast.If) and _type_checking(stmt.test):
        return set()
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return set()
    return None


def _bound(tree: ast.Module) -> set[str]:
    return {n for s in tree.body for n in _binds(s) or ()}


def _decorators(stmt: ast.stmt, touched: Collection[int]) -> list[ast.expr]:
    """*stmt*'s decorators, and those of the methods inside it the change touches."""
    out = list(getattr(stmt, "decorator_list", ()))
    if isinstance(stmt, ast.ClassDef):
        for node in stmt.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and any(
                line in _span(node) for line in touched
            ):
                out += list(node.decorator_list)
    return out


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None


def _pure(decorators: Iterable[ast.expr]) -> bool:
    return all(_decorator_name(d) in _PURE_DECORATORS for d in decorators)


def _reads(node: ast.AST, names: Collection[str]) -> bool:
    """Whether *node* reads one of *names*, bare or as an attribute."""
    return any(
        (isinstance(n, ast.Name) and n.id in names)
        or (isinstance(n, ast.Attribute) and n.attr in names)
        for n in ast.walk(node)
    )


def _functions(tree: ast.Module) -> Iterable[tuple[tuple[str, ...], ast.AST]]:
    """``(enclosing names, node)`` for every function, nested ones included."""
    stack: list[tuple[tuple[str, ...], ast.AST]] = [((), tree)]
    while stack:
        chain, node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                here = (*chain, child.name)
                if not isinstance(child, ast.ClassDef):
                    yield here, child
                stack.append((here, child))
            else:
                stack.append((chain, child))


def _once_reader(
    path: str, tree: ast.Module, names: Collection[str], once: Collection[str]
) -> str | None:
    """A function of *path* reading one of *names* that runs once for every later caller."""
    for chain, node in _functions(tree):
        if not _reads(node, names):
            continue
        memo = any(_decorator_name(d) in _MEMO_DECORATORS for d in node.decorator_list)
        if memo or "::".join((path, *chain)) in once:
            return ".".join(chain)
    return None


async def readers(
    path: str,
    change: ModuleChange,
    importers: Callable[[str], Awaitable[Iterable[Importer]]],
    read: Callable[[str], str | None],
    *,
    new_text: str | None,
    is_test: Callable[[str], bool],
    is_support: Callable[[str], bool],
    once: Collection[str] = frozenset(),
) -> Readers | str:
    """Who can see *change*, or why that cannot be told.

    Each importer taking a changed name (or the module whole) is followed:
    a test file joins as a reader; a test helper passes it on to the tests
    importing the helper; a production module joins, and the names it binds
    at module level from a changed one (a re-export, ``X = changed * 2``)
    are followed in turn. Not told: a conftest taking a changed name (its
    fixtures run for tests the map cannot name), a removed name still
    imported, a module-level statement reading a changed name for its effect,
    and a memo or once-per-process function reading one (*once*: the graph's
    :func:`~.function_hunks.run_once_symbols`), whose later callers run none
    of its lines.
    """
    every = _own_names(path, change, new_text, once)
    if isinstance(every, str):
        return every
    modules, tests = {path}, set()
    # ``None``: every importer of a test helper counts, whatever it takes, since
    # the map does not measure helpers and any test importing one may run it.
    queue: list[tuple[str, frozenset[str] | None]] = [(path, every)]
    seen: set[tuple[str, frozenset[str] | None]] = set(queue)
    while queue:
        module, names = queue.pop()
        for imp in await _importers_of(module, importers):
            step = _step(imp, module, names, path, change, read, once, is_test, is_support)
            if isinstance(step, str):
                return step
            if step is None:
                continue
            if step[0] == "test":
                tests.add(imp.path)
                continue
            if step[0] == "module":
                modules.add(imp.path)
            key = (imp.path, step[1])
            if key not in seen and key[1] != frozenset():
                seen.add(key)
                queue.append(key)
    return Readers(frozenset(modules), frozenset(tests), every)


def _own_names(
    path: str, change: ModuleChange, new_text: str | None, once: Collection[str]
) -> frozenset[str] | str:
    """The changed names plus those the file derives from them at import
    (``DOUBLE = LIMIT * 2``), or why the file's own code cannot be followed."""
    tree = _parse(new_text)
    if tree is None:
        return "it does not parse"
    if fn := _once_function(path, tree.tree, change.bodies, once):
        return f"{fn} changed and runs once for every later caller"
    derived = _derived(tree.tree, change.names | change.removed)
    if isinstance(derived, str):
        return f"it reads a changed name at import: {derived}"
    every = frozenset(derived) | change.names | change.removed
    if fn := _once_reader(path, tree.tree, every, once):
        return f"{fn} reads a changed name and runs once for every later caller"
    return every


def _step(
    imp: Importer,
    module: str,
    names: frozenset[str] | None,
    path: str,
    change: ModuleChange,
    read: Callable[[str], str | None],
    once: Collection[str],
    is_test: Callable[[str], bool],
    is_support: Callable[[str], bool],
) -> tuple[str, frozenset[str] | None] | str | None:
    """What one importer of *module* is: ``None`` (takes no changed name), a reader
    ``("test", _)``, ``("helper", None)`` or ``("module", names it passes on)``, or
    why it cannot be followed."""
    if "*" in imp.names:
        return f"{imp.path} star-imports {module}"
    taken = imp.names if names is None else imp.names & names
    if imp.names and not taken:
        return None
    if module == path and taken & change.removed:
        return f"{imp.path} imports a name the change removes"
    if is_test(imp.path):
        return ("test", None)
    if PurePosixPath(imp.path).name == "conftest.py":
        return f"{imp.path} takes a changed name"
    if is_support(imp.path):
        return ("helper", None)
    followed = _follow(imp, module, taken or names or frozenset(), read, once, change.removed)
    return followed if isinstance(followed, str) else ("module", frozenset(followed))


async def _importers_of(
    module: str, importers: Callable[[str], Awaitable[Iterable[Importer]]]
) -> list[Importer]:
    """*module*'s importers, a ``from pkg import module`` counted as importing it whole.

    That import names the module, not what it reads from it (``module.NAME``);
    its edge may land on the module or on the package's ``__init__.py``.
    """
    stem = PurePosixPath(module)
    own = stem.parent.name if stem.name == "__init__.py" else stem.stem
    package = (stem.parent / "__init__.py").as_posix()
    rows = list(await importers(module))
    if package != module:
        rows += [i for i in await importers(package) if own in i.names]
    return [Importer(i.path, frozenset()) if own in i.names else i for i in rows]


def _follow(
    imp: Importer,
    module: str,
    names: frozenset[str],
    read: Callable[[str], str | None],
    once: Collection[str],
    removed: frozenset[str],
) -> set[str] | str:
    """The names *imp* binds from *names*, or why it cannot be followed.

    Its aliases of the names it imports, and every name a top-level statement
    reading one binds, to a fixpoint. A whole-module import passes on the name
    it binds the module to (``import app.cfg as settings``) and what it reads
    at module level as ``module.name``.
    """
    side = _parse(read(imp.path))
    if side is None:
        return f"{imp.path} does not parse"
    aliases = _aliases(side.tree, module, names, whole=not imp.names)
    reads = aliases | (set(names) if not imp.names else set())
    if fn := _once_reader(imp.path, side.tree, reads, once):
        return f"{imp.path}: {fn} reads a changed name and runs once for every later caller"
    if not imp.names and any(_reads_at_top(s, removed) for s in side.tree.body):
        return f"{imp.path} reads a removed name at import"
    derived = _derived(side.tree, reads)
    if isinstance(derived, str):
        return f"{imp.path} reads a changed name at import: {derived}"
    return aliases | derived


def _aliases(tree: ast.Module, module: str, names: Collection[str], *, whole: bool) -> set[str]:
    """The names *tree* binds to *names* imported from *module*, and with *whole* the
    names it binds the module itself to (``from pkg import mod``, ``import pkg.mod as m``)."""
    stem = PurePosixPath(module)
    own = stem.parent.name if stem.name == "__init__.py" else stem.stem
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            wanted = {*names, own} if whole else set(names)
            out |= {a.asname or a.name for a in node.names if a.name in wanted}
        elif isinstance(node, ast.Import) and whole:
            out |= {
                a.asname or a.name.split(".")[0]
                for a in node.names
                if a.name.rsplit(".", 1)[-1] == own
            }
    return out


def _derived(tree: ast.Module, reads: Collection[str]) -> set[str] | str:
    """The names *tree*'s top-level statements bind from *reads*, to a fixpoint, or why
    a statement reading one does more than bind."""
    known, bound = set(reads), set()
    while True:
        grew = False
        for stmt in tree.body:
            if isinstance(stmt, ast.Import | ast.ImportFrom) or not _reads_at_top(stmt, known):
                continue
            why = _unbindable(stmt, _span(stmt))
            if why:
                return why
            new = (_binds(stmt) or set()) - known
            if new:
                known |= new
                bound |= new
                grew = True
        if not grew:
            return bound


def _once_function(
    path: str, tree: ast.Module, chains: Collection[tuple[str, ...]], once: Collection[str]
) -> str | None:
    """A changed function (or one around it) that runs once for every later caller."""
    nodes = dict(_functions(tree))
    for chain in chains:
        for i in range(1, len(chain) + 1):
            node = nodes.get(chain[:i])
            memo = node is not None and any(
                _decorator_name(d) in _MEMO_DECORATORS for d in node.decorator_list
            )
            if memo or "::".join((path, *chain[:i])) in once:
                return ".".join(chain[:i])
    return None


def _reads_at_top(stmt: ast.stmt, names: Collection[str]) -> bool:
    """Whether *stmt* reads one of *names* outside the bodies of the functions it holds."""
    bodies = {
        id(n)
        for f in ast.walk(stmt)
        if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)
        for n in f.body
    }
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        if id(node) in bodies:
            continue
        if (
            (isinstance(node, ast.Name) and node.id in names)
            or (isinstance(node, ast.Attribute) and node.attr in names)
            # ``getattr(module, "NAME")`` reads it by its name.
            or (isinstance(node, ast.Constant) and node.value in names)
        ):
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _load_imports(text: str | None) -> bytes:
    """The imports *text* runs when it loads (its top-level ones), as source.

    A ``TYPE_CHECKING`` import never runs, and one in a function body runs when
    the function is called. Ceiling: a function called at import that imports
    lazily can still close a cycle; the load-time graph does not see it either.
    """
    side = _parse(text)
    if side is None:
        return (text or "").encode()
    top = [s for s in side.tree.body if isinstance(s, ast.Import | ast.ImportFrom)]
    return "\n".join(ast.unparse(s) for s in top).encode()


def users_of(readers_: Readers, users: Mapping[str, Collection[str]]) -> frozenset[str]:
    """The test files *readers_* names: measured users of its modules, and its importing tests."""
    return frozenset({t for m in readers_.modules for t in users.get(m, ())} | set(readers_.tests))


async def module_scoped_tests(
    session,
    repo_id: str,
    repo_path,
    change,
    paths: Collection[str],
    *,
    repo_keys: Collection[str],
    read_source: Callable[[str], str | None] | None = None,
    is_test: Callable[[str], bool],
) -> dict[str, frozenset[str]]:
    """``{changed file: the test files its module-level change needs}`` for *paths*.

    *paths* are changed Python files whose change is not wholly inside function
    bodies; one the per-test map has no row for is left out. A file whose change cannot be
    read as names (:func:`module_change`, :func:`readers`) is left out and keeps
    the graph's answer. Importers come from the index's import edges with the
    names each takes; *read_source* reads an importer as the checkout holds it.
    """
    from ..persistence.crud import tests_covering_files
    from ..test_paths import is_test_related_path
    from .function_hunks import read_sides, run_once_symbols
    from .import_drift import import_resolver, rewiring
    from .test_reach.db import imported_names, load_time_importers

    own = await tests_covering_files(session, repo_id, set(paths)) if paths else {}
    paths = sorted(p for p in paths if own.get(p))
    if not paths:
        return {}
    once = await run_once_symbols(session, repo_id, repo_path)
    if once is None:
        return {}
    texts = read_sides(repo_path, change, paths)
    graph = await load_time_importers(session, repo_id)
    resolve = None
    edges: dict[str, list[Importer]] = {}

    async def importers(module: str) -> list[Importer]:
        if module not in edges:
            rows = await imported_names(session, repo_id, [module])
            edges[module] = [Importer(src, names) for src, _, names in rows if src != module]
        return edges[module]

    def read(path: str) -> str | None:
        if read_source is not None:
            return read_source(path)
        try:
            return (Path(repo_path) / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    found: dict[str, Readers] = {}
    for path in paths:
        old, new = texts.get(path, (None, None))

        def imports_back(path: str = path, old: str | None = old, new: str | None = new) -> bool:
            nonlocal resolve
            resolve = resolve or import_resolver(repo_path, repo_keys)
            gained = rewiring(path, _load_imports(old), _load_imports(new), resolve)
            if gained is None:
                return True
            leads_back = graph.hops([path])
            return any(t in leads_back for t in gained.imports if t != path)

        diff = change.files[path]
        what = module_change(old, new, diff.hunks, imports_back)
        if isinstance(what, str):
            continue
        seen = await readers(
            path,
            what,
            importers,
            read,
            new_text=new,
            is_test=is_test,
            is_support=lambda p: is_test_related_path(p) and not is_test(p),
            once=once,
        )
        if isinstance(seen, Readers):
            found[path] = seen
    if not found:
        return {}
    rows = await tests_covering_files(
        session, repo_id, {m for r in found.values() for m in r.modules}
    )
    users = {
        f: {r["test_file"] or r["test_id"].split("::", 1)[0] for r in hits}
        for f, hits in rows.items()
    }
    return {p: users_of(r, users) for p, r in found.items() if users.get(p)}
