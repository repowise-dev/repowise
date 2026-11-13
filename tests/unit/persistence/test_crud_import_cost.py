"""The read paths' imports must not load the parser or the graph builder.

Every CLI read command imports the analysis CRUD layer; the dead-code analyzer
and the ingestion package's eager re-exports once made that import pull in
networkx and the tree-sitter parser, about two seconds before any query ran.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

HEAVY = (
    "networkx",
    "repowise.core.ingestion.graph",
    "repowise.core.ingestion.parser",
    "repowise.core.analysis.dead_code.analyzer",
)
WALKER = "repowise.core.analysis.health.complexity.walker"


@pytest.mark.parametrize(
    ("module", "heavy"),
    [
        ("repowise.core.persistence.crud.analysis.actions", HEAVY),
        ("repowise.cli.commands.health_cmd.persist", HEAVY),
        # The MCP server is one long-lived process whose graph tools use
        # networkx; it must still not load the parser or the dead-code pass.
        ("repowise.server.mcp_server.tool_overview.tool", HEAVY[1:]),
        # `repowise context` and `repowise symbol`: the complexity walker loads
        # every grammar, and neither command walks a file.
        ("repowise.server.mcp_server.tool_context", (*HEAVY, WALKER)),
        ("repowise.core.analysis.function_test_map", (*HEAVY, WALKER)),
    ],
)
def test_a_read_path_loads_no_parser_or_graph(module: str, heavy: tuple[str, ...]) -> None:
    probe = f"import sys, {module}; print(sorted(m for m in {heavy!r} if m in sys.modules))"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"


def test_lazy_names_resolve_and_are_listed() -> None:
    from repowise.core import ingestion
    from repowise.core.analysis import dead_code
    from repowise.core.analysis.dead_code.analyzer import DeadCodeAnalyzer
    from repowise.core.ingestion.graph import GraphBuilder

    assert {"GraphBuilder", "FileTraverser"} <= set(dir(ingestion))
    assert ingestion.GraphBuilder is GraphBuilder
    assert dead_code.DeadCodeAnalyzer is DeadCodeAnalyzer
    assert "DeadCodeAnalyzer" in dir(dead_code)
    from repowise.core.analysis.health import complexity
    from repowise.core.analysis.health.complexity.walker import walk_file

    assert complexity.walk_file is walk_file
    assert "FileComplexity" in dir(complexity)
    with pytest.raises(AttributeError):
        _ = ingestion.NoSuchName
