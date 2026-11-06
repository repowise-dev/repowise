"""Code files a test names by a path relative to itself.

A test that starts a helper as a subprocess or loads it by file names it with
a string, not an import: ``Path(__file__).parent / "_mock_server.py"``,
``path.join(__dirname, "fixture-server.ts")``, ``[sys.executable, script]``.
The helper's users are then invisible to the graph, so a change reaching it
has no test to stand for it. Each such string becomes an edge from the test to
the file.

Only a string that resolves against the test's own directory and names an
indexed code file counts: that is the shape ``__file__`` / ``__dirname``
anchoring produces, and it keeps a path that is only mentioned (an expected
value, a log line) from linking to a file that merely shares a name elsewhere.
Ceiling: a path split into several string arguments
(``os.path.join(here, "helpers", "server.py")``) or written relative to the
working directory is not read. Upgrade path: join adjacent literal arguments of
a path-join call before resolving.
"""

from __future__ import annotations

import ast
import posixpath
import re
from typing import TYPE_CHECKING, Any

from repowise.core.pyparse import parse_python

from ...test_paths import is_test_path
from ..resolvers import ResolverContext
from ..source_text import source_bytes
from .base import DetectionContext, FrameworkHandler, _add_edge_if_new

if TYPE_CHECKING:
    import networkx as nx

# Stamped on the edge so consumers that mean "imports" can tell it apart.
PATH_STRING_HINT = "test_path_string"

# Extensions a runner or interpreter executes; a data file named by path is
# read, not run, and is no route.
_CODE_EXTS = (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
# Loaded for a whole directory already, and the names tests most often give a
# scratch file they write (``tmp_path / "__init__.py"``).
_DIR_SCOPED = ("__init__.py", "conftest.py")
_PATH_LITERAL_RE = re.compile(
    rb"""["'`]((?:[\w@~+\-]+/|\.{1,2}/)*[\w@~+.\-]+\.(?:py|js|jsx|mjs|cjs|ts|tsx|mts|cts))["'`]"""
)


def _is_helper(target: str) -> bool:
    """Not a directory-scoped file, and not test-named: a test already runs on its own."""
    name = posixpath.basename(target)
    return name not in _DIR_SCOPED and not is_test_path(name)


def path_literals(text: bytes) -> list[bytes]:
    """The code-file paths *text* spells as string literals, in order."""
    return [m.group(1) for m in _PATH_LITERAL_RE.finditer(text)]


def _is_file_path_call(node: ast.AST) -> bool:
    """Matches Path(__file__) or pathlib.Path(__file__)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    is_path = (
        isinstance(func, ast.Name) and func.id == "Path"
    ) or (
        isinstance(func, ast.Attribute)
        and func.attr == "Path"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pathlib"
    )
    if not is_path:
        return False
    return (
        len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "__file__"
    )


def _strip_resolve(node: ast.AST) -> ast.AST | None:
    """Unwraps a zero-argument .resolve() or .absolute() call."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("resolve", "absolute")
        and not node.args
    ):
        return node.func.value
    return None


def _get_anchor_n(node: ast.AST) -> int | None:
    """Return ancestor index n if node anchors on __file__, else None.

    n=0 is the directory containing the file (Path(__file__).parent).
    n=1 is 1 directory above, etc. Matches Path(__file__).parents[n],
    Path(__file__).parent.parent..., and .resolve().
    """
    unwrapped = _strip_resolve(node)
    if unwrapped is not None:
        node = unwrapped
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "parents"
    ):
        base = node.value.value
        inner = _strip_resolve(base)
        if inner is not None:
            base = inner
        if (
            _is_file_path_call(base)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
            and node.slice.value >= 0
        ):
            return node.slice.value
    k = 0
    curr = node
    while isinstance(curr, ast.Attribute) and curr.attr == "parent":
        k += 1
        curr = curr.value
    if k > 0:
        inner = _strip_resolve(curr)
        if inner is not None:
            curr = inner
        if _is_file_path_call(curr):
            return k - 1
    return None


def _climb_ancestor(test_path: str, n: int) -> str | None:
    """Resolve an ancestor of *test_path*'s directory by *n* steps up.

    n=0 is the directory containing test_path (same as Path(__file__).parent).
    n=1 is 1 level above that, etc. Returns '' for repository root.
    Returns None if n < 0 or n climbs past the repository root.
    """
    if n < 0:
        return None
    dir_part = posixpath.dirname(test_path)
    if not dir_part:
        return "" if n == 0 else None
    parts = dir_part.split("/")
    if n > len(parts):
        return None
    return "/".join(parts[: len(parts) - n])


def _unroll_div_chain(node: ast.AST) -> tuple[ast.AST, list[ast.AST]]:
    """Unroll left-associative Division BinOp tree into (base, [right_nodes])."""
    chain = []
    curr = node
    while isinstance(curr, ast.BinOp) and isinstance(curr.op, ast.Div):
        chain.append(curr.right)
        curr = curr.left
    chain.reverse()
    return curr, chain


class _Scope:
    def __init__(self, is_class: bool = False) -> None:
        self.bindings: dict[str, str | None] = {}
        self.is_class: bool = is_class
        self.globals: set[str] = set()


def _bind_function_args(scope: _Scope, args: ast.arguments) -> None:
    def _bind_arg(arg: ast.arg | None) -> None:
        if arg is not None:
            scope.bindings[arg.arg] = None

    for a in getattr(args, "posonlyargs", []):
        _bind_arg(a)
    for a in args.args:
        _bind_arg(a)
    if args.vararg:
        _bind_arg(args.vararg)
    for a in args.kwonlyargs:
        _bind_arg(a)
    if args.kwarg:
        _bind_arg(args.kwarg)


def python_ancestor_targets(test_path: str, text: bytes, path_set: set[str]) -> list[str]:
    """Indexed code files *test_path* names via Path(__file__) ancestor anchors or variables."""
    try:
        tree = parse_python(text)
    except Exception:
        return []

    scopes: list[_Scope] = [_Scope()]
    found: dict[str, None] = {}

    def resolve_name(name: str) -> str | None:
        if name in scopes[-1].globals:
            return scopes[0].bindings.get(name)
        for idx in range(len(scopes) - 1, -1, -1):
            scope = scopes[idx]
            if idx < len(scopes) - 1 and scope.is_class:
                continue
            if name in scope.bindings:
                return scope.bindings[name]
        return None

    def resolve_expr(node: ast.AST) -> str | None:
        anchor_n = _get_anchor_n(node)
        if anchor_n is not None:
            return _climb_ancestor(test_path, anchor_n)
        if isinstance(node, ast.Name):
            return resolve_name(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            base, chain = _unroll_div_chain(node)
            prefix = resolve_expr(base)
            if prefix is None:
                return None
            rel_parts: list[str] = []
            for part in chain:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    rel_parts.append(part.value)
                else:
                    return None
            if not rel_parts:
                return prefix
            rel_path = posixpath.normpath(posixpath.join(*rel_parts))
            return posixpath.normpath(posixpath.join(prefix, rel_path)) if prefix else rel_path
        return None

    def check_candidate(candidate: str | None) -> None:
        if (
            candidate
            and candidate.endswith(_CODE_EXTS)
            and candidate in path_set
            and _is_helper(candidate)
            and candidate != test_path
        ):
            found[candidate] = None

    class Visitor(ast.NodeVisitor):
        def _bind_or_invalidate(self, target: ast.AST, prefix: str | None) -> None:
            if isinstance(target, ast.Name):
                if target.id in scopes[-1].globals:
                    scopes[0].bindings[target.id] = prefix
                else:
                    scopes[-1].bindings[target.id] = prefix
            elif isinstance(target, (ast.Tuple, ast.List)):
                for elt in target.elts:
                    self._bind_or_invalidate(elt, None)
            elif isinstance(target, ast.Starred):
                self._bind_or_invalidate(target.value, None)

        def visit_Assign(self, node: ast.Assign) -> None:
            prefix = resolve_expr(node.value)
            for target in node.targets:
                self._bind_or_invalidate(target, prefix)
            if prefix is not None:
                check_candidate(prefix)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            prefix = resolve_expr(node.value) if node.value is not None else None
            self._bind_or_invalidate(node.target, prefix)
            if prefix is not None:
                check_candidate(prefix)
            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            self._bind_or_invalidate(node.target, None)
            self.generic_visit(node)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
            prefix = resolve_expr(node.value)
            self._bind_or_invalidate(node.target, prefix)
            if prefix is not None:
                check_candidate(prefix)
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:
            self.visit(node.iter)
            self._bind_or_invalidate(node.target, None)
            for stmt in node.body:
                self.visit(stmt)
            for stmt in node.orelse:
                self.visit(stmt)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
            self.visit(node.iter)
            self._bind_or_invalidate(node.target, None)
            for stmt in node.body:
                self.visit(stmt)
            for stmt in node.orelse:
                self.visit(stmt)

        def visit_With(self, node: ast.With) -> None:
            for item in node.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    self._bind_or_invalidate(item.optional_vars, None)
            for stmt in node.body:
                self.visit(stmt)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
            for item in node.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    self._bind_or_invalidate(item.optional_vars, None)
            for stmt in node.body:
                self.visit(stmt)

        def visit_Global(self, node: ast.Global) -> None:
            scopes[-1].globals.update(node.names)
            self.generic_visit(node)

        def visit_Delete(self, node: ast.Delete) -> None:
            for target in node.targets:
                self._bind_or_invalidate(target, None)
            self.generic_visit(node)

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            for dec in node.decorator_list:
                self.visit(dec)
            for tp in getattr(node, "type_params", []):
                self.visit(tp)
            self.visit(node.args)
            if node.returns:
                self.visit(node.returns)
            func_scope = _Scope()
            for tp in getattr(node, "type_params", []):
                if hasattr(tp, "name"):
                    func_scope.bindings[tp.name] = None
            _bind_function_args(func_scope, node.args)
            scopes.append(func_scope)
            try:
                for stmt in node.body:
                    self.visit(stmt)
            finally:
                scopes.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.visit(node.args)
            lam_scope = _Scope()
            _bind_function_args(lam_scope, node.args)
            scopes.append(lam_scope)
            try:
                self.visit(node.body)
            finally:
                scopes.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            for dec in node.decorator_list:
                self.visit(dec)
            for tp in getattr(node, "type_params", []):
                self.visit(tp)
            for b in node.bases:
                self.visit(b)
            for kw in node.keywords:
                self.visit(kw)
            class_scope = _Scope(is_class=True)
            scopes.append(class_scope)
            try:
                for stmt in node.body:
                    self.visit(stmt)
            finally:
                scopes.pop()

        def _visit_comprehension(
            self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
        ) -> None:
            comp_scope = _Scope()
            first_gen = node.generators[0] if node.generators else None
            if first_gen:
                self.visit(first_gen.iter)
            scopes.append(comp_scope)
            try:
                for idx, gen in enumerate(node.generators):
                    if idx > 0:
                        self.visit(gen.iter)
                    self._bind_or_invalidate(gen.target, None)
                    for if_expr in gen.ifs:
                        self.visit(if_expr)
                if isinstance(node, ast.DictComp):
                    self.visit(node.key)
                    self.visit(node.value)
                else:
                    self.visit(node.elt)
            finally:
                scopes.pop()

        def visit_ListComp(self, node: ast.ListComp) -> None:
            self._visit_comprehension(node)

        def visit_SetComp(self, node: ast.SetComp) -> None:
            self._visit_comprehension(node)

        def visit_DictComp(self, node: ast.DictComp) -> None:
            self._visit_comprehension(node)

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
            self._visit_comprehension(node)

        def visit_BinOp(self, node: ast.BinOp) -> None:
            if isinstance(node.op, ast.Div):
                prefix = resolve_expr(node)
                check_candidate(prefix)
            self.generic_visit(node)

    Visitor().visit(tree)
    return list(found)


def path_string_targets(path: str, text: bytes, path_set: set[str]) -> list[str]:
    """Indexed code files *text*, the source of *path*, names relative to its directory
    or through Path(__file__) ancestor anchors.
    """
    base = posixpath.dirname(path)
    found: dict[str, None] = {}
    for literal in path_literals(text):
        spec = literal.decode("ascii", errors="ignore")
        target = posixpath.normpath(posixpath.join(base, spec))
        if target in path_set and _is_helper(target) and target != path:
            found[target] = None
    if path.endswith(".py"):
        for target in python_ancestor_targets(path, text, path_set):
            found[target] = None
    return list(found)


def _add_path_string_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    count = 0
    for path in sorted(path_set):
        if not (path.endswith(_CODE_EXTS) and parsed_files[path].file_info.is_test):
            continue
        text = source_bytes(path, parsed_files[path].file_info.abs_path, ctx.source_map)
        for target in path_string_targets(path, text, path_set):
            if _add_edge_if_new(graph, path, target):
                graph[path][target]["hint_source"] = PATH_STRING_HINT
                count += 1
    return count


class _PathStringHandler:
    """Tests run helpers they name by a path relative to themselves."""

    def detect(self, dctx: DetectionContext) -> bool:
        return True

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_path_string_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_PathStringHandler()]
