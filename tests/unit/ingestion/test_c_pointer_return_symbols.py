"""A function whose declarator returns a pointer or a reference is still a symbol.

``client *createClient(...)`` wraps the ``function_declarator`` in a
``pointer_declarator``; C++ ``Foo& get()`` wraps it in a
``reference_declarator``. Each shape must yield a function symbol, and the
calls in its body must key to it as their caller.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

_PARSER = ASTParser()


def _parse(src: str, language: str):
    path = "a.c" if language == "c" else "a.cpp"
    return _PARSER.parse_file(_make_file_info(path, language), src.encode())


def _names(src: str, language: str) -> dict[str, str]:
    return {s.name: s.kind for s in _parse(src, language).symbols}


_SHARED_SHAPES = [
    ("client *createClient(int fd) { return 0; }", "createClient"),
    ("char **splitArgs(const char *s) { return 0; }", "splitArgs"),
    ("static inline struct entry *dictFind(void *d) { return 0; }", "dictFind"),
    ("const char * const *names(void) { return 0; }", "names"),
    ("int (*getHandler(int k))(int) { return 0; }", "getHandler"),
    ("client *declOnly(int fd);", "declOnly"),
    ("char **declOnly2(void);", "declOnly2"),
]


@pytest.mark.parametrize("language", ["c", "cpp"])
@pytest.mark.parametrize(("src", "name"), _SHARED_SHAPES)
def test_pointer_returning_function_is_a_symbol(language: str, src: str, name: str) -> None:
    assert _names(src, language).get(name) == "function"


@pytest.mark.parametrize(
    ("src", "name", "kind"),
    [
        ("struct Foo {}; Foo& get() { static Foo f; return f; }", "get", "function"),
        ("struct Foo {}; Foo&& moved() { return Foo(); }", "moved", "function"),
        ("struct Foo {}; Foo& declared(int);", "declared", "function"),
        ("Foo* Bar::make(int a) { return 0; }", "make", "method"),
        ("Foo& Bar::ref() { return *this; }", "ref", "method"),
        ("Foo& Foo::operator=(const Foo& o) { return *this; }", "operator=", "method"),
        ("namespace ns { Foo* A::B::make() { return 0; } }", "make", "method"),
        ("class Bar { Foo* inlineGet() { return 0; } };", "inlineGet", "method"),
    ],
)
def test_cpp_pointer_and_reference_returns(src: str, name: str, kind: str) -> None:
    assert _names(src, "cpp").get(name) == kind


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_function_pointer_variable_is_not_a_function(language: str) -> None:
    src = "void (*on_close)(int);\nint *(*alloc_fn)(unsigned n);\n"
    names = _names(src, language)
    assert "on_close" not in names
    assert "alloc_fn" not in names


def test_call_in_pointer_returning_body_keys_to_it() -> None:
    src = "void helper(void) {}\nclient *createClient(int fd) { helper(); return 0; }\n"
    calls = _parse(src, "c").calls
    assert [c.caller_symbol_id for c in calls if c.target_name == "helper"] == [
        "a.c::createClient"
    ]
