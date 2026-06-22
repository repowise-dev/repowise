"""Regression tests: Python ingestion fixes for dead-code false positives.

Each test reproduces a real false positive the dead-code analyzer reported
on a monorepo (``packages/*/src`` layout) and asserts the symbol is no
longer flagged. They drive the *real* parser, GraphBuilder and analyzer —
no mocking of the resolution path — so they verify the end-to-end behaviour
without running a full ``repowise init``.

The four classes of false positive, and the fix each exercises:

* base class reached only through an absolute cross-module import →
  source-root-aware module index (``languages/python_modules.py``);
* model reached through a *cross-package* absolute import (server → core) →
  same module index;
* submodule used via an aliased namespace import
  (``from . import levels as _levels``) → exported-name binding fix;
* class loaded by string via ``importlib`` → Python dynamic-import hints.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.dynamic_hints.python_imports import PythonDynamicHints
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.languages.python_modules import (
    build_python_module_index,
    dotted_module_for,
)
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _file_info(path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="python",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _graph_from_sources(sources: dict[str, str]) -> nx.DiGraph:
    """Parse each ``path -> source`` and build a real dependency graph."""
    builder = GraphBuilder()
    for path, src in sources.items():
        parsed = _PARSER.parse_file(_file_info(path), src.encode("utf-8"))
        builder.add_file(parsed)
    return builder.build()


def _unused_export_names(graph: nx.DiGraph) -> set[str]:
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze({"detect_unreachable_files": False, "detect_zombie_packages": False})
    return {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}


def _unreachable_paths(graph: nx.DiGraph) -> set[str]:
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze({"detect_unused_exports": False, "detect_zombie_packages": False})
    return {f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}


# ---------------------------------------------------------------------------
# Module index — the structural core of the resolution fix
# ---------------------------------------------------------------------------


class TestPythonModuleIndex:
    def test_dotted_name_strips_monorepo_source_root(self) -> None:
        paths = {
            "packages/core/src/myrepo/__init__.py",
            "packages/core/src/myrepo/sub/__init__.py",
            "packages/core/src/myrepo/sub/mod.py",
        }
        assert dotted_module_for("packages/core/src/myrepo/sub/mod.py", paths) == "myrepo.sub.mod"

    def test_init_maps_to_package_name(self) -> None:
        paths = {
            "src/pkg/__init__.py",
            "src/pkg/sub/__init__.py",
        }
        assert dotted_module_for("src/pkg/sub/__init__.py", paths) == "pkg.sub"

    def test_flat_layout(self) -> None:
        paths = {"app/__init__.py", "app/mod.py"}
        assert dotted_module_for("app/mod.py", paths) == "app.mod"

    def test_namespace_package_under_src(self) -> None:
        """A PEP 420 namespace package (top dir has no ``__init__.py``) under a
        monorepo ``src`` root still yields its full dotted name — the layout
        repowise itself uses (``repowise`` is a namespace package shared by
        the core/server/cli distributions)."""
        paths = {
            # note: NO ``packages/core/src/ns/__init__.py`` — namespace package
            "packages/core/src/ns/core/__init__.py",
            "packages/core/src/ns/core/mod.py",
        }
        assert dotted_module_for("packages/core/src/ns/core/mod.py", paths) == "ns.core.mod"

    def test_package_literally_named_src_is_not_a_root(self) -> None:
        paths = {"src/__init__.py", "src/mod.py"}
        # ``src`` has an __init__.py, so it is a real package, not a root.
        assert dotted_module_for("src/mod.py", paths) == "src.mod"

    def test_loose_module_without_src_is_unindexed(self) -> None:
        paths = {"vendor/util.py"}
        assert dotted_module_for("vendor/util.py", paths) is None

    def test_index_round_trips_dotted_to_path(self) -> None:
        paths = {
            "packages/a/src/lib/__init__.py",
            "packages/a/src/lib/core.py",
        }
        idx = build_python_module_index(paths)
        assert idx["lib.core"] == "packages/a/src/lib/core.py"
        assert idx["lib"] == "packages/a/src/lib/__init__.py"


# ---------------------------------------------------------------------------
# 1. Base class reached only via an absolute import (BaseProvider pattern)
# ---------------------------------------------------------------------------


def test_base_class_imported_absolutely_in_monorepo_not_flagged() -> None:
    sources = {
        "packages/core/src/myrepo/__init__.py": "",
        "packages/core/src/myrepo/providers/__init__.py": "",
        "packages/core/src/myrepo/providers/base.py": (
            "class BaseProvider:\n    def run(self):\n        return 1\n"
        ),
        "packages/core/src/myrepo/providers/anthropic.py": (
            "from myrepo.providers.base import BaseProvider\n\n\n"
            "class AnthropicProvider(BaseProvider):\n    pass\n"
        ),
    }
    graph = _graph_from_sources(sources)

    base = "packages/core/src/myrepo/providers/base.py"
    assert base not in _unreachable_paths(graph)
    assert "BaseProvider" not in _unused_export_names(graph)


# ---------------------------------------------------------------------------
# 2. Model reached via a cross-package absolute import (SecurityFinding)
# ---------------------------------------------------------------------------


def test_cross_package_absolute_import_keeps_symbol_live() -> None:
    sources = {
        "packages/core/src/myrepo/__init__.py": "",
        "packages/core/src/myrepo/persistence/__init__.py": "",
        "packages/core/src/myrepo/persistence/models.py": ("class SecurityFinding:\n    id = 0\n"),
        "packages/server/src/myrepo/server/__init__.py": "",
        "packages/server/src/myrepo/server/routers/__init__.py": "",
        "packages/server/src/myrepo/server/routers/security.py": (
            "from myrepo.persistence.models import SecurityFinding\n\n\n"
            "def list_findings():\n    return SecurityFinding\n"
        ),
    }
    graph = _graph_from_sources(sources)
    assert "SecurityFinding" not in _unused_export_names(graph)


# ---------------------------------------------------------------------------
# 3. Submodule used via aliased namespace import (build_levelN_coros)
# ---------------------------------------------------------------------------


def test_aliased_namespace_import_rescues_module_symbols() -> None:
    sources = {
        "packages/core/src/myrepo/__init__.py": "",
        "packages/core/src/myrepo/gen/__init__.py": "",
        "packages/core/src/myrepo/gen/levels.py": (
            "def build_level4_coros(run):\n    return []\n\n\n"
            "def build_level7_coros(run):\n    return []\n"
        ),
        "packages/core/src/myrepo/gen/orchestrate.py": (
            "from . import levels as _levels\n\n\n"
            "def run(self):\n"
            "    a = _levels.build_level4_coros(self)\n"
            "    b = _levels.build_level7_coros(self)\n"
            "    return a + b\n"
        ),
    }
    graph = _graph_from_sources(sources)
    flagged = _unused_export_names(graph)
    assert "build_level4_coros" not in flagged
    assert "build_level7_coros" not in flagged


def test_aliased_namespace_import_records_exported_module_name() -> None:
    """The import edge carries the *module* name (``levels``), not the alias."""
    sources = {
        "pkg/__init__.py": "",
        "pkg/levels.py": "def f():\n    return 1\n",
        "pkg/orchestrate.py": "from . import levels as _levels\n",
    }
    graph = _graph_from_sources(sources)
    edge = graph.get_edge_data("pkg/orchestrate.py", "pkg/levels.py")
    assert edge is not None, "aliased namespace import should resolve to the submodule"
    assert "levels" in edge.get("imported_names", [])


# ---------------------------------------------------------------------------
# 4. Class loaded by string via importlib (OllamaProvider)
# ---------------------------------------------------------------------------


def test_python_dynamic_hints_emit_edge_for_importlib_string(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "providers").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "providers" / "__init__.py").write_text("")
    (pkg / "providers" / "ollama.py").write_text("class OllamaProvider:\n    pass\n")
    (pkg / "registry.py").write_text(
        "import importlib\n"
        "_PROVIDERS = {'ollama': ('pkg.providers.ollama', 'OllamaProvider')}\n"
        "def get(name):\n"
        "    mod_path, cls = _PROVIDERS[name]\n"
        "    return getattr(importlib.import_module(mod_path), cls)\n"
    )

    edges = PythonDynamicHints().extract(tmp_path)
    pairs = {(e.source, e.target, e.edge_type) for e in edges}
    assert (
        "pkg/registry.py",
        "pkg/providers/ollama.py",
        "dynamic_uses",
    ) in pairs


def test_python_dynamic_hints_ignore_dotted_strings_without_loader(
    tmp_path: Path,
) -> None:
    """A dotted string in a file with no dynamic-import machinery is inert."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "target.py").write_text("X = 1\n")
    (pkg / "doc.py").write_text(
        "MESSAGE = 'see pkg.target for details'\n"  # mentions a real module, but no loader
    )

    edges = PythonDynamicHints().extract(tmp_path)
    assert all(e.source != "pkg/doc.py" for e in edges)


# ---------------------------------------------------------------------------
# 5. Same-file references in a non-call position (PR #531 false positives)
# ---------------------------------------------------------------------------


def test_callable_passed_as_argument_same_file_not_flagged() -> None:
    """A function handed to a higher-order helper — never invoked by name —
    is live. Mirrors ``maintainability_weight`` / ``performance_category``
    passed into ``_score_dimension`` in ``health/scoring.py``."""
    sources = {
        "packages/core/src/myrepo/__init__.py": "",
        "packages/core/src/myrepo/scoring.py": (
            "def maintainability_weight(name):\n    return 1.0\n\n\n"
            "def maintainability_category(name):\n    return 'x'\n\n\n"
            "def _score_dimension(results, weight_fn, category_fn):\n"
            "    return weight_fn(results) + category_fn(results)\n\n\n"
            "def score_file(results):\n"
            "    return _score_dimension(\n"
            "        results, maintainability_weight, maintainability_category\n"
            "    )\n"
        ),
    }
    graph = _graph_from_sources(sources)
    flagged = _unused_export_names(graph)
    assert "maintainability_weight" not in flagged
    assert "maintainability_category" not in flagged


def test_class_used_only_as_type_annotation_same_file_not_flagged() -> None:
    """A Pydantic-style model used only as a request-body parameter type is
    live — FastAPI instantiates it at runtime, so no constructor call lands
    in user code. Mirrors ``FindingStatusUpdate`` in ``routers/code_health.py``."""
    sources = {
        "packages/server/src/myrepo/__init__.py": "",
        "packages/server/src/myrepo/server/__init__.py": "",
        "packages/server/src/myrepo/server/routers/__init__.py": "",
        "packages/server/src/myrepo/server/routers/code_health.py": (
            "from pydantic import BaseModel\n\n\n"
            "class FindingStatusUpdate(BaseModel):\n    status: str\n\n\n"
            "def update_status(finding_id, payload: FindingStatusUpdate):\n"
            "    return payload.status\n"
        ),
    }
    graph = _graph_from_sources(sources)
    assert "FindingStatusUpdate" not in _unused_export_names(graph)


def test_forward_ref_annotation_same_file_not_flagged() -> None:
    """A quoted forward-reference annotation (``payload: "Body"``) still
    counts as a use — the parser re-parses string annotations."""
    sources = {
        "pkg/__init__.py": "",
        "pkg/api.py": (
            "class Body:\n    x: int = 0\n\n\n"
            "def handler(payload: 'Body'):\n    return payload\n"
        ),
    }
    graph = _graph_from_sources(sources)
    assert "Body" not in _unused_export_names(graph)


def test_genuinely_unused_same_file_symbol_still_flagged() -> None:
    """The rescue must not blanket-hide dead code: a public symbol that is
    never referenced anywhere (in-file or cross-file) is still reported."""
    sources = {
        "pkg/__init__.py": "",
        "pkg/mod.py": (
            "def used_helper():\n    return 1\n\n\n"
            "def caller():\n    return used_helper()\n\n\n"
            "def never_referenced():\n    return 2\n"
        ),
    }
    graph = _graph_from_sources(sources)
    flagged = _unused_export_names(graph)
    assert "never_referenced" in flagged


def test_genuinely_unused_recursive_symbol_still_flagged() -> None:
    """A symbol's own body must not rescue it: a recursive function that is
    dead from the rest of the codebase is still reported (its only reference
    is its own recursive call)."""
    sources = {
        "pkg/__init__.py": "",
        "pkg/mod.py": (
            "def dead_recursive(n):\n"
            "    if n <= 0:\n        return 0\n"
            "    return dead_recursive(n - 1)\n"
        ),
    }
    graph = _graph_from_sources(sources)
    assert "dead_recursive" in _unused_export_names(graph)


def test_name_only_in_all_string_is_not_rescued() -> None:
    """A name appearing solely as an ``__all__`` string literal is not a
    real reference (it is export bookkeeping, not a use) and must not be
    rescued by the same-file machinery."""
    sources = {
        "pkg/__init__.py": "",
        "pkg/mod.py": ("__all__ = ['orphan']\n\n\ndef orphan():\n    return 1\n"),
    }
    graph = _graph_from_sources(sources)
    assert "orphan" in _unused_export_names(graph)


def test_dynamic_use_edge_marks_target_file_live() -> None:
    """An incoming ``dynamic_uses`` edge keeps every public export of the
    target file out of the unused-export results (analyzer contract that the
    Python dynamic-hints edges rely on)."""
    g = nx.DiGraph()
    g.add_node(
        "pkg/registry.py",
        node_type="file",
        language="python",
        is_test=False,
        is_entry_point=False,
    )
    g.add_node(
        "pkg/ollama.py",
        node_type="file",
        language="python",
        is_test=False,
        is_entry_point=False,
    )
    sym = "pkg/ollama.py::OllamaProvider"
    g.add_node(
        sym,
        node_type="symbol",
        kind="class",
        name="OllamaProvider",
        visibility="public",
        decorators=[],
        start_line=1,
        end_line=20,
        file_path="pkg/ollama.py",
        language="python",
    )
    g.add_edge("pkg/ollama.py", sym, edge_type="defines")
    g.add_edge("pkg/registry.py", "pkg/ollama.py", edge_type="dynamic_uses")

    assert "OllamaProvider" not in _unused_export_names(g)
