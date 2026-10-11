"""Extract Method spans carry text anchors: the exact first and last lines, grown
until each is unique in the function, so an agent finds a stage by its text after
earlier stages have moved its lines. A span no short snippet anchors refuses the plan.
"""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.dataflow import analyze_file
from repowise.core.analysis.health.refactoring.anchors import (
    anchor_plan,
    function_lines,
    occurrences,
    span_anchors,
)
from repowise.core.analysis.health.refactoring.extract_method import _serve_anchored
from repowise.core.analysis.health.refactoring.recipe import build_recipe
from repowise.core.analysis.health.refactoring.render import list_plan
from tests.unit.health.test_extract_method_stages import _persist, _plans


def _fn(src: bytes, name: str = "f"):
    from repowise.core.ingestion.parser import _get_language

    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")
    res = analyze_file("m.py", "python", src, flagged_only=False)
    return next(f for f in res.functions if f.name == name)


def _lines_of(src: bytes):
    return function_lines(_fn(src).fn_node)


_REPEATS = b"""\
def f(rows):
    total = 0
    for r in rows:
        total += r
    for r in rows:
        total += r
        total -= 1
    return total
"""


def test_a_repeated_first_line_grows_until_unique():
    lines = _lines_of(_REPEATS)
    # Lines 5-7: "for r in rows:" and "total += r" both occur twice.
    anchors = span_anchors(lines, 5, 7)
    assert anchors == {
        "first": "    for r in rows:\n        total += r\n        total -= 1",
        "last": "        total -= 1",
    }


_TWIN = b"""\
def f(rows):
    for r in rows:
        print(r)
    x = 1
    for r in rows:
        print(r)
"""


def test_a_span_whose_ends_repeat_in_full_is_unanchored():
    assert span_anchors(_lines_of(_TWIN), 5, 6) is None


def test_crlf_lines_lose_only_their_line_ending():
    src = _REPEATS.replace(b"\n", b"\r\n")
    anchors = span_anchors(_lines_of(src), 2, 4)
    # Lines 3-4 repeat as lines 5-6, so the last anchor reaches back to line 2.
    assert anchors == {
        "first": "    total = 0",
        "last": "    total = 0\n    for r in rows:\n        total += r",
    }
    assert "\r" not in anchors["first"] + anchors["last"]


def test_tabs_are_kept_as_written():
    src = b"def f(rows):\n\ttotal = 0\n\tfor r in rows:\n\t\ttotal += r\n\treturn total\n"
    anchors = span_anchors(_lines_of(src), 2, 4)
    assert anchors == {"first": "\ttotal = 0", "last": "\t\ttotal += r"}


def test_a_span_at_the_function_start_and_end():
    # No trailing newline: the span's last line is the file's last.
    src = b"def f(a):\n    x = a + 1\n    y = x * 2\n    return y"
    lines = _lines_of(src)
    assert lines[0] == 1 and lines[1][0] == "def f(a):"
    assert span_anchors(lines, 2, 4) == {"first": "    x = a + 1", "last": "    return y"}
    # A span outside the function has nothing to anchor.
    assert span_anchors(lines, 4, 5) is None


def test_a_nested_function_reads_its_lines_from_column_zero():
    src = b"class A:\n    def f(self):\n        a = 1\n        b = 2\n        return a + b\n"
    lines = function_lines(_fn(src).fn_node)
    assert lines == (2, ["    def f(self):", "        a = 1", "        b = 2", "        return a + b"])


def test_anchor_plan_is_all_or_nothing():
    fn = _fn(_REPEATS).fn_node
    ok = {"span": {"start": 2, "end": 4}, "stages": [{"span": {"start": 2, "end": 4}}]}
    assert anchor_plan(ok, fn)
    assert ok["anchors"] == ok["stages"][0]["anchors"]
    mixed = {"span": {"start": 2, "end": 4}, "stages": [{"span": {"start": 2, "end": 99}}]}
    assert not anchor_plan(mixed, fn)
    assert "anchors" not in mixed and "anchors" not in mixed["stages"][0]


def test_every_stage_of_a_staged_plan_is_anchored_once_in_the_function():
    src = textwrap.dedent(_persist())
    plan = _plans(_persist())["persist"].plan
    rows = src.splitlines()
    body = "\n".join(rows)
    grown = 0
    for stage in plan["stages"]:
        anchors, span = stage["anchors"], stage["span"]
        for text in anchors.values():
            assert occurrences(body, text) == 1
        assert rows[span["start"] - 1] == anchors["first"].split("\n")[0]
        assert rows[span["end"] - 1] == anchors["last"].split("\n")[-1]
        grown += "\n" in anchors["last"]
    # Every section ends on "if strict: raise", so a last line alone repeats.
    assert grown
    assert plan["anchors"] == plan["stages"][0]["anchors"]


def test_anchors_are_detail_only_and_reach_the_recipe():
    plan = _plans(_persist())["persist"].plan
    assert "anchors" not in list_plan(plan)
    recipe = build_recipe(
        {"id": "x", "refactoring_type": "extract_method", "file_path": "m.py",
         "target_symbol": "persist", "plan": plan}
    )
    extracts = [s for s in recipe["steps"] if s["action"] == "extract"]
    assert [s["anchors"] for s in extracts] == [s["anchors"] for s in plan["stages"]]
    assert "starts and ends with the lines shown" in extracts[0]["text"]
    rules = {d["constraint"]: d["reason"] for d in recipe["does_not"]}
    assert "one stage at a time" in rules["generate a script that rewrites the file"]
    assert "find a span by its line numbers" in rules


def test_an_unanchorable_span_refuses_the_plan_with_a_reason():
    fn = _fn(_TWIN).fn_node
    finding = SimpleNamespace(details={})
    out: list = []
    _serve_anchored(out, SimpleNamespace(plan={"span": {"start": 5, "end": 6}}), [finding], fn)
    assert out == [] and finding.details == {"extract_method_refused": "span_unanchored"}
    served = SimpleNamespace(plan={"span": {"start": 2, "end": 4}})
    _serve_anchored(out, served, [finding], fn)
    assert out == [served]
    assert served.plan["anchors"]["first"] == "    for r in rows:\n        print(r)\n    x = 1"
