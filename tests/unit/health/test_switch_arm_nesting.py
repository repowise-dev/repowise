"""A switch / match / when opens one nesting level; its case arms open none.

``if { switch { case: if {} } }`` is three levels deep and costs 6 cognitive
(if +1, switch +2, inner if +3), in every language that has case arms. Arms
still count toward CCN.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file_complexity

_C_LIKE_SWITCH = b"""%s f(%s) {
  if (y > 0) {
    switch (x) {
      case 1:
        if (y > 2) { return 1; }
        return 3;
      default: return 0;
    }
  }
  return 0;
}"""

# language -> (file name, source, expected ccn)
_SOURCES = {
    "c": ("a.c", _C_LIKE_SWITCH % (b"int", b"int x, int y"), 5),
    "cpp": ("a.cpp", _C_LIKE_SWITCH % (b"int", b"int x, int y"), 5),
    "javascript": ("a.js", _C_LIKE_SWITCH % (b"function", b"x, y"), 5),
    "typescript": ("a.ts", _C_LIKE_SWITCH % (b"function", b"x: number, y: number"), 5),
    "java": ("A.java", b"class A { " + _C_LIKE_SWITCH % (b"int", b"int x, int y") + b" }", 5),
    "csharp": ("A.cs", b"class A { " + _C_LIKE_SWITCH % (b"int", b"int x, int y") + b" }", 5),
    "php": (
        "a.php",
        b"<?php function f($x, $y) { if ($y > 0) { switch ($x) {"
        b" case 1: if ($y > 2) { return 1; } return 3; default: return 0; } } return 0; }",
        4,
    ),
    "go": (
        "a.go",
        b"package a\nfunc f(x int, y int) int {\n if y > 0 {\n  switch x {\n  case 1:\n"
        b"   if y > 2 { return 1 }\n   return 3\n  default:\n   return 0\n  }\n }\n return 0\n}\n",
        5,
    ),
    "swift": (
        "a.swift",
        b"func f(x: Int, y: Int) -> Int {\n if y > 0 {\n  switch x {\n  case 1:\n"
        b"   if y > 2 { return 1 }\n   return 3\n  default:\n   return 0\n  }\n }\n return 0\n}\n",
        5,
    ),
    "rust": (
        "a.rs",
        b"fn f(x: i32, y: i32) -> i32 {\n if y > 0 {\n  match x {\n"
        b"   1 => { if y > 2 { return 1; } 3 }\n   _ => 0,\n  }\n } else { 0 }\n}\n",
        5,
    ),
    "kotlin": (
        "a.kt",
        b"fun f(x: Int, y: Int): Int {\n if (y > 0) {\n  when (x) {\n"
        b"   1 -> { if (y > 2) { return 1 }; return 3 }\n   else -> return 0\n  }\n }\n return 0\n}\n",
        5,
    ),
    "ruby": (
        "a.rb",
        b"def f(x, y)\n  if y > 0\n    case x\n    when 1\n      if y > 2\n        return 1\n"
        b"      end\n      3\n    else\n      0\n    end\n  end\nend\n",
        4,
    ),
    "python": (
        "a.py",
        b"def f(x, y):\n    if y > 0:\n        match x:\n            case 1:\n"
        b"                if y > 2:\n                    return 1\n                return 3\n"
        b"            case _:\n                return 0\n    return 0\n",
        5,
    ),
}


def _walk_one(name: str, language: str, source: bytes):
    results = walk_file_complexity(name, language, source)
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    (fn,) = results
    return fn


@pytest.mark.parametrize("language", sorted(_SOURCES))
def test_case_arm_adds_no_nesting_level(language: str) -> None:
    name, source, ccn = _SOURCES[language]
    fn = _walk_one(name, language, source)
    assert (fn.max_nesting, fn.cognitive, fn.ccn) == (3, 6, ccn)


def test_java_arrow_switch_expression() -> None:
    source = b"""class B { int f(int x, int y) {
  if (y > 0) {
    return switch (x) {
      case 1 -> { if (y > 2) { yield 1; } yield 3; }
      default -> 0;
    };
  }
  return 0;
} }"""
    fn = _walk_one("B.java", "java", source)
    assert (fn.max_nesting, fn.cognitive) == (3, 6)


def test_scala_match_guard_adds_no_level() -> None:
    source = b"""object A { def f(x: Int, y: Int): Int = {
  if (y > 0) {
    x match {
      case 1 if y > 5 => { if (y > 2) { return 1 }; 3 }
      case _ => 0
    }
  } else 0
} }"""
    fn = _walk_one("a.scala", "scala", source)
    # The guard is a flat +1 cognitive, no level.
    assert (fn.max_nesting, fn.cognitive) == (3, 7)


def test_scala_catch_case_still_nests() -> None:
    # A ``catch`` handler is a ``case_clause`` with no ``match`` above it:
    # it opens its body's level like any catch.
    source = b"""object A { def f(y: Int): Int = {
  try { y } catch { case e: Exception => if (y > 2) { 1 } else { 0 } }
} }"""
    fn = _walk_one("a.scala", "scala", source)
    assert fn.max_nesting == 2


def test_python_nesting_without_switch_unchanged() -> None:
    source = (
        b"def g(x, y):\n    if y > 0:\n        for i in x:\n            while y:\n"
        b"                if i:\n                    return 1\n    return 0\n"
    )
    fn = _walk_one("b.py", "python", source)
    assert (fn.max_nesting, fn.cognitive) == (4, 10)
