"""A bare or ``this->`` C++ call inside a member function names its class's member, or a base's.

The own-class tier looked only in the caller's file, so an out-of-line body in
a ``.cc`` never saw the members its header declares, and C++ was left out of
the inherited tier, so a member a base class declares never resolved. A
same-named method of another class in the file answered instead.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path, files: dict[str, str], this_calls: frozenset[str] = frozenset()):
    """The graph of *files*; a bare call to a name in *this_calls* is read as ``this->name()``."""
    for name, text in files.items():
        (repo / name).write_text(text)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        parsed = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        parsed.calls[:] = [
            replace(c, receiver_name="this") if c.target_name in this_calls else c
            for c in parsed.calls
        ]
        builder.add_file(parsed)
    return builder.build()


def _calls(graph, caller: str) -> dict[str, str]:
    return {
        t: d.get("resolution_origin")
        for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "calls" and s == caller
    }


_DISK_H = """#pragma once
#include <string>
struct DiskInterface {
  virtual long Stat(const std::string& path) const = 0;
  virtual bool MakeDir(const std::string& path) = 0;
  bool MakeDirs(const std::string& path);
};
struct RealDiskInterface : public DiskInterface {
  long Stat(const std::string& path) const override;
  bool MakeDir(const std::string& path) override;
};
"""

_DISK_CC = """#include "disk.h"
bool DiskInterface::MakeDirs(const std::string& path) {
  long mtime = Stat(path);
  if (!MakeDirs(path)) return false;
  return MakeDir(path);
}
long RealDiskInterface::Stat(const std::string& path) const { return 0; }
bool RealDiskInterface::MakeDir(const std::string& path) { return true; }
"""

_BUFFER_H = """#pragma once
namespace detail {
template <typename T> class buffer {
 public:
  void set(T* data, int n) {}
  int size() const { return 0; }
};
}
"""


class TestOwnClassAcrossFiles:
    def test_out_of_line_body_reaches_the_member_its_header_declares(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, {"disk.h": _DISK_H, "disk.cc": _DISK_CC})
        calls = _calls(graph, "disk.cc::DiskInterface::MakeDirs")
        assert calls.get("disk.h::DiskInterface::MakeDir") == "enclosing_class"
        assert calls.get("disk.h::DiskInterface::Stat") == "enclosing_class"
        # The same file's derived-class bodies are not the caller's class.
        assert "disk.cc::RealDiskInterface::MakeDir" not in calls

    def test_recursion_through_the_header_draws_no_edge(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, {"disk.h": _DISK_H, "disk.cc": _DISK_CC})
        calls = _calls(graph, "disk.cc::DiskInterface::MakeDirs")
        assert not any(t.endswith("::MakeDirs") for t in calls)

    def test_inline_body_reaches_a_member_defined_out_of_line(self, tmp_path: Path) -> None:
        header = (
            "#pragma once\nstruct Node {\n  bool Stat(int n);\n"
            "  bool StatIfNecessary(int n) { return Stat(n); }\n};\n"
        )
        files = {
            "graph.h": header,
            "graph.cc": '#include "graph.h"\nbool Node::Stat(int n) { return true; }\n',
            "other.cc": "struct Edge { bool Stat(int n) { return false; } };\n",
        }
        calls = _calls(_build(tmp_path, files), "graph.h::Node::StatIfNecessary")
        assert calls == {"graph.cc::Node::Stat": "enclosing_class"}

    def test_a_body_in_the_callers_file_beats_the_header_declaration(self, tmp_path: Path) -> None:
        # Two files define the member, so the header declaration pairs with neither.
        lexer_cc = (
            '#include "lexer.h"\nint Lexer::ReadToken() { return 1; }\n'
            "int Lexer::PeekToken() { return ReadToken(); }\n"
        )
        files = {
            "lexer.h": "#pragma once\nstruct Lexer {\n  int ReadToken();\n  int PeekToken();\n};\n",
            "lexer.cc": lexer_cc,
            "lexer.in.cc": lexer_cc,
        }
        calls = _calls(_build(tmp_path, files), "lexer.cc::Lexer::PeekToken")
        assert calls == {"lexer.cc::Lexer::ReadToken": "same_file"}

    def test_this_call_in_an_out_of_line_body_reaches_the_header_member(
        self, tmp_path: Path
    ) -> None:
        graph = _build(tmp_path, {"disk.h": _DISK_H, "disk.cc": _DISK_CC}, frozenset({"MakeDir"}))
        calls = _calls(graph, "disk.cc::DiskInterface::MakeDirs")
        assert calls.get("disk.h::DiskInterface::MakeDir") == "self_scope"


_OS_H = """#pragma once
#include "buffer.h"
class ostream : private detail::buffer<char> {
 public:
  ~ostream();
  void flush() { size(); }
};
"""


class TestInheritedMembers:
    def test_inline_bare_call_reaches_the_base_member(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "os.h": _OS_H})
        calls = _calls(graph, "os.h::ostream::flush")
        assert calls == {"buffer.h::buffer::size": "enclosing_inherited"}

    def test_out_of_line_bare_call_reaches_the_base_member(self, tmp_path: Path) -> None:
        os_cc = '#include "os.h"\nostream::~ostream() {\n  flush();\n  size();\n}\n'
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "os.h": _OS_H, "os.cc": os_cc})
        calls = _calls(graph, "os.cc::ostream::~ostream")
        assert calls == {
            "os.h::ostream::flush": "enclosing_class",
            "buffer.h::buffer::size": "enclosing_inherited",
        }

    def test_own_declaration_hides_the_base_member(self, tmp_path: Path) -> None:
        os_h = _OS_H.replace("  ~ostream();\n", "  ~ostream();\n  int size() const;\n")
        os_cc = '#include "os.h"\nint ostream::size() const { return 1; }\n'
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "os.h": os_h, "os.cc": os_cc})
        calls = _calls(graph, "os.h::ostream::flush")
        assert calls == {"os.cc::ostream::size": "enclosing_class"}

    def test_two_bases_declaring_the_member_refuse(self, tmp_path: Path) -> None:
        os_h = _OS_H.replace(
            "class ostream : private detail::buffer<char> {",
            "struct sizer {\n  int size() const { return 2; }\n};\n"
            "class ostream : private detail::buffer<char>, sizer {",
        )
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "os.h": os_h})
        assert "enclosing_inherited" not in _calls(graph, "os.h::ostream::flush").values()

    def test_a_base_the_file_cannot_include_is_not_followed(self, tmp_path: Path) -> None:
        # ``ext::Widget`` binds by bare name to a repository class that
        # nothing on this file's include path declares.
        files = {
            "widget.h": "#pragma once\nclass Widget {\n public:\n  int code() { return 0; }\n};\n",
            "panel.h": "#pragma once\n#include <ext/base.h>\n"
            "class Panel : public ext::Widget {\n"
            "  int twice() { return code(); }\n};\n",
        }
        graph = _build(tmp_path, files)
        assert graph.has_edge("panel.h::Panel", "widget.h::Widget")
        assert "enclosing_inherited" not in _calls(graph, "panel.h::Panel::twice").values()


_MEM_H = """#pragma once
#include "buffer.h"
template <typename T, typename Alloc = int>
class mem_buffer : public detail::buffer<T> {
 public:
  void grow(int n) { set(nullptr, n); }
};
"""


class TestTemplates:
    def test_this_call_reaches_a_dependent_base(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "mem.h": _MEM_H}, frozenset({"set"}))
        calls = _calls(graph, "mem.h::mem_buffer::grow")
        assert calls == {"buffer.h::buffer::set": "self_inherited"}

    def test_bare_call_skips_a_dependent_base(self, tmp_path: Path) -> None:
        free_set = '#include "buffer.h"\nvoid set(void* p, int n);\n'
        mem_h = _MEM_H.replace('#include "buffer.h"\n', free_set)
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "mem.h": mem_h})
        assert "buffer.h::buffer::set" not in _calls(graph, "mem.h::mem_buffer::grow")

    def test_a_template_parameter_base_is_not_followed(self, tmp_path: Path) -> None:
        files = {
            "mixin.h": "#pragma once\nclass Mixin {\n public:\n"
            "  int size() const { return 0; }\n};\n",
            "wrap.h": '#pragma once\n#include "mixin.h"\ntemplate <class Mixin>\n'
            "class wrap : public Mixin {\n  int go() { return size(); }\n};\n",
        }
        graph = _build(tmp_path, files, frozenset({"size"}))
        assert _calls(graph, "wrap.h::wrap::go") == {}

    def test_a_class_nested_in_a_template_skips_its_bases_for_a_bare_call(
        self, tmp_path: Path
    ) -> None:
        outer_h = (
            '#pragma once\n#include "buffer.h"\ntemplate <typename T>\nstruct outer {\n'
            "  struct inner : detail::buffer<T> {\n    int g() { return size(); }\n  };\n};\n"
        )
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "outer.h": outer_h})
        assert "enclosing_inherited" not in _calls(graph, "outer.h::inner::g").values()


class TestQualifiedCalls:
    def test_a_qualified_call_is_not_the_classs_member(self, tmp_path: Path) -> None:
        os_h = _OS_H.replace("void flush() { size(); }", "void flush() { other::size(); }")
        graph = _build(tmp_path, {"buffer.h": _BUFFER_H, "os.h": os_h})
        assert "enclosing_inherited" not in _calls(graph, "os.h::ostream::flush").values()


class TestNonCallees:
    def test_an_elaborated_field_type_is_not_a_member_function(self, tmp_path: Path) -> None:
        set_h = "#pragma once\nstruct Set {\n  Set();\n  struct sigaction old_act_;\n};\n"
        set_cc = '#include "set.h"\nSet::Set() { sigaction(2, 0, &old_act_); }\n'
        graph = _build(tmp_path, {"set.h": set_h, "set.cc": set_cc})
        assert "set.h::Set::sigaction" not in _calls(graph, "set.cc::Set::Set")

    def test_a_prototype_two_files_define_is_not_the_callee(self, tmp_path: Path) -> None:
        lexer_h = (
            "#pragma once\nstruct Lexer {\n  int ReadEval();\n"
            "  int ReadPath() { return ReadEval(); }\n};\n"
        )
        body = '#include "lexer.h"\nint Lexer::ReadEval() { return 1; }\n'
        graph = _build(tmp_path, {"lexer.h": lexer_h, "lexer.cc": body, "lexer.in.cc": body})
        assert "lexer.h::Lexer::ReadEval" not in _calls(graph, "lexer.h::Lexer::ReadPath")

    def test_a_temporary_of_the_base_type_is_not_a_member_call(self, tmp_path: Path) -> None:
        node_h = "#pragma once\nclass Node {\n public:\n  Node();\n  explicit Node(int v);\n};\n"
        node_cc = '#include "node.h"\nNode::Node() {}\nNode::Node(int v) {}\n'
        value_h = (
            '#pragma once\n#include "node.h"\nstruct value : public Node {\n'
            "  explicit value(int v) { take(Node(v)); }\n  void take(const Node& n);\n};\n"
        )
        graph = _build(tmp_path, {"node.h": node_h, "node.cc": node_cc, "value.h": value_h})
        assert "enclosing_inherited" not in _calls(graph, "value.h::value::value").values()
