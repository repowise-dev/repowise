"""Two per-function walker facts: ``dispatch_share`` and ``deprecated``.

``dispatch_share`` is the CCN of the largest top-level multiway branch on one
subject over the function's CCN. ``deprecated`` is a declaration marker or a
top-level deprecation warning. Neither moves a CCN, threshold or score.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.duplication import DuplicationReport
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.models import HealthFindingData, Severity

_EXT = {
    "python": "py",
    "typescript": "ts",
    "javascript": "js",
    "go": "go",
    "java": "java",
    "csharp": "cs",
    "rust": "rs",
    "kotlin": "kt",
    "ruby": "rb",
}


def _functions(language: str, source: str) -> dict:
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}


# --------------------------------------------------------------------------
# dispatch_share
# --------------------------------------------------------------------------

_PY = '''
def visit(node):
    if isinstance(node, A):
        return 1
    elif isinstance(node, B) or isinstance(node, C):
        return 2
    elif isinstance(node, D):
        return 3
    else:
        return 4

def matcher(tokens, flag):
    for t in tokens:
        match t.kind:
            case "a":
                x()
            case "b":
                y()
            case _:
                z()
    if flag:
        w()

def mixed(x, y):
    if x == 1:
        return 1
    elif y == 2:
        return 2
    elif x == 3:
        return 3

def short_chain(x):
    if x == 1:
        return 1
    elif x == 2:
        return 2

def nested(x, ready):
    if ready:
        match x:
            case 1:
                a()
            case 2:
                b()
'''


def test_python_if_chain_on_one_subject() -> None:
    fns = _functions("python", _PY)
    # Three arms plus one ``or`` out of CCN 5.
    assert fns["visit"].ccn == 5
    assert fns["visit"].dispatch_share == 0.8


def test_python_match_inside_a_loop_counts() -> None:
    fns = _functions("python", _PY)
    # for (1) + three cases (3) + if (1) + entry (1) = 6; the match is 3 of them.
    assert fns["matcher"].ccn == 6
    assert fns["matcher"].dispatch_share == 0.5


@pytest.mark.parametrize("name", ["mixed", "short_chain", "nested"])
def test_python_negatives(name: str) -> None:
    # Two subjects, a two-arm chain, and a match nested under a branch.
    assert _functions("python", _PY)[name].dispatch_share == 0.0


def test_typescript_switch_and_member_chain() -> None:
    fns = _functions(
        "typescript",
        """
function sw(x: Node) {
  switch (x.kind) {
    case 1: if (a) { b(); } break;
    case 2: c(); break;
    case 3: d(); break;
  }
}
function chain(x) {
  if (x.k === 1) {} else if (x.k === 2) {} else if (x.k === 3 || x.k === 4) {}
}
function two(x, y) {
  if (x.k === 1) {} else if (y.k === 2) {} else if (x.k === 3) {}
}
""",
    )
    # Three counted arms + the nested if + entry = 5.
    assert fns["sw"].ccn == 5
    assert fns["sw"].dispatch_share == 0.6
    assert fns["chain"].dispatch_share == 0.8
    assert fns["two"].dispatch_share == 0.0


def test_go_switch_needs_a_subject() -> None:
    fns = _functions(
        "go",
        """package m
func F(x int) int {
	switch x {
	case 1:
		if a { return 1 }
	case 2:
		return 2
	case 3:
		return 3
	}
	return 0
}
func H(x int) int {
	switch {
	case x > 1:
		if a { return 1 }
	case x < 0:
		return 2
	}
	return 0
}
""",
    )
    assert fns["F"].dispatch_share == 0.6
    assert fns["H"].dispatch_share == 0.0


def test_java_switch_and_equals_chain() -> None:
    fns = _functions(
        "java",
        """class A {
  int f(int x) { switch (x) { case 1: if (a) return 1; case 2: return 2; } return 0; }
  int g(String cmd) {
    if ("a".equals(cmd)) {} else if ("b".equals(cmd)) {} else if ("c".equals(cmd)) {}
    return 0;
  }
}""",
    )
    assert fns["f"].dispatch_share == 0.5
    assert fns["g"].dispatch_share == 0.75


def test_csharp_flat_switch_expression_is_one_point() -> None:
    fns = _functions(
        "csharp",
        "class A { int F(int x) { return x switch { 1 => 1, 2 => 2, _ => 3 }; } }",
    )
    # A flat switch charges one point, so it is one point of the share too.
    assert fns["F"].ccn == 2
    assert fns["F"].dispatch_share == 0.5


def test_rust_match() -> None:
    fns = _functions(
        "rust",
        "fn f(x: E) -> i32 { match x { E::A => { if a { 1 } else { 2 } } E::B => 2, E::C => 3 } }",
    )
    assert fns["f"].dispatch_share == 0.6


def test_kotlin_when_needs_a_subject() -> None:
    fns = _functions(
        "kotlin",
        """fun f(x: Int): Int { if (a) {}; return when (x) { 1 -> { if (b) 1 else 2 } 2 -> 3 else -> 4 } }
fun g(x: Int): Int { return when { x > 1 -> { if (b) 1 else 2 } x < 0 -> 3 else -> 4 } }""",
    )
    assert fns["f"].dispatch_share == 0.5
    assert fns["g"].dispatch_share == 0.0


def test_ruby_case_needs_a_subject() -> None:
    fns = _functions(
        "ruby",
        """def f(x)
  case x
  when 1 then if a then 1 end
  when 2 then 2
  end
end
def g(x)
  case
  when x > 1 then if a then 1 end
  when x < 0 then 2
  end
end
""",
    )
    assert fns["f"].dispatch_share == 0.5
    assert fns["g"].dispatch_share == 0.0


# --------------------------------------------------------------------------
# deprecated
# --------------------------------------------------------------------------

_PY_DEPRECATED = '''
import warnings

def old():
    """Doc."""
    warnings.warn("old is deprecated", DeprecationWarning, stacklevel=2)
    return 1

@typing_extensions.deprecated("use new")
def decorated():
    pass

@deprecated
def bare_decorated():
    pass

def param(x=None):
    if x:
        warnings.warn("x is deprecated", DeprecationWarning)

def warn_deprecated(msg):
    warnings.warn(msg, DeprecationWarning)

def plain():
    warnings.warn("slow path", RuntimeWarning)

def finder(c):
    return find_deprecated_settings(c)
'''


@pytest.mark.parametrize("name", ["old", "decorated", "bare_decorated"])
def test_python_deprecated(name: str) -> None:
    assert _functions("python", _PY_DEPRECATED)[name].deprecated is True


@pytest.mark.parametrize("name", ["param", "warn_deprecated", "plain", "finder"])
def test_python_not_deprecated(name: str) -> None:
    # A warning in a branch deprecates a parameter, a helper that warns for
    # others is not itself deprecated, another warning category is not one, and
    # returning a value from a deprecation helper is not a warning.
    assert _functions("python", _PY_DEPRECATED)[name].deprecated is False


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("java", "class A { @Deprecated int f() { return 1; } int g() { return 1; } }"),
        ("kotlin", '@Deprecated("x")\nfun f(): Int { return 1 }\nfun g(): Int { return 1 }'),
        ("csharp", 'class A { [Obsolete("x")] int f() { return 1; } int g() { return 1; } }'),
        ("rust", '#[deprecated(since = "1.0")]\nfn f() -> i32 { 1 }\nfn g() -> i32 { 1 }'),
        (
            "typescript",
            "/** Old.\n * @deprecated use g */\nexport function f() { return 1 }\n"
            "/** Current. */\nexport function g() { return 1 }",
        ),
        (
            "go",
            "package m\n// F is old.\n//\n// Deprecated: use G.\nfunc F() int { return 1 }\n"
            "// G is current.\nfunc G() int { return 1 }",
        ),
        ("javascript", 'function f() { console.warn("f is deprecated"); }\nfunction g() { return 1 }'),
        ("ruby", 'def f\n  warn "f is deprecated"\nend\ndef g\n  1\nend\n'),
    ],
)
def test_deprecated_marker_per_language(language: str, source: str) -> None:
    fns = _functions(language, source)
    first, second = ("F", "G") if language == "go" else ("f", "g")
    assert fns[first].deprecated is True
    assert fns[second].deprecated is False


def test_a_deprecated_option_in_an_inline_type_is_not_the_function() -> None:
    fns = _functions(
        "typescript",
        """/** Serve files. */
export const serveStatic = (options: {
  /** @deprecated use root */
  path?: string
}) => { return options }
""",
    )
    assert fns["serveStatic"].deprecated is False


# --------------------------------------------------------------------------
# Engine: details, metric origin, stable ids
# --------------------------------------------------------------------------

_TANGLED = b"""
import warnings

def tangled(x):
    warnings.warn("tangled is deprecated", DeprecationWarning)
    total = 0
    if x == 1:
        total += 1
    elif x == 2:
        total += 2
    elif x == 3:
        total += 3
    elif x == 4:
        total += 4
    elif x == 5:
        total += 5
    elif x == 6:
        total += 6
    elif x == 7:
        total += 7
    elif x == 8:
        total += 8
    elif x == 9:
        total += 9
    return total

def fine():
    return 1
"""


def _evaluate(path: str):
    fcx = walk_file("/tmp/tangled.py", "python", _TANGLED)
    if not fcx.functions:
        pytest.skip("python grammar unavailable")
    pf = SimpleNamespace(
        file_info=SimpleNamespace(
            path=path, language="python", abs_path="/tmp/tangled.py", is_test=False
        ),
        symbols=[],
    )
    return HealthAnalyzer(graph=None)._evaluate_file(
        pf,
        fcx,
        paired_tests=set(),
        package_roots=set(),
        disabled=[],
        dup_report=DuplicationReport(),
    )


def test_complexity_findings_carry_dispatch_share_and_deprecated() -> None:
    _, findings, _ = _evaluate("src/tangled.py")
    complex_method = next(f for f in findings if f.biomarker_type == "complex_method")
    assert complex_method.details["dispatch_share"] == 0.9
    assert complex_method.details["deprecated"] is True


def test_metric_carries_code_origin() -> None:
    assert _evaluate("src/tangled.py")[0].code_origin == "production"
    assert _evaluate("docs_src/tutorial/tangled.py")[0].code_origin == "docs_example"


def test_new_detail_keys_leave_the_finding_id_unchanged() -> None:
    base = HealthFindingData(
        biomarker_type="complex_method",
        severity=Severity.HIGH,
        file_path="src/a.py",
        function_name="f",
        line_start=1,
        line_end=30,
        details={"ccn": 12, "cognitive": 20, "nloc": 30},
        health_impact=1.0,
    )
    annotated = HealthFindingData(
        **{**base.__dict__, "details": {**base.details, "dispatch_share": 0.8, "deprecated": True}}
    )
    assert finding_public_id(annotated) == finding_public_id(base)
