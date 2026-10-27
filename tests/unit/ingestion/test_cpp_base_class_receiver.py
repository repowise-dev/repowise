"""A typed C++ receiver reaches a method its class inherits from a base class.

``specs.set_align()`` on a ``format_specs&`` names ``basic_specs::set_align``
when ``format_specs`` derives from ``basic_specs`` and declares no
``set_align`` of its own. The pair index holds only ``(class, method)`` pairs a
class declares, so the call went unresolved.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _call_targets(graph, caller: str) -> set[str]:
    return {
        t for s, t, d in graph.edges(data=True) if d.get("edge_type") == "calls" and s == caller
    }


_SPECS_H = """#pragma once
class basic_specs {
 public:
  void set_align(int a) { align_ = a; }
  void set_fill(char c) { fill_ = c; }
 private:
  int align_;
  char fill_;
};

struct format_specs : basic_specs {
  int width;
};

struct dynamic_specs : public format_specs {
  int ref;
};
"""


def _repo(root: Path, specs_h: str = _SPECS_H, extra: dict[str, str] | None = None) -> None:
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "specs.h").write_text(specs_h)
    # Reached through another header, so no direct include lets the bare-name
    # tiers answer for the typed one.
    (root / "lib" / "mid.h").write_text('#pragma once\n#include "specs.h"\n')
    (root / "lib" / "parse.cc").write_text(
        '#include "lib/mid.h"\n'
        "void parse_align(format_specs& specs) { specs.set_align(1); }\n"
        "void parse_fill(dynamic_specs& specs) { specs.set_fill('x'); }\n"
    )
    for name, text in (extra or {}).items():
        (root / "lib" / name).write_text(text)


class TestCppBaseClassReceiver:
    def test_typed_receiver_reaches_the_base_class_method(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert "lib/specs.h::basic_specs::set_align" in targets

    def test_reaches_a_grandparent(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_fill")
        assert "lib/specs.h::basic_specs::set_fill" in targets

    def test_an_out_of_line_override_hides_the_base_method(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H.replace("  int width;\n", "  int width;\n  void set_align(int a);\n")
        _repo(
            tmp_path,
            specs_h,
            {"specs.cc": '#include "lib/specs.h"\nvoid format_specs::set_align(int a) {}\n'},
        )
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert "lib/specs.h::basic_specs::set_align" not in targets

    def test_an_out_of_line_template_override_hides_the_base_method(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H + (
            "\ntemplate <typename T> struct tspecs : basic_specs {\n  void set_align(int a);\n};\n"
            "template <typename T> void tspecs<T>::set_align(int a) {}\n"
        )
        caller = '#include "lib/mid.h"\nvoid parse_t(tspecs<char>& specs) { specs.set_align(1); }\n'
        _repo(tmp_path, specs_h, {"tparse.cc": caller})
        targets = _call_targets(_build(tmp_path), "lib/tparse.cc::parse_t")
        assert "lib/specs.h::basic_specs::set_align" not in targets

    def test_two_types_of_that_name_refuse(self, tmp_path: Path) -> None:
        other = "#pragma once\nstruct format_specs {\n  int width;\n};\n"
        _repo(tmp_path, extra={"other.h": other})
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert "lib/specs.h::basic_specs::set_align" not in targets

    def test_two_bases_declaring_the_method_refuse(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H.replace(
            "struct format_specs : basic_specs {",
            "class aligner {\n public:\n  void set_align(int a) {}\n};\n\n"
            "struct format_specs : basic_specs, aligner {",
        )
        _repo(tmp_path, specs_h)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert not any(t.endswith("::set_align") for t in targets)

    def test_a_method_no_base_declares_yields_no_edge(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H.replace("  void set_align(int a) { align_ = a; }\n", "")
        _repo(tmp_path, specs_h)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert not any(t.endswith("::set_align") for t in targets)
