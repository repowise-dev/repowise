"""A buffer or a set is built once per loop, so its plan is one step per loop.

``buffer_string_accumulation`` and ``replace_membership_collection`` had one
step per source line: a loop with eight ``out +=`` lines gave eight identical
steps and an effort of eight edits (#3268). The edit is one per loop and
variable, and the step names the variable when the walk kept it.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.refactoring.performance_fix import (
    _site_locations,
    fix_steps,
    performance_fix_suggestions,
)
from tests.unit.health.test_perf_opportunities import _finding

_BUFFER = "buffer_string_accumulation"
_MEMBERSHIP = "replace_membership_collection"
_CONCAT = "string_concat_in_loop"
_PROBE = "membership_test_against_list_in_loop"


def _evidence(lines, *, loop_line=5, subject=None, path="a.py"):
    return [
        {
            "file_path": path,
            "function_name": "f",
            "line_start": line,
            "line_end": line,
            **({"loop_line": loop_line} if loop_line else {}),
            **({"subject": subject} if subject else {}),
        }
        for line in lines
    ]


def _steps(strategy, evidence, safety="judgment"):
    return fix_steps(strategy, safety, None, None, _site_locations(evidence))


def _plan(rows, **options):
    opportunities = build_performance_opportunities(rows, **options)
    assert len(opportunities) == 1
    (suggestion,) = performance_fix_suggestions(opportunities)
    return suggestion


# -- the steps ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("strategy", "wording"),
    [
        (_BUFFER, "Accumulate into a list and join once after the loop"),
        (_MEMBERSHIP, "Build a set once before the loop and probe it"),
    ],
)
def test_the_report_s_eight_lines_in_one_loop_are_one_step(strategy, wording):
    steps = _steps(strategy, _evidence(range(10, 18)))

    assert [(s["order"], s["action"], s["line"], s["loop_line"]) for s in steps] == [
        (1, wording, 5, 5)
    ]


@pytest.mark.parametrize(
    ("strategy", "name", "action"),
    [
        (_BUFFER, "out", "Collect `out` in a list and join it once after the loop"),
        (_BUFFER, "self.buf", "Collect `self.buf` in a list and join it once after the loop"),
        (_MEMBERSHIP, "seen", "Build a set from `seen` once before the loop and probe it"),
    ],
)
def test_the_step_names_the_variable(strategy, name, action):
    steps = _steps(strategy, _evidence(range(10, 14), subject=name))

    assert [s["action"] for s in steps] == [action]


def test_two_variables_in_one_loop_are_two_steps():
    evidence = (
        _evidence([10, 12], subject="out")
        + _evidence([11, 13], subject="log")
        + _evidence([14], subject="out")
    )

    steps = _steps(_BUFFER, evidence)

    assert [(s["order"], s["action"], s["line"]) for s in steps] == [
        (1, "Collect `out` in a list and join it once after the loop", 5),
        (2, "Collect `log` in a list and join it once after the loop", 5),
    ]


def test_two_loops_in_one_function_are_two_steps():
    evidence = _evidence([10, 11], subject="out") + _evidence(
        [22, 23, 24], loop_line=20, subject="out"
    )

    steps = _steps(_BUFFER, evidence)

    assert [(s["line"], s["loop_line"], s["symbol"]) for s in steps] == [(5, 5, "f"), (20, 20, "f")]


def test_the_same_loop_line_in_another_file_is_another_loop():
    evidence = _evidence([10, 11], subject="out") + _evidence([10], subject="out", path="b.py")

    assert [(s["file_path"], s["line"]) for s in _steps(_BUFFER, evidence)] == [
        ("a.py", 5),
        ("b.py", 5),
    ]


def test_sites_with_no_name_share_one_step_with_today_s_wording():
    """Beside a named variable in the same loop, they are their own edit."""
    evidence = _evidence([10, 11], subject="out") + _evidence([12, 13])

    assert [s["action"] for s in _steps(_BUFFER, evidence)] == [
        "Collect `out` in a list and join it once after the loop",
        "Accumulate into a list and join once after the loop",
    ]


def test_evidence_with_no_loop_line_stays_one_step_per_site():
    """Stored before the walk kept the loop: nothing says which loop a site is in."""
    steps = _steps(_BUFFER, _evidence([10, 11, 12], loop_line=None, subject="out"))

    assert [s["line"] for s in steps] == [10, 11, 12]
    assert all("loop_line" not in s for s in steps)
    assert {s["action"] for s in steps} == {
        "Collect `out` in a list and join it once after the loop"
    }


def test_a_proven_plan_s_loop_step_is_mechanical():
    assert [s["applicability"] for s in _steps(_BUFFER, _evidence([10, 11]), "proven")] == [
        "mechanical"
    ]
    assert [s["applicability"] for s in _steps(_BUFFER, _evidence([10, 11]))] == ["judgment"]


def test_the_other_strategies_keep_one_step_per_site():
    evidence = [{**item, "sink_call": "db.get"} for item in _evidence([10, 11, 12], subject="x")]

    steps = fix_steps("batch_or_prefetch_io", "judgment", None, None, _site_locations(evidence))

    assert [(s["line"], s["loop_line"]) for s in steps] == [(10, 5), (11, 5), (12, 5)]
    assert all("db.get" in s["action"] for s in steps)


def test_the_locations_still_list_every_line_and_carry_the_name():
    locations = _site_locations(_evidence([10, 11], subject="out"))

    assert [(loc["line_start"], loc["loop_line"], loc["subject"]) for loc in locations] == [
        (10, 5, "out"),
        (11, 5, "out"),
    ]
    assert "subject" not in _site_locations(_evidence([10]))[0]


# -- from a finding to the plan ---------------------------------------------------


def test_eight_lines_in_one_loop_are_one_edit_of_effort():
    rows = [
        _finding("a.py", line, marker=_CONCAT, boundary="", loop_line=5, subject="out")
        for line in range(10, 18)
    ]

    plan = _plan(rows)

    assert plan.plan["strategy"] == _BUFFER
    assert [s["action"] for s in plan.plan["steps"]] == [
        "Collect `out` in a list and join it once after the loop"
    ]
    assert len(plan.plan["affected_locations"]) == 8
    assert plan.effort_bucket == "S"


def test_effort_counts_loops_and_variables():
    rows = (
        [_finding("a.py", n, marker=_CONCAT, boundary="", loop_line=5, subject="out") for n in (6, 7)]
        + [_finding("a.py", n, marker=_CONCAT, boundary="", loop_line=5, subject="log") for n in (8, 9)]
        + [_finding("a.py", n, marker=_CONCAT, boundary="", loop_line=20, subject="out") for n in (21, 22)]
    )

    plan = _plan(rows)

    assert len(plan.plan["steps"]) == 3
    assert plan.effort_bucket == "M"


def test_sites_past_the_evidence_cap_still_count_one_each():
    """Six lines of one loop, four of them shown: the four are one edit, and
    nothing says the other two are in that loop."""
    rows = [
        _finding("a.py", line, marker=_CONCAT, boundary="", loop_line=5, subject="out")
        for line in range(10, 16)
    ]

    plan = _plan(rows, evidence_limit=4)

    assert len(plan.plan["affected_locations"]) == 4
    assert len(plan.plan["steps"]) == 1
    assert plan.effort_bucket == "M"


def test_a_per_site_strategy_s_effort_is_what_it_was():
    """Three call sites of one helper are three site steps and the bulk form:
    four edits, ``L``. Three alone would be ``M``."""
    sink = ("shared.py::load", "git.py::head")
    rows = [
        _finding("a.py", line, loop_line=8, call_path=("a.py::run", *sink)) for line in (10, 11, 12)
    ]

    plan = _plan(rows)

    assert len(plan.plan["steps"]) == 4
    assert plan.effort_bucket == "L"


# -- the walk keeps the name ------------------------------------------------------


def _subjects(path, language, source):
    return [
        (hit.kind, hit.line, hit.subject)
        for hit in walk_file(path, language, source.encode()).perf_hits
    ]


def test_python_records_the_string_and_the_list():
    source = (
        "def f(rows):\n"
        "    out = ''\n"
        "    big = [1, 2, 3]\n"
        "    for r in rows:\n"
        "        out += 'a'\n"
        "        self.buf += 'b'\n"
        "        if r in big:\n"
        "            out += 'c'\n"
        "        if r not in big:\n"
        "            parts[0] += 'd'\n"
        "    return out\n"
    )

    assert _subjects("a.py", "python", source) == [
        (_CONCAT, 5, "out"),
        (_CONCAT, 6, "self.buf"),
        (_PROBE, 7, "big"),
        (_CONCAT, 8, "out"),
        (_PROBE, 9, "big"),
        # A subscript is not a name a step can quote.
        (_CONCAT, 10, ""),
    ]


def test_typescript_records_the_string_and_the_array():
    source = (
        "export function f(rows: string[]) {\n"
        "  let out = '';\n"
        "  const big = [1, 2, 3];\n"
        "  for (const r of rows) {\n"
        "    out += 'a';\n"
        "    this.buf += 'b';\n"
        "    if (big.includes(r)) { out += 'c'; }\n"
        "  }\n"
        "  return out;\n"
        "}\n"
    )

    assert _subjects("a.ts", "typescript", source) == [
        (_CONCAT, 5, "out"),
        (_CONCAT, 6, "this.buf"),
        (_PROBE, 7, "big"),
        (_CONCAT, 7, "out"),
    ]


@pytest.mark.parametrize(
    ("path", "language", "source", "expected"),
    [
        pytest.param(
            "a.go",
            "go",
            'package m\nfunc f(rows []string) string {\n\tout := ""\n'
            '\tfor range rows {\n\t\tout += "a"\n\t}\n\treturn out\n}\n',
            [(_CONCAT, 5, "out")],
            id="go",
        ),
        pytest.param(
            "a.rb",
            "ruby",
            "def f(rows)\n  out = ''\n  rows.each do |r|\n    out += 'a'\n    @buf += 'b'\n  end\n  out\nend\n",
            [(_CONCAT, 4, "out"), (_CONCAT, 5, "@buf")],
            id="ruby",
        ),
        pytest.param(
            "a.kt",
            "kotlin",
            'fun f(rows: List<String>): String {\n    var out = ""\n    for (r in rows) {\n'
            '        out += "a"\n    }\n    return out\n}\n',
            [(_CONCAT, 4, "out")],
            id="kotlin",
        ),
        pytest.param(
            "a.scala",
            "scala",
            'object O {\n  def f(rows: List[String]): String = {\n    var out = ""\n'
            '    for (r <- rows) {\n      out += "a"\n    }\n    out\n  }\n}\n',
            [(_CONCAT, 5, "out")],
            id="scala",
        ),
        pytest.param(
            "a.dart",
            "dart",
            "String f(List<String> rows) {\n  var out = '';\n  for (final r in rows) {\n"
            "    out += 'a';\n  }\n  return out;\n}\n",
            [(_CONCAT, 4, "out")],
            id="dart",
        ),
    ],
)
def test_every_dialect_with_the_marker_records_the_string(path, language, source, expected):
    hits = _subjects(path, language, source)
    if not hits:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    assert hits == expected


def test_no_other_kind_of_hit_has_a_subject():
    """A sink, and a call marker that is not the membership one."""
    source = (
        "import requests\n"
        "def f(urls, items):\n"
        "    for u in urls:\n"
        "        requests.get(u)\n"
        "        items.insert(0, u)\n"
    )

    hits = walk_file("a.py", "python", source.encode()).perf_hits

    assert [hit.kind for hit in hits] == ["io_in_loop", "list_insert_zero_in_loop"]
    assert all(hit.subject == "" for hit in hits)
    assert all("subject" not in hit.loop_facts() for hit in hits)


def test_the_name_travels_in_the_finding_s_details():
    source = "def f(rows):\n    out = ''\n    for r in rows:\n        out += 'a'\n    return out\n"

    (hit,) = walk_file("a.py", "python", source.encode()).perf_hits

    assert hit.loop_facts()["subject"] == "out"
    assert hit.loop_facts()["loop_line"] == 3
