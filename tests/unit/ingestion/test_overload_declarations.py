"""Overload stubs are declarations; the implementation is the symbol.

A Python ``@overload`` stub and a TypeScript overload signature share the
implementation's symbol id. Unmarked, any consumer that takes the first row for
an id served the stub's line range next to the implementation's call edges.
The rows are kept, not dropped: the call resolver reads every overload's return
type (``_overload_return_types``), and an overload set is one id with several
rows (``test_overload_set_uniqueness.py``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.call_resolver import CallResolver, _overload_return_types

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"
PY_FIXTURE = FIXTURES / "python" / "py_overload_init.py"
TS_FIXTURE = FIXTURES / "typescript" / "ts_overload_fn_method.ts"


def _parse(tmp_path: Path, fixture: Path):
    shutil.copy(fixture, tmp_path / fixture.name)
    parser = ASTParser()
    parsed = {}
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in FileTraverser(tmp_path).traverse():
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        parsed[fi.path] = pf
        builder.add_file(pf)
    return parsed[fixture.name], builder.build()


def _rows(parsed, sym_id: str) -> list[tuple[int, int, bool]]:
    return [(s.start_line, s.end_line, s.is_declaration) for s in parsed.symbols if s.id == sym_id]


class TestPythonOverloads:
    @pytest.fixture
    def parsed_graph(self, tmp_path: Path):
        return _parse(tmp_path, PY_FIXTURE)

    def test_stubs_are_declarations_and_implementation_is_not(self, parsed_graph) -> None:
        parsed, _ = parsed_graph
        # ``@overload`` and ``@typing.overload``, then the implementation.
        assert _rows(parsed, "py_overload_init.py::Client::__init__") == [
            (9, 9, True),
            (12, 12, True),
            (14, 17, False),
        ]
        # ``@typing_extensions.overload`` and a bare ``@overload`` at module level.
        assert _rows(parsed, "py_overload_init.py::parse") == [
            (28, 28, True),
            (32, 32, True),
            (35, 36, False),
        ]

    def test_other_decorators_are_not_declarations(self, parsed_graph) -> None:
        parsed, _ = parsed_graph
        assert _rows(parsed, "py_overload_init.py::Client::build") == [(23, 24, False)]
        assert _rows(parsed, "py_overload_init.py::Client::connect") == [(19, 20, False)]

    def test_overload_through_a_module_alias(self, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text(
            "import typing as t\n\n\n@t.overload\ndef f(x: int) -> int: ...\ndef f(x):\n    return x\n"
        )
        (fi,) = FileTraverser(tmp_path).traverse()
        parsed = ASTParser().parse_file(fi, Path(fi.abs_path).read_bytes())
        assert _rows(parsed, "m.py::f") == [(5, 5, True), (6, 7, False)]

    def test_graph_node_spans_the_implementation(self, parsed_graph) -> None:
        _, graph = parsed_graph
        node = graph.nodes["py_overload_init.py::Client::__init__"]
        assert (node["start_line"], node["end_line"]) == (14, 17)
        assert node["is_declaration"] is False
        callees = {
            tgt
            for _, tgt, data in graph.out_edges("py_overload_init.py::Client::__init__", data=True)
            if data.get("edge_type") == "calls"
        }
        assert "py_overload_init.py::Client::connect" in callees

    def test_every_overload_return_type_is_kept(self, parsed_graph) -> None:
        parsed, _ = parsed_graph
        types = _overload_return_types({"py_overload_init.py": parsed})
        assert {"str", "bytes"} <= set().union(
            *(v for (_, parent, name, _), v in types.items() if (parent, name) == (None, "parse"))
        )

    def test_stub_is_not_paired_with_a_same_named_definition_elsewhere(
        self, tmp_path: Path
    ) -> None:
        # Its implementation shares its id, so a same-named def in an importing
        # file must not capture the calls aimed at it.
        files = {
            "a.py": PY_FIXTURE.read_text(),
            "b.py": "import a\n\n\ndef parse(raw):\n    return a.parse(raw)\n",
            "c.py": "from a import parse\n\n\ndef run():\n    return parse('x')\n",
        }
        for name, text in files.items():
            (tmp_path / name).write_text(text)
        parser = ASTParser()
        parsed = {
            fi.path: parser.parse_file(fi, Path(fi.abs_path).read_bytes())
            for fi in FileTraverser(tmp_path).traverse()
        }
        resolver = CallResolver(
            parsed, {"b.py": {"a.py"}, "c.py": {"a.py"}}, repo_path=str(tmp_path)
        )
        assert "a.py::parse" not in resolver.declaration_definitions
        callees = {rc.callee_id for rc in resolver.resolve_file("c.py", parsed["c.py"].calls)}
        assert callees == {"a.py::parse"}


class TestTypeScriptOverloads:
    @pytest.fixture
    def parsed_graph(self, tmp_path: Path):
        return _parse(tmp_path, TS_FIXTURE)

    def test_function_signatures_are_declarations(self, parsed_graph) -> None:
        parsed, _ = parsed_graph
        assert _rows(parsed, "ts_overload_fn_method.ts::parse") == [
            (1, 1, True),
            (2, 2, True),
            (3, 5, False),
        ]
        sigs = [s.signature for s in parsed.symbols if s.id == "ts_overload_fn_method.ts::parse"]
        assert sigs[0] == "function parse(raw: string) -> string"
        assert all(s.kind == "function" for s in parsed.symbols if s.name == "parse")

    def test_class_overload_signatures_are_declarations(self, parsed_graph) -> None:
        parsed, _ = parsed_graph
        assert _rows(parsed, "ts_overload_fn_method.ts::Client::send") == [
            (8, 8, True),
            (9, 9, True),
            (10, 12, False),
        ]
        connect = [s for s in parsed.symbols if s.id == "ts_overload_fn_method.ts::Client::connect"]
        assert [(s.start_line, s.end_line, s.is_declaration) for s in connect] == [
            (14, 14, True),
            (15, 15, True),
            (16, 18, False),
        ]
        assert {(s.kind, s.parent_name, s.visibility) for s in connect} == {
            ("method", "Client", "private")
        }

    def test_interface_method_signature_is_not_a_symbol(self, parsed_graph) -> None:
        parsed, _ = parsed_graph
        assert not [s for s in parsed.symbols if s.name == "open"]

    def test_ambient_signature_without_a_body_is_not_a_symbol(self, parsed_graph) -> None:
        # ``declare`` signatures have no implementation; they are not overloads.
        parsed, _ = parsed_graph
        assert not [s for s in parsed.symbols if s.name in ("ambient", "run")]
        assert [s.name for s in parsed.symbols if s.name == "Shim"] == ["Shim"]

    def test_graph_node_spans_the_implementation(self, parsed_graph) -> None:
        _, graph = parsed_graph
        for sym_id, span in (
            ("ts_overload_fn_method.ts::parse", (3, 5)),
            ("ts_overload_fn_method.ts::Client::send", (10, 12)),
        ):
            node = graph.nodes[sym_id]
            assert (node["start_line"], node["end_line"]) == span
            assert node["is_declaration"] is False
