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


def python_ancestor_targets(test_path: str, text: bytes, path_set: set[str]) -> list[str]:
    """Indexed code files *test_path* names via Path(__file__) ancestor anchors or variables."""
    try:
        tree = ast.parse(text)
    except Exception:
        return []

    vars_to_prefix: dict[str, str] = {}
    found: dict[str, None] = {}

    def resolve_expr(node: ast.AST) -> str | None:
        anchor_n = _get_anchor_n(node)
        if anchor_n is not None:
            return _climb_ancestor(test_path, anchor_n)
        if isinstance(node, ast.Name):
            return vars_to_prefix.get(node.id)
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
        def visit_Assign(self, node: ast.Assign) -> None:
            prefix = resolve_expr(node.value)
            if prefix is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        vars_to_prefix[target.id] = prefix
                check_candidate(prefix)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None:
                prefix = resolve_expr(node.value)
                if prefix is not None:
                    if isinstance(node.target, ast.Name):
                        vars_to_prefix[node.target.id] = prefix
                    check_candidate(prefix)
            self.generic_visit(node)

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
