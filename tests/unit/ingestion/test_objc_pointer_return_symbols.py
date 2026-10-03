"""A C function returning a pointer is still a symbol in an Objective-C file.

``NSString *MakeName(void)`` wraps the ``function_declarator`` in a
``pointer_declarator``, as it does in a ``.c`` file, and ``objectivec.scm``
carries the plain-C shapes ``c.scm`` does.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

_PARSER = ASTParser()


def _parse(src: str):
    return _PARSER.parse_file(_make_file_info("a.m", "objectivec"), src.encode())


def _names(src: str) -> dict[str, str]:
    return {s.name: s.kind for s in _parse(src).symbols}


@pytest.mark.parametrize(
    ("src", "name"),
    [
        ('NSString *MakeName(void) { return @"x"; }', "MakeName"),
        ("NSString **MakeNamePtr(void) { return 0; }", "MakeNamePtr"),
        ("static inline NSArray *MakeItems(id source) { return nil; }", "MakeItems"),
        ("int (*getHandler(int k))(int) { return 0; }", "getHandler"),
        ("NSString *DeclaredOnly(int fd);", "DeclaredOnly"),
        ("char **DeclaredOnly2(void);", "DeclaredOnly2"),
    ],
)
def test_pointer_returning_function_is_a_symbol(src: str, name: str) -> None:
    assert _names(src).get(name) == "function"


def test_a_function_returning_no_pointer_is_still_a_symbol() -> None:
    assert _names("int MakeCount(void) { return 1; }").get("MakeCount") == "function"


def test_function_pointer_variable_is_not_a_function() -> None:
    names = _names("void (*on_close)(int);\nint *(*alloc_fn)(unsigned n);\n")
    assert "on_close" not in names
    assert "alloc_fn" not in names


def test_calls_to_and_from_a_pointer_returning_function_key_to_it() -> None:
    src = (
        "void helper(void) {}\n"
        "NSString *MakeName(void) { helper(); return 0; }\n"
        "void caller(void) { MakeName(); }\n"
    )
    calls = _parse(src).calls
    assert [c.caller_symbol_id for c in calls if c.target_name == "helper"] == ["a.m::MakeName"]
    assert [c.caller_symbol_id for c in calls if c.target_name == "MakeName"] == ["a.m::caller"]
