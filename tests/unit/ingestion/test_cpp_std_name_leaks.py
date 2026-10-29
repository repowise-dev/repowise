"""A receiver-less C++ call that cannot mean a repo member must not bind to one by name.

``std::find(a, b, c)`` is the standard algorithm, and a bare ``count(..)`` in a
free function under ``using namespace std`` is too: neither can reach
``HashMap::find`` or ``HashMap::count``, which only a receiver or an enclosing
class's ``this`` can.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _callees(root: Path, caller: str) -> set[str]:
    traverser = FileTraverser(root)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    return {
        t for s, t, d in graph.edges(data=True) if d.get("edge_type") == "calls" and s == caller
    }


_MAP_H = (
    "#pragma once\nstruct HashMap {\n  int find(int k);\n  int count(int k);\n"
    "  int Lookup(int k) { return find(k); }\n};\n"
    "inline int HashMap::find(int k) { return k; }\ninline int HashMap::count(int k) { return k; }\n"
)


def _repo(root: Path, body: str) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "map.h").write_text(_MAP_H)
    (root / "src" / "use.cc").write_text(
        '#include <algorithm>\n#include "map.h"\nusing namespace std;\n' + body
    )


class TestStdNameLeaks:
    def test_a_std_qualified_call_binds_no_repo_member(self, tmp_path: Path) -> None:
        _repo(tmp_path, "int Free(int* a, int* b) { return *std::find(a, b, 1); }\n")
        assert not _callees(tmp_path, "src/use.cc::Free")

    def test_a_bare_call_in_a_free_function_binds_no_member(self, tmp_path: Path) -> None:
        _repo(tmp_path, "int Free(int* a, int* b) { return count(a, b, 2); }\n")
        assert not _callees(tmp_path, "src/use.cc::Free")

    def test_a_member_still_reaches_its_own_class_method(self, tmp_path: Path) -> None:
        """The control: inside ``HashMap``, a bare ``find`` is ``this->find``."""
        _repo(tmp_path, "int Free() { return 0; }\n")
        assert "src/map.h::HashMap::find" in _callees(tmp_path, "src/map.h::HashMap::Lookup")

    def test_a_free_function_of_the_name_still_binds(self, tmp_path: Path) -> None:
        _repo(
            tmp_path,
            "int count(int* a, int* b, int v) { return 0; }\n"
            "int Free(int* a, int* b) { return count(a, b, 2); }\n",
        )
        assert "src/use.cc::count" in _callees(tmp_path, "src/use.cc::Free")

    def test_a_namespace_qualified_definition_is_a_free_function(self, tmp_path: Path) -> None:
        """``int util::clamp()`` names a namespace, not a class: the bare call stands."""
        _repo(tmp_path, "")
        (tmp_path / "src" / "util.cc").write_text("int util::clamp(int x) { return x; }\n")
        (tmp_path / "src" / "use.cc").write_text(
            "using namespace util;\nint Free() { return clamp(1); }\n"
        )
        assert "src/util.cc::util::clamp" in _callees(tmp_path, "src/use.cc::Free")

    def test_a_friend_defined_in_the_class_still_binds(self, tmp_path: Path) -> None:
        _repo(tmp_path, "")
        (tmp_path / "src" / "foo.h").write_text(
            "#pragma once\nstruct Foo {\n  int v;\n"
            "  friend void Exchange(Foo& a, Foo& b) { a.v = b.v; }\n};\n"
        )
        (tmp_path / "src" / "use.cc").write_text(
            '#include "foo.h"\nvoid Free(Foo a, Foo b) { Exchange(a, b); }\n'
        )
        assert any(t.endswith("Exchange") for t in _callees(tmp_path, "src/use.cc::Free"))

    def test_a_constructor_still_binds(self, tmp_path: Path) -> None:
        _repo(tmp_path, "")
        (tmp_path / "src" / "slice.h").write_text(
            "#pragma once\nstruct Slice {\n  Slice(const char* s);\n  const char* d;\n};\n"
            "inline Slice::Slice(const char* s) : d(s) {}\n"
        )
        (tmp_path / "src" / "use.cc").write_text(
            '#include "slice.h"\nint Free() { Slice s = Slice("x"); return 0; }\n'
        )
        assert any("Slice::Slice" in t for t in _callees(tmp_path, "src/use.cc::Free"))

    def test_a_macro_misparsed_free_function_still_binds(self, tmp_path: Path) -> None:
        """An unexpanded ``INLINE box_contents_t`` parses ``box_decode`` as its member.

        The spelling is jemalloc's ``rtree.h`` cut down; the parse is sensitive to it.
        """
        _repo(tmp_path, "")
        (tmp_path / "src" / "types.h").write_text(
            "typedef struct box_s { int x; } box_contents_t;\n"
        )
        (tmp_path / "src" / "box.h").write_text(
            "LIB_ALWAYS_INLINE uintptr_t\nbox_encode(box_contents_t contents) {\n\treturn 0;\n}\n\n"
            "LIB_ALWAYS_INLINE box_contents_t\nbox_decode(uintptr_t bits) {\n"
            "\tbox_contents_t contents;\n\treturn contents;\n}\n"
        )
        (tmp_path / "src" / "use.cc").write_text(
            '#include "box.h"\nint Free() { return box_decode(1).x; }\n'
        )
        assert any(t.endswith("box_decode") for t in _callees(tmp_path, "src/use.cc::Free"))


_PRINTER_H = (
    "#pragma once\nstruct Printer {\n  void Error(const char* msg);\n};\n"
    "inline void Printer::Error(const char* msg) {}\n"
)
_UTIL_H = "#pragma once\nvoid Error(const char* msg);\n"
_UTIL_CC = '#include "util.h"\nvoid Error(const char* msg) {}\n'


class TestUnrelatedClassMember:
    """A bare call inside one class cannot name another class's member."""

    def _write(self, root: Path, use: str, printer_file: str = "printer.h") -> None:
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / printer_file).write_text(_PRINTER_H)
        (root / "src" / "util.h").write_text(_UTIL_H)
        (root / "src" / "util.cc").write_text(_UTIL_CC)
        (root / "src" / "use.cc").write_text(use)

    def test_an_unrelated_member_yields_to_the_included_free_function(
        self, tmp_path: Path
    ) -> None:
        self._write(
            tmp_path,
            '#include "printer.h"\n#include "util.h"\nstruct Cleaner {\n'
            '  void Run() {\n    Error("x");\n  }\n};\n',
        )
        assert _callees(tmp_path, "src/use.cc::Cleaner::Run") == {"src/util.cc::Error"}

    def test_a_member_of_a_base_class_still_binds(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            '#include "printer.h"\nstruct Loud : public Printer {\n'
            '  void Run() {\n    Error("x");\n  }\n};\n',
        )
        assert _callees(tmp_path, "src/use.cc::Loud::Run") == {"src/printer.h::Printer::Error"}

    def test_a_member_of_an_enclosing_class_still_binds(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "struct Outer {\n  static void Log(const char* m) {\n    m++;\n  }\n"
            "  struct Inner {\n    void Run() {\n      Log(\"x\");\n    }\n  };\n};\n",
        )
        assert _callees(tmp_path, "src/use.cc::Inner::Run") == {"src/use.cc::Outer::Log"}

    def test_a_free_caller_takes_the_free_function_a_member_hid(self, tmp_path: Path) -> None:
        """The same file holds both; the flat name table answered with the member."""
        self._write(
            tmp_path,
            "int* reserve(int* it, int n) {\n  return it;\n}\n"
            "struct Buffer {\n  void reserve(int n) {\n    n++;\n  }\n};\n"
            "int* Pad(int* out) {\n  return reserve(out, 2);\n}\n",
        )
        assert _callees(tmp_path, "src/use.cc::Pad") == {"src/use.cc::reserve"}

    def test_a_class_template_member_yields_too(self, tmp_path: Path) -> None:
        """A class template is two symbols under one id; its member is still a member."""
        self._write(
            tmp_path,
            "int* reserve(int* it, int n) {\n  return it;\n}\n"
            "template <typename T> class Buffer {\n public:\n"
            "  void reserve(int n) {\n    n++;\n  }\n};\n"
            "int* Pad(int* out) {\n  return reserve(out, 2);\n}\n",
        )
        assert _callees(tmp_path, "src/use.cc::Pad") == {"src/use.cc::reserve"}

    def test_a_macro_defined_in_a_class_body_still_binds(self, tmp_path: Path) -> None:
        """The preprocessor knows no class scope, so the macro is no member."""
        self._write(
            tmp_path,
            "struct Spec {\n  int mode;\n#define SPEC_VALID(s) ((s)->mode != 0)\n  int size;\n};\n"
            "int Check(Spec* s) {\n  return SPEC_VALID(s);\n}\n",
        )
        assert _callees(tmp_path, "src/use.cc::Check") == {"src/use.cc::Spec::SPEC_VALID"}

    def test_an_out_of_line_member_yields_too(self, tmp_path: Path) -> None:
        """The caller's class is declared in a header and its body defined in the ``.cc``."""
        self._write(
            tmp_path,
            '#include "cleaner.h"\n#include "printer.h"\n#include "util.h"\n'
            'void Cleaner::Run() {\n  Error("x");\n}\n',
        )
        (tmp_path / "src" / "cleaner.h").write_text(
            "#pragma once\nstruct Cleaner {\n  void Run();\n};\n"
        )
        assert _callees(tmp_path, "src/use.cc::Cleaner::Run") == {"src/util.cc::Error"}
