"""Which changed files a per-test map answers on its own: every hunk inside a function body.

A per-test coverage map is reliable for a line inside a function or method
body: it runs when a test calls the function, and the test's context records
that. It is not reliable for a module-level line (an import, a constant, a
decorator, a class body, a default argument value): that runs once per
process, at import, and is credited to whichever test imported the module
first. Nor for a body that runs at import: one a module-level statement or a
decorator calls, directly or through other functions.

:func:`function_scoped_lines` names the changed files whose every hunk sits
inside function bodies that do not run at import, with the body lines of the
functions they touch, on the side of the diff the map was measured at. The
tests covering those lines are the tests the change needs; the graph's wider
reach (the import closure, a conftest's broadcast) adds nothing for them.

Ceiling: Python only, read with :mod:`ast` at the measured revision. Another
language keeps the graph's answer; the stored symbol spans with
:func:`repowise.core.callable_spans.body_span` read at that revision are the
upgrade. A body run at import through a call the graph does not record (a
function stored in a table a module-level loop calls) is not seen.
"""

from __future__ import annotations

import ast
import builtins
import json
import re
import sys
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..persistence.index_cache import IndexStampCache
from ..test_paths import is_test_related_path
from .changed_lines import working_tree_label

#: Methods Python calls on a class's or instance's behalf, so a class
#: instantiated, subclassed or hashed at import runs them then.
_IMPLICIT_DUNDERS = re.compile(r"__\w+__")


@dataclass(frozen=True)
class Body:
    """One function's body lines (1-based, inclusive) and its enclosing names, outermost first."""

    start: int
    end: int
    chain: tuple[str, ...]
    # Which of *chain* are functions (the rest are classes).
    defs: tuple[bool, ...]


def function_bodies(tree: ast.AST) -> list[Body]:
    """Every function's body in *tree*, nested ones included.

    A body starts at its first statement (a decorator of that statement
    included) and ends on the function's last line: the ``def`` line, its
    parameters and their defaults, its decorators and its return annotation
    are outside, since they run when the enclosing scope does.
    """
    out: list[Body] = []

    def visit(node: ast.AST, chain: tuple[str, ...], defs: tuple[bool, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                here, kinds = (*chain, child.name), (*defs, True)
                first = child.body[0]
                start = min([first.lineno, *(d.lineno for d in _decorators(first))])
                out.append(Body(start, child.end_lineno or start, here, kinds))
                visit(child, here, kinds)
            elif isinstance(child, ast.ClassDef):
                visit(child, (*chain, child.name), (*defs, False))
            else:
                visit(child, chain, defs)

    visit(tree, (), ())
    return out


def _decorators(node: ast.AST) -> list[ast.expr]:
    return list(getattr(node, "decorator_list", ()))


def innermost(bodies: Iterable[Body], line: int) -> Body | None:
    """The innermost body holding *line*, else ``None`` (module level)."""
    best: Body | None = None
    for body in bodies:
        if body.start <= line <= body.end and (best is None or body.start >= best.start):
            best = body
    return best


@dataclass(frozen=True)
class _Source:
    """One side of a changed file, parsed once."""

    tree: ast.Module
    lines: list[str]
    bodies: list[Body]
    # Lines inside a string spanning several lines: blank or ``#`` there is data.
    in_strings: frozenset[int]

    @classmethod
    def parse(cls, text: str | None) -> _Source | None:
        if text is None:
            return None
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return None
        strings = {
            line
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant | ast.JoinedStr)
            and (node.end_lineno or node.lineno) > node.lineno
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1)
        }
        # Lines as git and ``ast`` count them: ``splitlines`` also breaks on form feeds.
        lines = [line.removesuffix("\r") for line in text.split("\n")]
        return cls(tree, lines, function_bodies(tree), frozenset(strings))

    def code(self, lines: Iterable[int]) -> list[int]:
        """Those of *lines* that hold code, not blanks or comments outside a string."""
        out = []
        for line in lines:
            if 0 < line <= len(self.lines):
                stripped = self.lines[line - 1].strip()
                if line in self.in_strings or (stripped and not stripped.startswith("#")):
                    out.append(line)
        return out

    def inside(self, lines: Iterable[int]) -> bool:
        return all(innermost(self.bodies, line) is not None for line in lines)


@dataclass(frozen=True)
class FileFunctions:
    """A changed file's touched functions on the measured side, and their body lines."""

    path: str
    bodies: tuple[Body, ...]

    @property
    def lines(self) -> set[int]:
        return {n for b in self.bodies for n in range(b.start, b.end + 1)}

    def symbol_ids(self) -> set[str]:
        """The graph ids of each touched function and every function around it.

        A method Python calls implicitly (``__init__``, ``__hash__``...) adds its
        class: a module-level ``Foo()`` is a call to the class in the graph.
        """
        out: set[str] = set()
        for body in self.bodies:
            for i, is_def in enumerate(body.defs):
                prefix = body.chain[: i + 1]
                if is_def:
                    out.add(_symbol_id(self.path, prefix))
                    if i and not body.defs[i - 1] and _IMPLICIT_DUNDERS.fullmatch(prefix[-1]):
                        out.add(_symbol_id(self.path, prefix[:-1]))
        return out


def _symbol_id(path: str, chain: tuple[str, ...]) -> str:
    return "::".join((path, *chain))


def touched_functions(
    path: str, hunks, side: str, old: str | None, new: str | None
) -> FileFunctions | None:
    """*path*'s touched functions on *side* when no hunk changes what runs at import, else ``None``.

    A hunk qualifies when its old lines are inside function bodies of *old*
    and its new lines inside bodies of *new* (a line moved out of a function
    is a module-level change), or when it only adds module-level statements
    that run nothing at import (:func:`_inert_lines`). The functions are those
    on the measured *side* whose bodies hold a line a qualifying hunk touches
    there (for a pure insertion, its two neighbours). A file with none, such as
    one that only adds new functions, keeps the graph's answer.
    """
    before, after = _Source.parse(old), _Source.parse(new)
    if before is None or after is None or not hunks:
        return None
    inert = _inert_lines(before, after, hunks)
    if inert is None:
        return None
    found: dict[tuple[str, ...], Body] = {}
    for hunk in hunks:
        hit = _hunk_functions(before, after, inert, side, hunk)
        if hit is None:
            return None
        found.update(hit)
    return FileFunctions(path, tuple(found.values())) if found else None


def _hunk_functions(
    before: _Source, after: _Source, inert: set[int], side: str, hunk
) -> dict[tuple[str, ...], Body] | None:
    """The functions one hunk touches on *side*; ``None`` when it changes code outside them.

    Empty for a hunk of blank lines, comments or new inert statements.
    """
    old_start, old_count, new_start, new_count = hunk
    removed = before.code(range(old_start, old_start + old_count))
    added = [n for n in after.code(range(new_start, new_start + new_count)) if n not in inert]
    if not (before.inside(removed) and after.inside(added)):
        return None
    if not (removed or added):
        return {}
    measured = before if side == "old" else after
    # A neighbour may be blank or a comment and still sit in the function.
    hit = {
        b.chain: b
        for line in _near(side, hunk, added)
        if (b := innermost(measured.bodies, line)) is not None
    }
    return hit or None  # a change no function holds on the measured side


def _near(side: str, hunk, added: list[int]) -> Iterable[int]:
    """The lines a hunk touches on *side*; for a pure insertion or deletion, its two neighbours."""
    old_start, old_count, new_start, _ = hunk
    if side == "old":
        return range(old_start, old_start + old_count) if old_count else (old_start, old_start + 1)
    return added or (new_start, new_start + 1)


def _inert_lines(before: _Source, after: _Source, hunks) -> set[int] | None:
    """New-side lines of the module-level statements the hunks add that run nothing at import.

    Such a statement lies wholly inside a pure insertion and binds a name the
    old file never mentions (nor a builtin), so no unchanged code can reach
    it: a ``def`` with no decorator, literal defaults and deferred
    annotations; a standard-library import; a constant built from literals.
    ``None`` when the old file star-imports, which could bind any name.
    """
    known = _mentioned(before.tree)
    if known is None:
        return None
    exports = _declares_all(before.tree)
    deferred = _defers_annotations(after.tree)
    binds_re = _imports_re(after.tree)
    inserted = [range(s, s + c) for _, o_c, s, c in hunks if o_c == 0 and c]
    out: set[int] = set()
    for stmt in after.tree.body:
        span = _inserted_span(stmt, inserted)
        names = _inert_names(stmt, deferred, binds_re) if span else None
        if (
            names
            and not (set(names) & known)
            and all(_new_name_is_private(n, stmt, exports) for n in names)
        ):
            out.update(span)
    return out


def _inserted_span(stmt: ast.stmt, inserted: list[range]) -> range | None:
    """*stmt*'s lines, decorators included, when one pure insertion holds them all."""
    end = stmt.end_lineno or stmt.lineno
    first = min([stmt.lineno, *(d.lineno for d in _decorators(stmt))])
    if any(first in r and end in r for r in inserted):
        return range(first, end + 1)
    return None


def _new_name_is_private(name: str, stmt: ast.stmt, exports: bool) -> bool:
    """Whether a new module name stays out of every importer's way.

    A dunder (``__getattr__``, ``__all__``) changes how the module behaves; a
    public def or constant reaches ``from m import *`` unless ``__all__`` limits it.
    """
    if name.startswith("__") and name.endswith("__"):
        return False
    return exports or name.startswith("_") or isinstance(stmt, ast.Import | ast.ImportFrom)


def _declares_all(tree: ast.Module) -> bool:
    return any(
        isinstance(s, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and any(isinstance(t, ast.Name) and t.id == "__all__" for t in _targets(s))
        for s in tree.body
    )


def _targets(stmt: ast.stmt) -> list[ast.expr]:
    return list(stmt.targets) if isinstance(stmt, ast.Assign) else [stmt.target]


def _mentioned(tree: ast.Module) -> set[str] | None:
    """Every name *tree* binds or reads anywhere, and the builtins; ``None`` on a star import."""
    names = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            if node.name == "*":
                return None
            names.add(node.asname or node.name.split(".")[0])
        elif isinstance(node, ast.Global | ast.Nonlocal):
            names.update(node.names)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def _defers_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(s, ast.ImportFrom)
        and s.module == "__future__"
        and any(a.name == "annotations" for a in s.names)
        for s in tree.body
    )


def _imports_re(tree: ast.Module) -> bool:
    return any(
        isinstance(s, ast.Import) and any(a.name == "re" and a.asname is None for a in s.names)
        for s in tree.body
    )


def _inert_names(stmt: ast.stmt, deferred: bool, binds_re: bool) -> list[str] | None:
    """The names *stmt* binds, when running it at import can neither fail nor run repo code."""
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
        return _inert_def(stmt, deferred)
    if isinstance(stmt, ast.Import):
        return _inert_import(stmt)
    if isinstance(stmt, ast.ImportFrom):
        return _inert_import_from(stmt)
    return _inert_constant(stmt, deferred, binds_re)


def _inert_def(stmt: ast.FunctionDef | ast.AsyncFunctionDef, deferred: bool) -> list[str] | None:
    """An undecorated ``def`` with literal defaults and annotations Python does not evaluate."""
    args = stmt.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    annotated = stmt.returns is not None or any(a and a.annotation for a in every)
    defaults = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
    if stmt.decorator_list or (annotated and not deferred):
        return None
    return [stmt.name] if all(_literal(d) for d in defaults) else None


def _inert_import(stmt: ast.Import) -> list[str] | None:
    """``import`` of standard-library modules only."""
    if all(a.name.split(".")[0] in sys.stdlib_module_names for a in stmt.names):
        return [a.asname or a.name.split(".")[0] for a in stmt.names]
    return None


def _inert_import_from(stmt: ast.ImportFrom) -> list[str] | None:
    """``from <stdlib> import name``; a star import could bind any name."""
    root = (stmt.module or "").split(".")[0]
    if stmt.level or root not in sys.stdlib_module_names or root == "__future__":
        return None
    names = [a.asname or a.name for a in stmt.names]
    return None if "*" in names else names


def _inert_constant(stmt: ast.stmt, deferred: bool, binds_re: bool) -> list[str] | None:
    """``NAME = <constant>``, annotated only when annotations are deferred."""
    target, value = None, None
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target, value = stmt.targets[0], stmt.value
    elif isinstance(stmt, ast.AnnAssign) and deferred:
        target, value = stmt.target, stmt.value
    if isinstance(target, ast.Name) and value is not None and _constant(value, binds_re):
        return [target.id]
    return None


def _literal(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


_LITERAL_CONTAINERS = frozenset({"frozenset", "set", "tuple", "list", "dict"})


def _constant(node: ast.expr, binds_re: bool) -> bool:
    """A literal, a builtin container of literals, or ``re.compile`` of a pattern that compiles."""
    if _literal(node):
        return True
    if not isinstance(node, ast.Call) or node.keywords:
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in _LITERAL_CONTAINERS:
        return all(_literal(a) for a in node.args)
    return binds_re and _compiles(node)


def _compiles(call: ast.Call) -> bool:
    """Whether *call* is ``re.compile(<str literal>[, re.X | ...])`` with a pattern that compiles."""
    pattern = _compile_pattern(call)
    flags = _re_flags(call.args[1]) if len(call.args) == 2 else 0
    if pattern is None or flags is None:
        return False
    try:
        re.compile(pattern, flags)
    except re.error:
        return False
    return True


def _compile_pattern(call: ast.Call) -> str | None:
    """The literal pattern of a ``re.compile(pattern[, flags])`` call, else ``None``."""
    func = call.func
    pattern = call.args[0] if call.args else None
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "compile"
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
        and len(call.args) <= 2
        and isinstance(pattern, ast.Constant)
        and isinstance(pattern.value, str)
    ):
        return pattern.value
    return None


def _re_flags(node: ast.expr) -> int | None:
    """The value of ``re.X | re.Y ...``, else ``None``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _re_flags(node.left), _re_flags(node.right)
        return None if left is None or right is None else left | right
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "re"
        and isinstance(flag := getattr(re, node.attr, None), re.RegexFlag)
    ):
        return int(flag)
    return None


def is_python_source(path: str) -> bool:
    """A Python file outside the test tree: a test's own coverage is not measured."""
    return path.endswith(".py") and not is_test_related_path(path)


def read_sides(
    repo_path, change, paths: Collection[str]
) -> dict[str, tuple[str | None, str | None]]:
    """``{path: (text at the base, text at the head)}``, ``None`` for a side git cannot read.

    With no head commit the new side is the working tree, or the index for a
    staged change (the two diffs :func:`~.changed_lines.change_set` makes).
    """
    from .change_health.sources import read_blobs

    paths = sorted(paths)
    if not paths or change.base is None:
        return {p: (None, None) for p in paths}
    working_tree = change.head is None and change.label.endswith(working_tree_label())
    # ``rev:./path`` reads relative to the checkout, which can sit below the top level.
    revs = [change.base] + ([] if working_tree else [change.head or ""])
    blobs = read_blobs(str(repo_path), [(rev, f"./{p}") for p in paths for rev in revs]) or {}

    def text(rev: str, path: str) -> str | None:
        blob = blobs.get((rev, f"./{path}"))
        return None if blob is None else blob.decode("utf-8", errors="replace")

    return {
        p: (
            text(change.base, p),
            _read_text(Path(repo_path) / p) if working_tree else text(change.head or "", p),
        )
        for p in paths
    }


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


#: Decorators whose function runs once for every later caller in the process.
_MEMO_DECORATORS = frozenset({"lru_cache", "cache", "cached_property"})
_DECORATOR_LINE = re.compile(r"^\s*@\s*((?:\w+\.)*(\w+))\s*(\(.*|#.*)?$")
#: Fixture scopes that set up once per test file at most. A test file runs
#: whole when one of its tests covers a change, so the tests sharing such a
#: fixture with the one coverage credited run too; a wider scope spans files.
_FILE_SCOPED = re.compile(r"""scope\s*=\s*["'](?:function|class|module)["']""")

_RUN_ONCE = IndexStampCache(4)


async def run_once_symbols(session, repo_id: str, repo_path) -> frozenset[str] | None:
    """Python symbols that may run once per test process rather than once per calling test.

    Coverage credits such code to the one test that ran it first, while every
    later test depends on what it did. Seeds: what module-level code and
    class bodies call (a call under ``if __name__ == "__main__":`` excluded),
    functions used as decorators, memoized functions, fixtures shared across
    test files (session or package scope) and a conftest's pytest hooks. Then
    everything they call, and the implicit methods (``__init__``...) of every
    class among them. Read once per index state and working tree: the
    decorators and ``__main__`` blocks are read from disk, which a long-lived
    server sees change between indexes. ``None`` when the decorators cannot be
    listed.

    Ceiling: a cache written by hand (a module-level dict) is not seen.
    """
    key = await _RUN_ONCE.key(session, repo_id, "run_once", str(repo_path), _tree_stamp(repo_path))
    if key in _RUN_ONCE:
        return _RUN_ONCE.get(key)
    calls, methods, module_calls = await _execution_edges(session, repo_id)
    by_file, by_name, classes, hooks = await _python_symbols(session, repo_id)
    decorated = _decorator_seeds(repo_path, by_file, by_name)
    if decorated is None:
        return None
    seeds = _module_seeds(repo_path, module_calls) | hooks | decorated
    seeds |= {t for c in classes for t in calls.get(c, ())}
    return _RUN_ONCE.remember(key, _reach(seeds, calls, methods))


async def _execution_edges(session, repo_id: str):
    """``(calls, methods, module_calls)``: who calls whom, each class's methods, and the
    calls made by a Python module's top-level code as ``(path, target, call lines)``."""
    from sqlalchemy import text

    from ..ingestion.models import EXECUTION_EDGE_TYPES

    types = sorted({*EXECUTION_EDGE_TYPES, "has_method"})
    params = {"repo_id": repo_id, **{f"t{i}": t for i, t in enumerate(types)}}
    rows = await session.execute(
        text(
            "SELECT source_node_id, target_node_id, edge_type, call_lines_json FROM graph_edges "
            "WHERE repository_id = :repo_id AND edge_type IN ("
            + ",".join(f":t{i}" for i in range(len(types)))
            + ")"
        ),
        params,
    )
    calls: dict[str, set[str]] = {}
    methods: dict[str, set[str]] = {}
    module_calls: list[tuple[str, str, str]] = []
    for source, target, edge_type, lines in rows:
        if edge_type == "has_method":
            methods.setdefault(source, set()).add(target)
            continue
        calls.setdefault(source, set()).add(target)
        if source.endswith(".py::__module__"):
            module_calls.append((source.removesuffix("::__module__"), target, lines))
    return calls, methods, module_calls


async def _python_symbols(session, repo_id: str):
    """``(by_file, by_name, classes, hooks)`` over the indexed Python symbols: each file's
    ``(start line, id)`` pairs, ids by name, the classes and a conftest's pytest hooks."""
    from sqlalchemy import text

    symbols = await session.execute(
        text(
            "SELECT symbol_id, file_path, name, kind, start_line FROM wiki_symbols "
            "WHERE repository_id = :repo_id AND file_path LIKE '%.py'"
        ),
        {"repo_id": repo_id},
    )
    by_file: dict[str, list[tuple[int, str]]] = {}
    by_name: dict[str, set[str]] = {}
    classes: set[str] = set()
    hooks: set[str] = set()
    for symbol_id, path, name, kind, start in symbols:
        by_file.setdefault(path, []).append((start, symbol_id))
        by_name.setdefault(name, set()).add(symbol_id)
        if kind == "class":
            classes.add(symbol_id)
        elif name.startswith("pytest_") and path.rsplit("/", 1)[-1] == "conftest.py":
            hooks.add(symbol_id)
    return by_file, by_name, classes, hooks


def _reach(
    seeds: set[str], calls: Mapping[str, set[str]], methods: Mapping[str, set[str]]
) -> frozenset[str]:
    """*seeds*, everything they call, and the implicit methods of every class reached."""
    reached: set[str] = set()
    frontier = list(seeds)
    while frontier:
        node = frontier.pop()
        if node in reached:
            continue
        reached.add(node)
        frontier.extend(calls.get(node, ()))
        frontier.extend(
            m for m in methods.get(node, ()) if _IMPLICIT_DUNDERS.fullmatch(m.rsplit("::", 1)[-1])
        )
    return frozenset(reached)


def _tree_stamp(repo_path) -> str | None:
    """A fingerprint of the checkout's Python files: ``HEAD``, the diff against it, untracked files.

    ``None`` when git cannot say, which a cache then keys like any other value.
    """
    import hashlib

    from .doc_drift.suggest import git_run

    root = Path(repo_path)
    parts = []
    for args in (
        ("rev-parse", "HEAD"),
        ("diff", "HEAD", "--no-ext-diff", "--", "*.py"),
        ("ls-files", "-o", "--exclude-standard", "-z", "--", "*.py"),
    ):
        ran = git_run(root, *args, pathspec_magic=True)
        if ran is None or ran[0] != 0:
            return None
        parts.append(ran[1])
    digest = hashlib.sha1(b"\0".join(parts), usedforsecurity=False)
    for name in parts[2].split(b"\0"):
        if name:
            try:
                st = (root / name.decode("utf-8", "replace")).stat()
            except OSError:
                continue
            digest.update(f"{st.st_mtime_ns}:{st.st_size}".encode())
    return digest.hexdigest()


def _module_seeds(repo_path, module_calls: list[tuple[str, str, str]]) -> set[str]:
    """What module-level code calls, leaving out calls under ``if __name__ == "__main__":``."""
    guarded: dict[str, list[range]] = {}
    out: set[str] = set()
    for path, target, lines_json in module_calls:
        if path not in guarded:
            guarded[path] = _main_blocks(Path(repo_path) / path)
        try:
            lines = [int(n) for n in json.loads(lines_json or "[]")]
        except (TypeError, ValueError):
            lines = []
        if not lines or not all(any(n in r for r in guarded[path]) for n in lines):
            out.add(target)
    return out


def _main_blocks(path: Path) -> list[range]:
    """Line ranges of the module-level ``if __name__ == "__main__":`` blocks in *path*."""
    text = _read_text(path)
    if text is None or "__main__" not in text:
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    return [
        range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1)
        for stmt in tree.body
        if _is_main_guard(stmt)
    ]


def _is_main_guard(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.If)
        and isinstance(test := stmt.test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def _decorator_seeds(
    repo_path, by_file: Mapping[str, list[tuple[int, str]]], by_name: Mapping[str, set[str]]
) -> set[str] | None:
    """Functions used as decorators, and the functions memoized or shared past one test.

    One ``git grep`` of every decorator line. A decorator is matched to repo
    symbols by name; a memo or a shared fixture to the symbol it decorates,
    the first declared below it. ``None`` when git cannot list them.
    """
    from .doc_drift.suggest import git_run

    ran = git_run(
        Path(repo_path), "grep", "-n", "-E", "-e", r"^[[:space:]]*@", "--", "*.py",
        pathspec_magic=True, timeout=60,
    )  # fmt: skip
    if ran is None or ran[0] not in (0, 1):
        return None
    out: set[str] = set()
    for path, number, name, args in _decorator_lines(ran[1]):
        if name in _MEMO_DECORATORS or _shared_fixture(name, args):
            below = [(start, s) for start, s in by_file.get(path, ()) if start > number]
            if below:
                out.add(min(below)[1])
        else:
            out |= by_name.get(name, set())
    return out


def _decorator_lines(grep_output: bytes) -> Iterator[tuple[str, int, str, str]]:
    """``(path, line, decorator name, what follows it)`` for each ``git grep -n`` row."""
    for row in grep_output.decode("utf-8", errors="replace").splitlines():
        path, _, rest = row.partition(":")
        number, _, line = rest.partition(":")
        match = _DECORATOR_LINE.match(line)
        if match is not None and number.isdigit():
            yield path, int(number), match.group(2), match.group(3) or ""


def _shared_fixture(name: str, args: str) -> bool:
    """A fixture whose scope spans test files, or may: a split decorator names it on a later line."""
    return (
        name == "fixture"
        and bool(args)
        and not _FILE_SCOPED.search(args)
        and ("scope" in args or not args.rstrip().endswith(")"))
    )


async def function_scoped_lines(
    session,
    repo_id: str,
    repo_path,
    change,
    side: str | None,
    asked: Mapping[str, set[int] | None],
) -> dict[str, FileFunctions]:
    """The changed files the per-test map answers alone, with their touched functions.

    *side* is where the map was measured (``"old"`` or ``"new"``); without one
    the map cannot be matched by line and nothing qualifies. *asked* are the
    files the map is queried for, by line. A file touching a function that may
    run once per process (:func:`run_once_symbols`) keeps the graph's answer.
    """
    if side is None:
        return {}
    paths = [
        p
        for p, lines in asked.items()
        if lines is not None and p in change.files and is_python_source(p)
    ]
    if not paths:
        return {}
    texts = read_sides(repo_path, change, paths)
    found = {
        p: f
        for p in paths
        if (f := touched_functions(p, change.files[p].hunks, side, *texts[p])) is not None
    }
    if not found:
        return {}
    once = await run_once_symbols(session, repo_id, repo_path)
    if once is None:
        return {}
    return {p: f for p, f in found.items() if not (f.symbol_ids() & once)}
