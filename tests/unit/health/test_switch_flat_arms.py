"""How a switch's arms count: one point for its simple cases together, one per
case with control flow of its own, none for the ``default`` / ``else`` / ``_`` arm.

One ``if`` in one arm used to make every arm count, and the fallthrough arm
counted as a case though it decides nothing.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_EXT = {
    "c": "c",
    "go": "go",
    "java": "java",
    "typescript": "ts",
    "rust": "rs",
    "kotlin": "kt",
    "swift": "swift",
    "csharp": "cs",
    "python": "py",
    "scala": "scala",
    "shell": "sh",
}


def _ccn(language: str, source: str) -> int:
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return fcx.functions[0].ccn


# One arm holds an ``if``; four arms are one-liners; a ``default`` closes it.
# 1 (entry) + 1 (the four simple cases) + 1 (the arm with the if) + 1 (its if).
_MIXED = {
    "c": """int f(int x, int y) {
  switch (x) {
    case 1: if (y) { return 9; } return 1;
    case 2: return 2;
    case 3: return 3;
    case 4: return 4;
    case 5: return 5;
    default: return 0;
  }
}""",
    "go": """package p
func f(x, y int) int {
	switch x {
	case 1:
		if y > 0 { return 9 }
		return 1
	case 2: return 2
	case 3: return 3
	case 4: return 4
	case 5: return 5
	default: return 0
	}
}""",
    "java": """class A { int f(int x, int y) {
  switch (x) {
    case 1: if (y > 0) { return 9; } return 1;
    case 2: return 2;
    case 3: return 3;
    case 4: return 4;
    case 5: return 5;
    default: return 0;
  }
} }""",
    "typescript": """function f(x: number, y: number) {
  switch (x) {
    case 1: if (y) { return 9; } return 1;
    case 2: return 2;
    case 3: return 3;
    case 4: return 4;
    case 5: return 5;
    default: return 0;
  }
}""",
    "rust": """fn f(x: i32, y: bool) -> i32 {
    match x {
        1 => { if y { return 9; } 1 }
        2 => 2,
        3 => 3,
        4 => 4,
        5 => 5,
        _ => 0,
    }
}""",
    "kotlin": """fun f(x: Int, y: Boolean): Int = when (x) {
    1 -> { if (y) 9 else 1 }
    2 -> 2
    3 -> 3
    4 -> 4
    5 -> 5
    else -> 0
}""",
    "swift": """func f(x: Int, y: Bool) -> Int {
    switch x {
    case 1: if y { return 9 }; return 1
    case 2: return 2
    case 3: return 3
    case 4: return 4
    case 5: return 5
    default: return 0
    }
}""",
    "csharp": """class A { int F(int x, bool y) {
  switch (x) {
    case 1: if (y) { return 9; } return 1;
    case 2: return 2;
    case 3: return 3;
    case 4: return 4;
    case 5: return 5;
    default: return 0;
  }
} }""",
    "scala": """object O { def f(x: Int, y: Boolean): Int = x match {
  case 1 => if (y) 9 else 1
  case 2 => 2
  case 3 => 3
  case 4 => 4
  case 5 => 5
  case _ => 0
} }""",
}


@pytest.mark.parametrize("language", sorted(_MIXED))
def test_one_tangled_arm_leaves_the_others_flat(language: str) -> None:
    assert _ccn(language, _MIXED[language]) == 4


def test_python_match_counts_each_case_but_not_the_wildcard() -> None:
    source = """
def f(x):
    match x:
        case 1:
            return 1
        case 2:
            return 2
        case _:
            return 0
"""
    # The same as ``if x == 1 / elif x == 2 / else``.
    assert _ccn("python", source) == 3


def test_shell_catch_all_arm_is_not_a_case() -> None:
    source = """
f() {
  case "$1" in
    a) if [ -n "$2" ]; then x; fi ;;
    *) y ;;
  esac
}
"""
    # The ``a)`` arm and its ``if``; ``*)`` is the fallthrough.
    assert _ccn("shell", source) == 3


def test_a_switch_without_a_subject_and_a_tangled_arm_is_an_if_chain() -> None:
    kotlin = """fun f(x: Int, y: Boolean): Int = when {
    x > 10 -> { if (y) 9 else 1 }
    x > 5 -> 2
    x > 0 -> 3
    else -> 0
}"""
    go = """package p
func f(x int, y bool) int {
	switch {
	case x > 10:
		if y { return 9 }
		return 1
	case x > 5: return 2
	case x > 0: return 3
	default: return 0
	}
}"""
    # Three conditions and the if, as ``if`` / ``else if`` / ``else if`` / ``else``.
    assert _ccn("kotlin", kotlin) == 5
    assert _ccn("go", go) == 5


def test_a_guarded_wildcard_is_a_case() -> None:
    source = """object O { def f(x: Int, y: Boolean): Int = x match {
  case 1 => 1
  case _ if y => 2
  case _ => 0
} }"""
    # The simple case on the match, the guarded arm and its guard.
    assert _ccn("scala", source) == 4
