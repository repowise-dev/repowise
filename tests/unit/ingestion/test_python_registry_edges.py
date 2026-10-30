from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.analysis.test_reachability import call_graph_from_graph
from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import (
    MODULE_REGISTRATION_HINT,
    FileInfo,
    ParsedFile,
)
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _parsed_files(root: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    parsed: dict[str, ParsedFile] = {}
    for path in root.rglob("*.py"):
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        info = FileInfo(
            path=relative,
            abs_path=str(path.resolve()),
            language="python",
            size_bytes=path.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[relative] = parser.parse_file(info, path.read_bytes())
    return parsed


def test_registry_tables_and_consumers_are_marked_as_module_registrations(tmp_path: Path):
    (tmp_path / "dialects").mkdir()
    (tmp_path / "dialects" / "python.py").write_text("DIALECT = object()\n")
    (tmp_path / "dialects" / "__init__.py").write_text(
        "from .python import DIALECT as PYTHON\n"
        "_REGISTER = ((\"python\", PYTHON),)\n"
    )
    (tmp_path / "health.py").write_text(
        "from .dialects import PERF_DIALECTS\n"
        "def inspect():\n"
        "    return PERF_DIALECTS.get(\"python\")\n"
    )
    (tmp_path / "registry.py").write_text("def register_lazy(name, target):\n    pass\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "__init__.py").write_text("")
    (tmp_path / "tools" / "reader.py").write_text(
        "from ..registry import cli_registry as registry\n"
        "@registry.register()\n"
        "def read():\n"
        "    pass\n"
    )
    (tmp_path / "cli.py").write_text(
        "from .registry import cli_registry\n"
        "cli_registry.register_lazy(\"python\", \"dialects.python:DIALECT\")\n"
    )
    parsed = _parsed_files(tmp_path)
    graph = nx.DiGraph()
    for path in parsed:
        graph.add_node(path, node_type="file")
    graph.add_edge("dialects/__init__.py", "dialects/python.py", edge_type="imports")
    graph.add_edge("health.py", "dialects/__init__.py", edge_type="imports")
    paths = set(parsed)
    context = ResolverContext(
        path_set=paths,
        stem_map={},
        graph=graph,
        repo_path=tmp_path,
    )

    add_framework_edges(graph, parsed, context, tech_stack=[])

    assert graph.edges["dialects/__init__.py", "dialects/python.py"]["hint_source"] == (
        MODULE_REGISTRATION_HINT
    )
    assert graph.edges["health.py", "dialects/__init__.py"]["hint_source"] == (
        MODULE_REGISTRATION_HINT
    )
    assert graph.edges["cli.py", "dialects/python.py"]["hint_source"] == (
        MODULE_REGISTRATION_HINT
    )
    assert graph.edges["registry.py", "tools/reader.py"]["hint_source"] == (
        MODULE_REGISTRATION_HINT
    )
    assert not graph.has_edge("tools/reader.py", "registry.py")
    assert call_graph_from_graph(graph).registrations == {
        "dialects/__init__.py": ("dialects/python.py",),
        "health.py": ("dialects/__init__.py",),
        "cli.py": ("dialects/python.py", "registry.py"),
        "registry.py": ("tools/reader.py",),
    }
