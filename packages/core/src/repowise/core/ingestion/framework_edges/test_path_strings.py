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


def path_string_targets(path: str, text: bytes, path_set: set[str]) -> list[str]:
    """Indexed code files *text*, the source of *path*, names relative to its directory."""
    base = posixpath.dirname(path)
    found: dict[str, None] = {}
    for match in _PATH_LITERAL_RE.finditer(text):
        spec = match.group(1).decode("ascii", errors="ignore")
        target = posixpath.normpath(posixpath.join(base, spec))
        if target in path_set and _is_helper(target) and target != path:
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
