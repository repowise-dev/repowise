"""A macro invoked in a C++ class body is not a method.

``REGISTER_CLASS(Foo, Base);`` and ``GENERATED_BODY()`` parse as typeless,
bodiless member declarations, the shape of a constructor, so each became a
method named after the macro. The macro's own uses must still count where
they are read: the registration markers that make a file a root, and the
macro's name written outside its ``#define``.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.c_name_uses import drop_preprocessed_named_elsewhere
from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from repowise.core.ingestion import ASTParser, GraphBuilder
from repowise.core.ingestion.graph_warmups import _mark_cpp_entry_point_files
from tests.unit.ingestion.parser._helpers import _make_file_info


def _members(source: str, path: str = "lib/thing.h") -> dict[str, set[str]]:
    parsed = ASTParser().parse_file(_make_file_info(path, "cpp"), source.encode())
    members: dict[str, set[str]] = {}
    for s in parsed.symbols:
        if s.parent_name:
            members.setdefault(s.parent_name, set()).add(s.name)
    return members


@pytest.mark.parametrize(
    "line",
    [
        "REGISTER_CLASS(Foo, Base);",
        "REGISTER_CLASS(Foo, Base)",
        "DECLARE_VIRTUAL1R(bool, _process, double)",
        "DECLARE_VIRTUAL1R(bool, _process, double);",
        "CLASS_ALLOCATOR(Foo, SystemAllocator);",
        "CLASS_ALLOCATOR(Foo, SystemAllocator)",
        "GENERATED_BODY()",
        "GENERATED_BODY();",
    ],
)
def test_macro_in_class_body_is_not_a_method(line: str) -> None:
    source = f"class Foo : public Base {{\n    {line}\npublic:\n    void tick();\n}};\n"
    members = _members(source)

    assert members["Foo"] == {"tick"}


def test_macros_between_members_keep_the_members() -> None:
    source = """class MainLoop : public Base {
    REGISTER_CLASS(MainLoop, Base);
protected:
    static void bind_methods();
    DECLARE_VIRTUAL1R(bool, _physics_process, double)
    DECLARE_VIRTUAL1R(bool, _process, double)
public:
    virtual bool process(double time);
};
"""
    assert _members(source)["MainLoop"] == {"bind_methods", "process"}


def test_uppercase_methods_and_constructors_stay() -> None:
    source = """class Hasher {
public:
    uint32_t CRC32() const;
    void RESET() { state = 0; }
    Hasher();
};
struct URL {
    URL();
    explicit URL(int port);
    ENCODE_HELPERS(URL);
};
template <>
class VEC<int> {
    VEC();
    VEC_ALLOC(VEC);
};
"""
    members = _members(source)

    assert members["Hasher"] == {"CRC32", "RESET", "Hasher"}
    assert members["URL"] == {"URL"}
    assert members["VEC<int>"] == {"VEC"}


def test_qt_object_class_is_still_a_root(tmp_path: Path) -> None:
    source = (
        "class Widget : public QObject {\n"
        "    Q_OBJECT\n"
        "    DECLARE_PROPS(Widget)\n"
        "public:\n"
        "    void go();\n"
        "};\n"
    )
    header = tmp_path / "widget.h"
    header.write_text(source)
    info = _make_file_info("widget.h", "cpp")
    info.abs_path = str(header)
    parsed = {"widget.h": ASTParser().parse_file(info, source.encode())}
    graph = nx.DiGraph()
    graph.add_node("widget.h")

    _mark_cpp_entry_point_files(parsed, graph)

    assert graph.nodes["widget.h"].get("is_reachability_root") is True
    assert {s.name for s in parsed["widget.h"].symbols if s.parent_name} == {"go"}


def test_macro_used_in_a_class_body_counts_as_a_use_of_its_define() -> None:
    files = {
        "macros.h": b"#pragma once\n#define REGISTER_CLASS(C, B) static const char* type_name();\n",
        "thing.h": (
            b'#pragma once\n#include "macros.h"\n'
            b"class Thing : public Base {\n    REGISTER_CLASS(Thing, Base);\npublic:\n"
            b"    void run();\n};\n"
        ),
    }
    parser = ASTParser()
    builder = GraphBuilder()
    for path, source in files.items():
        builder.add_file(parser.parse_file(_make_file_info(path, "cpp"), source))
    declarations = DeadCodeAnalyzer(builder.build())._preprocessed_declaration_sites()
    define = DeadCodeFindingData(
        kind=DeadCodeKind.UNUSED_EXPORT,
        file_path="macros.h",
        symbol_name="REGISTER_CLASS",
        symbol_kind="function",
        confidence=0.6,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=1,
        evidence=[],
        safe_to_delete=False,
        primary_owner=None,
        age_days=None,
        start_line=2,
        end_line=2,
    )

    assert drop_preprocessed_named_elsewhere([define], files, declarations) == []
