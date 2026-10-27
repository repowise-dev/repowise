"""Python ``qualified_name`` is the importable dotted module, not the repo path.

The parser sees one file, so it dots the raw path. ``GraphBuilder.build`` runs
after every file is parsed and rewrites Python names from the repository's
package layout. ``Symbol.id`` is a separate identifier and must not move.
"""

from __future__ import annotations

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

_PY = b"class Widget:\n    def run(self):\n        pass\n\n\ndef helper():\n    pass\n"
_GO = b"package main\n\nfunc Helper() {}\n"


def _build(files: dict[str, tuple[str, bytes]]) -> tuple[dict[str, dict[str, str]], GraphBuilder]:
    parser = ASTParser()
    builder = GraphBuilder()
    parsed_by_path = {}
    for path, (language, source) in files.items():
        parsed = parser.parse_file(_make_file_info(path, language), source)
        parsed_by_path[path] = parsed
        builder.add_file(parsed)
    builder.build()
    names = {
        path: {s.id: s.qualified_name for s in parsed.symbols}
        for path, parsed in parsed_by_path.items()
    }
    return names, builder


def test_python_names_use_importable_module_and_others_are_unchanged():
    names, _ = _build(
        {
            "src/mypkg/__init__.py": ("python", b""),
            "src/mypkg/core.py": ("python", _PY),
            "packages/core/src/repowise/core/persist.py": ("python", _PY),
            "flat/__init__.py": ("python", b""),
            "flat/mod.py": ("python", _PY),
            "scripts/tool.py": ("python", _PY),
            "cmd/main.go": ("go", _GO),
        }
    )

    assert names["src/mypkg/core.py"]["src/mypkg/core.py::helper"] == "mypkg.core.helper"
    assert names["src/mypkg/core.py"]["src/mypkg/core.py::Widget::run"] == "mypkg.core.Widget.run"
    assert (
        names["packages/core/src/repowise/core/persist.py"][
            "packages/core/src/repowise/core/persist.py::helper"
        ]
        == "repowise.core.persist.helper"
    )
    assert names["flat/mod.py"]["flat/mod.py::helper"] == "flat.mod.helper"
    # A loose script has no importable module, so it keeps the path form.
    assert names["scripts/tool.py"]["scripts/tool.py::helper"] == "scripts.tool.helper"
    # Other languages keep the path form.
    assert names["cmd/main.go"]["cmd/main.go::Helper"] == "cmd.main.Helper"


def test_graph_nodes_carry_the_new_name_and_symbol_id_is_stable():
    _, builder = _build({"src/mypkg/core.py": ("python", _PY)})
    node = builder.graph().nodes["src/mypkg/core.py::helper"]
    assert node["qualified_name"] == "mypkg.core.helper"
    assert "src/mypkg/core.py::Widget::run" in builder.graph()


def test_rebuilding_is_idempotent():
    names, builder = _build({"src/mypkg/core.py": ("python", _PY)})
    builder.build()
    assert (
        builder.graph().nodes["src/mypkg/core.py::helper"]["qualified_name"] == "mypkg.core.helper"
    )
    assert names["src/mypkg/core.py"]["src/mypkg/core.py::helper"] == "mypkg.core.helper"


def test_two_files_deriving_one_module_keep_distinct_symbol_ids():
    # Two source roots can both derive ``pkg.mod``. The names collide, the
    # ids (which carry the path) do not, so both symbols stay in the graph.
    names, builder = _build(
        {
            "a/src/pkg/mod.py": ("python", _PY),
            "b/src/pkg/mod.py": ("python", _PY),
        }
    )
    assert names["a/src/pkg/mod.py"]["a/src/pkg/mod.py::helper"] == "pkg.mod.helper"
    assert names["b/src/pkg/mod.py"]["b/src/pkg/mod.py::helper"] == "pkg.mod.helper"
    assert "a/src/pkg/mod.py::helper" in builder.graph()
    assert "b/src/pkg/mod.py::helper" in builder.graph()
