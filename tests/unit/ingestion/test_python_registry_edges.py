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


def _parsed_files(tmp_path: Path, sources: dict[str, str]) -> dict[str, ParsedFile]:
    """Parse *sources* in memory; *tmp_path* only supplies the absolute paths."""
    parser = ASTParser()
    parsed: dict[str, ParsedFile] = {}
    for relative, source in sources.items():
        info = FileInfo(
            path=relative,
            abs_path=str(tmp_path / relative),
            language="python",
            size_bytes=len(source.encode()),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[relative] = parser.parse_file(info, source.encode())
    return parsed


def _context(
    tmp_path: Path,
    parsed: dict[str, ParsedFile],
    sources: dict[str, str],
    graph: nx.DiGraph,
) -> ResolverContext:
    return ResolverContext(
        path_set=set(parsed),
        stem_map={},
        graph=graph,
        repo_path=tmp_path,
        source_map={path: source.encode() for path, source in sources.items()},
    )


def test_registry_tables_and_consumers_are_marked_as_module_registrations(tmp_path: Path):
    sources = {
        "dialects/python.py": "DIALECT = object()\n",
        "dialects/__init__.py": (
            'from .python import DIALECT as PYTHON\n_REGISTER = (("python", PYTHON),)\n'
        ),
        "health.py": (
            "from .dialects import PERF_DIALECTS\n"
            "def inspect():\n"
            '    return PERF_DIALECTS.get("python")\n'
        ),
        "registry.py": "def register_lazy(name, target):\n    pass\n",
        "tools/__init__.py": "",
        "tools/reader.py": (
            "from ..registry import cli_registry as registry\n"
            "@registry.register()\n"
            "def read():\n"
            "    pass\n"
        ),
        "cli.py": (
            "from .registry import cli_registry\n"
            'cli_registry.register_lazy("python", "dialects.python:DIALECT")\n'
        ),
    }
    parsed = _parsed_files(tmp_path, sources)
    graph = nx.DiGraph()
    for path in parsed:
        graph.add_node(path, node_type="file")
    graph.add_edge("dialects/__init__.py", "dialects/python.py", edge_type="imports")
    graph.add_edge("health.py", "dialects/__init__.py", edge_type="imports")
    context = _context(tmp_path, parsed, sources, graph)

    add_framework_edges(graph, parsed, context, tech_stack=[])

    assert graph.edges["dialects/__init__.py", "dialects/python.py"]["hint_source"] == (
        MODULE_REGISTRATION_HINT
    )
    assert graph.edges["health.py", "dialects/__init__.py"]["hint_source"] == (
        MODULE_REGISTRATION_HINT
    )
    assert graph.edges["cli.py", "dialects/python.py"]["hint_source"] == (MODULE_REGISTRATION_HINT)
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


def test_unrelated_register_calls_and_function_imports_stay_at_import_tier(
    tmp_path: Path,
):
    sources = {
        "registry.py": "",
        "hooks.py": "def shutdown():\n    pass\ndef get_tools():\n    pass\nevent_handlers = []\n",
        "caller.py": (
            "import atexit\n"
            "from .hooks import shutdown, get_tools, event_handlers\n"
            "atexit.register(shutdown)\n"
        ),
    }
    parsed = _parsed_files(tmp_path, sources)
    graph = nx.DiGraph()
    for path in parsed:
        graph.add_node(path, node_type="file")
    graph.add_edge("caller.py", "hooks.py", edge_type="imports")
    context = _context(tmp_path, parsed, sources, graph)

    add_framework_edges(graph, parsed, context, tech_stack=[])

    assert graph.edges["caller.py", "hooks.py"].get("hint_source") is None
    assert call_graph_from_graph(graph).registrations.get("caller.py") is None


def test_attribute_registry_receivers_create_registration_edges(tmp_path: Path):
    sources = {
        "handlers.py": "def handler():\n    pass\n",
        "caller.py": (
            "from .handlers import handler\n"
            "class App:\n"
            "    def register_local(self):\n"
            "        self._registry.register(handler)\n"
            "    def register_plugin(self):\n"
            "        app.state.plugins.register(handler)\n"
        ),
    }
    parsed = _parsed_files(tmp_path, sources)
    graph = nx.DiGraph()
    for path in parsed:
        graph.add_node(path, node_type="file")
    graph.add_edge("caller.py", "handlers.py", edge_type="imports")
    context = _context(tmp_path, parsed, sources, graph)

    add_framework_edges(graph, parsed, context, tech_stack=[])

    assert graph.edges["caller.py", "handlers.py"]["hint_source"] == (MODULE_REGISTRATION_HINT)
    assert call_graph_from_graph(graph).registrations == {
        "caller.py": ("handlers.py",),
    }
