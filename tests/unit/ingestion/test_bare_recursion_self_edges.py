"""A bare or self-receiver recursive call is a self edge, answered in the caller's own scope.

Answering it there is what keeps recursion from falling through to an import,
an ancestor or another class's method of the same name.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _edges(tmp_path: Path, language: str, rel: str, text: str) -> set[tuple[str, str]]:
    path = tmp_path / rel
    path.write_text(text, encoding="utf-8")
    info = FileInfo(
        path=rel,
        abs_path=str(path),
        language=language,
        size_bytes=len(text),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, text.encode("utf-8"))
    resolver = CallResolver({rel: parsed}, {rel: set()}, repo_path=str(tmp_path))
    return {(rc.caller_id, rc.callee_id) for rc in resolver.resolve_file(rel, parsed.calls)}


SELF_CASES = [
    ("python", "m.py", "def fact(n):\n    return n * fact(n - 1) if n else 1\n", "m.py::fact"),
    (
        "python",
        "m.py",
        "class C:\n    def walk(self, n):\n        return self.walk(n - 1) if n else 0\n",
        "m.py::C::walk",
    ),
    (
        "python",
        "m.py",
        "class Node:\n    def __init__(self):\n        self.next: Node = None\n"
        "    def walk(self):\n        return self.next.walk()\n",
        "m.py::Node::walk",
    ),
    (
        "java",
        "A.java",
        "class A {\n  int walk(int n) { return n == 0 ? 0 : walk(n - 1); }\n}\n"
        "class B {\n  int walk(int n) { return 1; }\n}\n",
        "A.java::A::walk",
    ),
    (
        "java",
        "A.java",
        "class A {\n  int walk(int n) { return n == 0 ? 0 : this.walk(n - 1); }\n}\n",
        "A.java::A::walk",
    ),
    (
        "csharp",
        "A.cs",
        "class A {\n  int Walk(int n) { return n == 0 ? 0 : Walk(n - 1); }\n}\n",
        "A.cs::A::Walk",
    ),
    ("c", "m.c", "int fact(int n) { return n ? n * fact(n - 1) : 1; }\n", "m.c::fact"),
    (
        "cpp",
        "m.cpp",
        "class S {\npublic:\n  int walk(int n) { return n ? walk(n - 1) : 0; }\n};\n",
        "m.cpp::S::walk",
    ),
    (
        "kotlin",
        "A.kt",
        "class A {\n    fun walk(n: Int): Int = if (n == 0) 0 else this.walk(n - 1)\n}\n",
        "A.kt::A::walk",
    ),
    (
        "cpp",
        "m.cpp",
        "class S {\npublic:\n  int walk(int n);\n};\nint S::walk(int n) { return n ? walk(n - 1) : 0; }\n",
        "m.cpp::S::walk",
    ),
    (
        "rust",
        "lib.rs",
        "pub struct T;\nimpl T {\n"
        "    pub fn walk(&self, n: u32) -> u32 { if n == 0 { 0 } else { self.walk(n - 1) } }\n}\n",
        "lib.rs::T::walk",
    ),
    (
        "go",
        "m.go",
        "package m\n\nfunc fact(n int) int {\n\tif n == 0 {\n\t\treturn 1\n\t}\n"
        "\treturn n * fact(n-1)\n}\n",
        "m.go::fact",
    ),
]


@pytest.mark.parametrize(
    ("language", "rel", "text", "symbol"),
    SELF_CASES,
    ids=[f"{case[0]}-{i}" for i, case in enumerate(SELF_CASES)],
)
def test_a_recursive_call_is_a_self_edge(
    tmp_path: Path, language: str, rel: str, text: str, symbol: str
) -> None:
    assert _edges(tmp_path, language, rel, text) == {(symbol, symbol)}


def test_a_python_bare_call_never_reaches_the_method_calling_it(tmp_path: Path) -> None:
    # A bare name inside a Python method is a module or builtin name, never the method.
    text = "class C:\n    def walk(self, n):\n        return walk(n)\n"
    assert _edges(tmp_path, "python", "m.py", text) == set()


def test_an_overload_of_another_arity_is_not_recursion(tmp_path: Path) -> None:
    text = (
        "class A {\n  int f(int n) { return f(n, 1); }\n"
        "  int f(int n, int m) { return f(n); }\n}\n"
    )
    assert _edges(tmp_path, "java", "A.java", text) == {
        ("A.java::A::f#1", "A.java::A::f#2"),
        ("A.java::A::f#2", "A.java::A::f#1"),
    }


def test_an_overload_its_arguments_pick_is_recursion(tmp_path: Path) -> None:
    text = "class A {\n  int f(int n) { return f(n - 1); }\n  int f(int n, int m) { return n; }\n}\n"
    assert _edges(tmp_path, "java", "A.java", text) == {("A.java::A::f#1", "A.java::A::f#1")}


@pytest.mark.parametrize(
    ("language", "rel", "text"),
    [
        ("csharp", "A.cs", "class A : B {\n  int F(int a, int b) { return F(a); }\n}\n"),
        ("java", "A.java", "class A extends B {\n  int f(int a, int b) { return this.f(a); }\n}\n"),
    ],
    ids=["csharp", "java"],
)
def test_a_self_call_its_parameters_cannot_take_is_no_self_edge(
    tmp_path: Path, language: str, rel: str, text: str
) -> None:
    # F(a) inside F(a, b) means an inherited F, never the caller.
    assert not any(caller == callee for caller, callee in _edges(tmp_path, language, rel, text))


NOT_RECURSION = [
    # The parser drops the receiver of `Outer.this.f()`; the line still shows it.
    (
        "java",
        "A.java",
        "class A {\n  void clear() { }\n  class Keys {\n"
        "    public void clear() {\n      A.this.clear();\n    }\n  }\n}\n",
    ),
    # A chained receiver: `count` here is the iterator's.
    ("rust", "lib.rs", "fn count(b: &[u8]) -> usize {\n    b.iter().count()\n}\n"),
    # An explicit interface member shares its id with the public method it calls.
    (
        "csharp",
        "A.cs",
        "class A : IEnumerable {\n  public IEnumerator<int> GetEnumerator() { return null; }\n"
        "  IEnumerator IEnumerable.GetEnumerator() { return GetEnumerator(); }\n}\n",
    ),
    # An overload picked by arity, called on a chained receiver.
    (
        "java",
        "A.java",
        "class A {\n  A first() { return this; }\n  String val() { return first().val(); }\n"
        "  A val(String v) { return this; }\n}\n",
    ),
    # Namespaces are on no symbol: the bare `f` may be `detail::f`.
    (
        "cpp",
        "m.cpp",
        "namespace detail {\nint f(int n) { return n; }\n}\n"
        "namespace api {\nint f(int n) { return f(n); }\n}\n",
    ),
    # An explicit interface member has no bare name: the call is the public overload's.
    (
        "csharp",
        "A.cs",
        "class A : IV {\n  public int Run(T x, int n = 0) { return n; }\n"
        "  int IV.Run(object x, int n) { return Run(Wrap(x), n); }\n}\n",
    ),
    # A macro around the call may rewrite it, here into the POSIX `::dup`.
    (
        "cpp",
        "m.cpp",
        "class file {\npublic:\n  int dup(int fd);\n};\n"
        "int file::dup(int fd) {\n  return FMT_POSIX_CALL(dup(fd));\n}\n",
    ),
    # A lambda with a receiver rebinds a bare Kotlin name.
    ("kotlin", "A.kt", "fun main() {\n    App().apply { main() }\n}\n"),
    # A function-local import shadows the function's own name.
    (
        "python",
        "m.py",
        "def pager():\n    from other import pager\n\n    return pager()\n",
    ),
]


@pytest.mark.parametrize(
    ("language", "rel", "text"),
    NOT_RECURSION,
    ids=[f"{case[0]}-{i}" for i, case in enumerate(NOT_RECURSION)],
)
def test_a_call_that_only_looks_recursive_draws_no_self_edge(
    tmp_path: Path, language: str, rel: str, text: str
) -> None:
    assert not any(caller == callee for caller, callee in _edges(tmp_path, language, rel, text))


def test_a_method_shadows_a_same_named_top_level_function(tmp_path: Path) -> None:
    text = (
        "int walk(int n) { return n; }\n"
        "class S {\npublic:\n  int walk(int n) { return n ? walk(n - 1) : 0; }\n};\n"
    )
    assert _edges(tmp_path, "cpp", "m.cpp", text) == {("m.cpp::S::walk", "m.cpp::S::walk")}


def test_an_anonymous_class_member_refuses_a_call_its_parameters_cannot_take(
    tmp_path: Path,
) -> None:
    # f(a) inside f(a, b) of an anonymous body is no self edge, and no other edge either.
    text = "class A {\n  Object o = new Object() {\n    int f(int a, int b) { return f(a); }\n  };\n}\n"
    assert _edges(tmp_path, "java", "A.java", text) == set()


def test_an_anonymous_class_member_recursion_is_a_self_edge(tmp_path: Path) -> None:
    text = "class A {\n  Object o = new Object() {\n    int f(int a) { return f(a - 1); }\n  };\n}\n"
    member = "A.java::A::o::Object::f"
    assert _edges(tmp_path, "java", "A.java", text) == {(member, member)}


def test_a_caller_id_two_definitions_share_draws_no_self_edge(tmp_path: Path) -> None:
    # Which of the two the call means is unproven, so the shared id is not recursion.
    text = "def fact(n):\n    return fact(n - 1)\n\ndef fact(n):\n    return fact(n - 1)\n"
    assert _edges(tmp_path, "python", "m.py", text) == set()


def test_a_caller_id_one_definition_holds_draws_a_self_edge(tmp_path: Path) -> None:
    text = "def fact(n):\n    return fact(n - 1)\n"
    assert _edges(tmp_path, "python", "m.py", text) == {("m.py::fact", "m.py::fact")}


def _shared_ids(tmp_path: Path, language: str, rel: str, text: str) -> frozenset[str]:
    from repowise.core.ingestion.call_resolver import _shared_definition_ids

    (tmp_path / rel).write_text(text, encoding="utf-8")
    info = FileInfo(
        path=rel,
        abs_path=str(tmp_path / rel),
        language=language,
        size_bytes=len(text),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return _shared_definition_ids({rel: _PARSER.parse_file(info, text.encode("utf-8"))})


def test_a_declaration_twin_of_one_definition_does_not_share_the_id(tmp_path: Path) -> None:
    text = "class S {\npublic:\n  int walk(int n);\n};\nint S::walk(int n) { return n ? walk(n - 1) : 0; }\n"
    assert _shared_ids(tmp_path, "cpp", "m.cpp", text) == frozenset()
    # The one definition still draws its self edge.
    assert _edges(tmp_path, "cpp", "m.cpp", text) == {("m.cpp::S::walk", "m.cpp::S::walk")}


def test_two_definitions_of_one_id_are_shared(tmp_path: Path) -> None:
    text = "def fact(n):\n    return fact(n - 1)\n\ndef fact(n):\n    return fact(n - 1)\n"
    assert _shared_ids(tmp_path, "python", "m.py", text) == frozenset({"m.py::fact"})


def test_an_overload_representative_calls_a_sibling_not_itself(tmp_path: Path) -> None:
    # f#1 is the set's representative; its call has another arity, so it is no recursion.
    text = (
        "class A {\n  int f(int a) { return f(a, 1, 2); }\n"
        "  int f(int a, int b) { return b; }\n"
        "  int f(int a, int b, int c) { return c; }\n}\n"
    )
    assert _edges(tmp_path, "java", "A.java", text) == {("A.java::A::f#1", "A.java::A::f#3")}
