"""Cross-language tests for the ``complex_conditional`` biomarker."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.complex_conditional import (
    ComplexConditionalDetector,
)
from repowise.core.analysis.health.complexity import walk_file_complexity
from repowise.core.analysis.health.models import Severity

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _walk(rel_path: str, language: str):
    p = FIXTURES / rel_path
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    results = walk_file_complexity(str(p), language, p.read_bytes())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return results


def _ctx(metrics):
    return FileContext(
        file_path="x",
        language="python",
        nloc=10,
        has_test_file=False,
        module=None,
        all_functions=tuple(metrics),
    )


def _find(results, name):
    for r in results:
        if r.name == name:
            return r
    return None


def _assert_three_six_two(metrics, three: str, six: str, two: str):
    # The walker counts every condition; the 3-op and 6-op fixtures are
    # flat ``&&`` chains.
    for name, count in ((three, 3), (six, 6)):
        cond = _find(metrics, name).complex_conditions[0]
        assert cond.operator_count == count
        assert cond.flat_chain is True

    # Flat chains and the 2-op condition produce no finding.
    by_fn = {f.function_name for f in ComplexConditionalDetector().detect(_ctx(metrics))}
    assert not by_fn & {three, six, two}


def test_python_conditions():
    metrics = _walk("python/conditionals.py", "python")
    _assert_three_six_two(metrics, "three_ops", "six_ops", "two_ops")


def test_typescript_conditions():
    metrics = _walk("typescript/conditionals.ts", "typescript")
    _assert_three_six_two(metrics, "threeOps", "sixOps", "twoOps")


def test_go_conditions():
    metrics = _walk("go/conditionals.go", "go")
    _assert_three_six_two(metrics, "ThreeOps", "SixOps", "TwoOps")


def test_java_conditions():
    metrics = _walk("java/Conditionals.java", "java")
    _assert_three_six_two(metrics, "threeOps", "sixOps", "twoOps")


def test_rust_conditions():
    metrics = _walk("rust/conditionals.rs", "rust")
    _assert_three_six_two(metrics, "three_ops", "six_ops", "two_ops")


def test_walker_emits_complex_conditions_field():
    """Lock the new walker contract — assertion lives here so the
    cross-language fixtures double as walker coverage."""
    metrics = _walk("python/conditionals.py", "python")
    fc = _find(metrics, "six_ops")
    assert fc is not None
    assert len(fc.complex_conditions) == 1
    cond = fc.complex_conditions[0]
    assert cond.operator_count == 6
    assert cond.enclosing_construct == "while"
    # Existing CCN field must still be populated (regression guard).
    assert fc.ccn >= 7  # entry + while + 6 booleans


def _conditions(source: str, language: str, name: str):
    results = walk_file_complexity(f"x.{language}", language, source.encode())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    fc = _find(results, name)
    assert fc is not None
    return fc


@pytest.mark.parametrize(
    ("condition", "count", "flat"),
    [
        ("a === 1 || b === 2 || c === 3 || d === 4 || e === 5 || g === 6 || h === 7", 6, True),
        ("a && b && c && d && e && g && h", 6, True),
        ("a && b || c && d || e && g || h", 6, False),
        ("a || b || c || !d || e || g || h", 6, False),
        ("a !== b || c != d || e || g || h || i || j!", 6, True),
    ],
)
def test_flat_chain_typescript(condition: str, count: int, flat: bool):
    src = f"function f(a, b, c, d, e, g, h, i, j) {{ if ({condition}) {{ return 1; }} return 0; }}"
    cond = _conditions(src, "typescript", "f").complex_conditions[0]
    assert cond.operator_count == count
    assert cond.flat_chain is flat


@pytest.mark.parametrize(
    ("condition", "flat"),
    [
        ("a or b or c or d", True),
        ("a and b and c is not None and d not in e", True),
        ("a and b or c and d", False),
        ("a and not b and c and d", False),
    ],
)
def test_flat_chain_python(condition: str, flat: bool):
    src = f"def f(a, b, c, d, e):\n    if {condition}:\n        return 1\n    return 0\n"
    cond = _conditions(src, "python", "f").complex_conditions[0]
    assert cond.operator_count == 3
    assert cond.flat_chain is flat


def test_flat_chain_has_no_finding_and_mixed_keeps_count_severity():
    src = (
        "function flat(a, b, c, d, e, g, h) { if (a || b || c || d || e || g || h) { return 1; } }\n"
        "function knot(a, b, c, d, e, g, h) { if (a && b || c && !d || e && g || h) { return 1; } }\n"
        "function mixedLow(a, b, c, d) { if (a && b || c && d) { return 1; } }\n"
        "function negated(a, b, c, d) { if (a || !b || c || d) { return 1; } }\n"
    )
    results = walk_file_complexity("x.ts", "typescript", src.encode())
    if not results:
        pytest.skip("tree-sitter language pack missing for typescript")
    by_fn = {f.function_name: f for f in ComplexConditionalDetector().detect(_ctx(results))}
    assert "flat" not in by_fn
    assert by_fn["knot"].severity == Severity.CRITICAL
    assert by_fn["mixedLow"].severity == Severity.LOW
    assert by_fn["negated"].severity == Severity.LOW


def test_python_flat_chain_has_no_finding_and_mixed_flags():
    src = (
        "def flat(a, b, c, d, e):\n    if a or b or c or d or e:\n        return 1\n\n"
        "def mixed(a, b, c, d):\n    if a and b or c and d:\n        return 1\n"
    )
    results = walk_file_complexity("x.py", "python", src.encode())
    if not results:
        pytest.skip("tree-sitter language pack missing for python")
    by_fn = {f.function_name: f for f in ComplexConditionalDetector().detect(_ctx(results))}
    assert set(by_fn) == {"mixed"}
    assert by_fn["mixed"].severity == Severity.LOW


@pytest.mark.parametrize(
    ("body", "count"),
    [
        ("x = a and b if c or d else e or g", 1),
        ("x = (a and b and c) if d else (e or g)", 0),
        ("x = a if b and c or d and e else g", 3),
    ],
)
def test_python_ternary_counts_condition_only(body: str, count: int):
    src = f"def f(a, b, c, d, e, g):\n    {body}\n    return x\n"
    conds = _conditions(src, "python", "f").complex_conditions
    assert [c.operator_count for c in conds] == ([count] if count else [])


@pytest.mark.parametrize(
    ("language", "src"),
    [
        (
            "python",
            "def f(v, a, b, c):\n    match v:\n        case 1 if a and b and c:\n            return 1\n    return 0\n",
        ),
        (
            "scala",
            "object O { def f(v: Int, a: Boolean, b: Boolean, c: Boolean): Int = v match {\n"
            "  case 1 if a && b && c => 1\n  case _ => 0\n} }\n",
        ),
        (
            "scala",
            "object O { def f(xs: List[Int], a: Boolean, b: Boolean, c: Boolean) = "
            "{ for (x <- xs; if a && b && c) println(x) } }\n",
        ),
        (
            "swift",
            "func f(xs: [Int], a: Bool, b: Bool, c: Bool) {\n for x in xs where a && b && c { print(x) }\n}\n",
        ),
    ],
)
def test_guard_counts_once(language: str, src: str):
    conds = _conditions(src, language, "f").complex_conditions
    assert [c.operator_count for c in conds] == [2]


@pytest.mark.parametrize(
    ("language", "src"),
    [
        ("python", "def f(a, b, c):\n    for x in (a and b or c):\n        print(x)\n"),
        ("python", "async def f(a, b, c):\n    async for x in (a and b or c):\n        print(x)\n"),
        ("typescript", "function f(a, b, c) { for (const x of (a && b || c)) { g(x); } }\n"),
        ("java", "class C { void f(boolean a, boolean b) { for (Object x : h(a && b || a)) { g(x); } } }\n"),
        ("csharp", "class C { void f(bool a, bool b) { foreach (var x in H(a && b || a)) { G(x); } } }\n"),
        ("kotlin", "fun f(a: Boolean, b: Boolean) { for (x in h(a && b || a)) g(x) }\n"),
        ("ruby", "def f(a, b, c)\n  for x in (a && b || c)\n    g(x)\n  end\nend\n"),
        ("rust", "fn f(a: bool, b: bool) { for x in h(a && b || a) { g(x); } }\n"),
        ("swift", "func f(a: Bool, b: Bool) {\n for x in h(a && b || a) { g(x) }\n}\n"),
        ("cpp", "void f(bool a, bool b) { for (auto x : h(a && b || a)) { g(x); } }\n"),
        ("php", "<?php function f($a, $b) { foreach (h($a && $b || $a) as $x) { g($x); } }\n"),
        ("scala", "object O { def f(a: Boolean, b: Boolean) = { for (x <- h(a && b || a)) g(x) } }\n"),
    ],
)
def test_foreach_iterable_is_not_a_condition(language: str, src: str):
    assert _conditions(src, language, "f").complex_conditions == []


def test_while_condition_still_counted():
    src = "def f(a, b, c):\n    while a and b or c:\n        pass\n"
    (cond,) = _conditions(src, "python", "f").complex_conditions
    assert (cond.operator_count, cond.enclosing_construct) == (2, "while")


def test_reason_operator_count_matches_details_and_code():
    # Three operators in the condition, one more in each arm.
    src = "def f(a, b, c, d, e, g):\n    x = a or b if c and d or e and g else a and b\n    return x\n"
    results = walk_file_complexity("x.py", "python", src.encode())
    if not results:
        pytest.skip("tree-sitter language pack missing for python")
    (finding,) = ComplexConditionalDetector().detect(_ctx(results))
    assert finding.details["operator_count"] == 3
    assert "combines 3 boolean operators" in finding.reason
