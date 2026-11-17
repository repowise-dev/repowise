"""TypeScript plan types after a guard: ``if (!x) return;`` narrows ``x`` for the
lifted lines, so the helper parameter takes the narrowed type, or a note when
the code does not write it (openclaw ``pdf-tool.model-config.ts``: a typed
parameter whose narrowing came from a guard did not compile)."""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.dataflow import Extraction, analyze_file
from repowise.core.analysis.health.dataflow.dialects.ts_js import union_arms
from repowise.core.analysis.health.refactoring import render
from repowise.core.analysis.health.refactoring.extract_method import _narrow
from repowise.core.analysis.health.refactoring.narrowing import narrowed

_SRC = """
function run(reply?: Reply, opts: Opts | null, kind: unknown, n: number, late?: string) {
  if (!reply) {
    return;
  }
  if (opts === null) throw new Error("no opts");
  if (typeof kind !== "string") return;
  const model = pick(n);
  if (!model || n < 0) return;
  if (!late) return;
  late = other(n);
  send(reply, opts, kind, model, late);
  log(reply);
}
"""


def _analysis():
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_SRC)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")
    lines = src.splitlines()
    span = next(i for i, ln in enumerate(lines, 1) if ln.strip().startswith("send("))
    return res.functions[0], span, lines


def test_a_guard_narrows_the_declared_type_or_says_the_type_is_narrower():
    fn, span, lines = _analysis()
    params = ("reply", "opts", "kind", "model", "late")
    types = {"reply": "Reply | undefined", "opts": "Opts | null", "kind": "unknown", "late": "string | undefined"}
    notes = _narrow(fn, Extraction(span, span + 1, params, (), 0, 0), "typescript", types)
    assert types["reply"] == "Reply" and types["opts"] == "Opts"
    # ``typeof`` narrows to a type the code does not write; ``model`` has none.
    guard = next(i for i, ln in enumerate(lines, 1) if "typeof kind" in ln)
    assert "kind" not in types and notes["kind"] == guard
    assert notes["model"] == next(i for i, ln in enumerate(lines, 1) if "!model" in ln)
    # ``late`` is written after its guard: the declared type stands.
    assert types["late"] == "string | undefined" and "late" not in notes


def test_javascript_and_python_spans_are_left_alone():
    fn, span, _ = _analysis()
    types = {"reply": "Reply | undefined"}
    assert _narrow(fn, Extraction(span, span, ("reply",), (), 0, 0), "javascript", types) == {}
    assert types == {"reply": "Reply | undefined"}


def test_union_arms_split_outside_brackets():
    assert union_arms("Map<string, A | B> | undefined") == ["Map<string, A | B>", "undefined"]
    assert union_arms("(a: A | B) => void") == ["(a: A | B) => void"]
    assert narrowed("A | null | undefined", frozenset({"undefined", "null", "false"})) == "A"
    assert narrowed("A", frozenset({"undefined"})) == "A"
    assert narrowed("undefined", frozenset({"undefined"})) is None


def test_a_narrowed_parameter_without_a_type_gets_its_own_note():
    texts = render.render(
        render.HelperShape(
            language="typescript",
            name="send",
            kind="function",
            is_async=False,
            params=(render.Slot("reply"), render.Slot("model", narrowed_at=7)),
        )
    )
    assert texts.notes == (
        "Write the types of reply: the code declares none for them.",
        "Type model as the guard on line 7 narrows it: a wider type undoes that narrowing.",
    )


def test_a_name_tested_twice_in_one_guard_loses_both_arms():
    from repowise.core.analysis.health.refactoring.narrowing import _merged

    assert _merged({"a": frozenset({"null"})}, {"a": frozenset({"undefined"})}) == {
        "a": frozenset({"null", "undefined"})
    }
    assert _merged({"a": frozenset({"null"})}, {"a": None, "b": None}) == {"a": None, "b": None}



_LOOP_SRC = """
function collect(calls: Call[], seen: Set<string>, name: string) {
  for (const tc of calls) {
    if (tc.status !== "done" || !tc.result) continue;
    if (!seen.has(name)) continue;
    const result = tc.result;
    seen.add(result.id);
    seen.add(tc.name);
  }
}
"""


def test_a_guard_on_a_property_is_named_for_the_object_holding_it():
    # ``chat/source-citations.tsx``: ``!tc.result`` narrows the path, not ``tc``,
    # so a helper typed with any type a caller's ``tc`` has stops compiling.
    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.refactoring.extract_method import _def_lines
    from repowise.core.analysis.health.refactoring.narrowing import property_narrowings
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_LOOP_SRC)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")
    fn = res.functions[0]
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines, 1) if "const result" in ln)
    guard = next(i for i, ln in enumerate(lines, 1) if "!tc.result" in ln)
    found = property_narrowings(
        fn.fn_node, get_language_map("typescript"), start, start + 2, {"seen", "tc"}, _def_lines(fn)
    )
    # ``seen.has`` is a callee, not a property the guard tests.
    assert found == {"tc": guard}


_PLANNED_SRC = """
function scan(calls: Call[], out: string[], strict: boolean) {
  for (const tc of calls) {
    if (!GUARD) continue;
    const result = tc.result;
    if (result.a) {
      out.push(result.a);
    } else if (result.b) {
      out.push(result.b);
    }
    if (result.c && strict) {
      out.push(result.c);
    }
    out.push(String(out.length));
    out.push(String(out.length + 1));
  }
  if (strict) {
    out.push("strict");
  }
  return out;
}
"""


@pytest.mark.parametrize(("guard", "planned"), [("tc.result", False), ("strict", True)])
def test_a_span_reading_a_property_a_guard_narrowed_is_refused(guard, planned):
    from dataclasses import dataclass

    from repowise.core.analysis.health.refactoring.extract_method import (
        NARROWING_LOST,
        STAGE_REFUSED_DETAIL,
        ExtractMethodDetector,
    )
    from repowise.core.analysis.health.refactoring.models import RefactoringContext
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_PLANNED_SRC).replace("GUARD", guard)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")

    @dataclass
    class _Finding:
        biomarker_type: str
        function_name: str
        line_start: int
        health_impact: float
        details: dict

    finding = _Finding("complex_method", "scan", res.functions[0].start_line, 2.0, {})
    ctx = RefactoringContext(
        file_path="m.ts", language="typescript", nloc=40, findings=[finding],
        function_analyses=res.functions,
    )
    plans = ExtractMethodDetector().detect(ctx)
    assert any("tc" in p.plan["params"] for p in plans) is planned
    assert (NARROWING_LOST in finding.details.get(STAGE_REFUSED_DETAIL, ())) is not planned


_GUARDED_SRC = """
function f(calls: Call[], xs: number[], seen: Set<string>) {
  for (let tc of calls) {
    GUARD
    REBIND
    seen.add(BODY);
  }
}
"""


@pytest.mark.parametrize(
    ("guard", "body", "rebind", "refused"),
    [
        ("if (!tc.result) continue;", "tc.result.id", "", True),
        ("if (tc.result == null) continue;", "tc?.result.id", "", True),
        # The span never reads the narrowed path: nothing to lose.
        ("if (!tc.result) continue;", "tc.name", "", False),
        # A literal comparison may discriminate the whole union.
        ("if (tc.kind !== 'done') continue;", "tc.name", "", True),
        # An ordering test narrows nothing.
        ("if (xs.length > 0) continue;", "String(xs[0])", "", False),
        # A rebinding after the guard ends its narrowing.
        ("if (!tc.result) continue;", "tc.result.id", "tc = calls[0];", False),
    ],
)
def test_which_property_tests_narrow_a_span_input(guard, body, rebind, refused):
    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.refactoring.extract_method import _def_lines
    from repowise.core.analysis.health.refactoring.narrowing import property_narrowings
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_GUARDED_SRC).replace("GUARD", guard).replace("BODY", body)
    src = src.replace("REBIND", rebind)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")
    fn = res.functions[0]
    line = next(i for i, ln in enumerate(src.splitlines(), 1) if "seen.add" in ln)
    found = property_narrowings(
        fn.fn_node, get_language_map("typescript"), line, line, {"tc", "xs", "seen"}, _def_lines(fn)
    )
    assert bool(found) is refused


def _ts_function(src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(src)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")
    return res.functions[0], src.splitlines()


def test_an_if_or_assert_the_span_runs_under_narrows_a_plain_name():
    # zulip ``composebox_typeahead.ts``: ``if (sub !== undefined) { ... }`` around
    # the span; ``activity_ui.ts``: ``assert(user_cursor !== undefined)`` above it.
    fn, lines = _ts_function("""
    function f(sub: Sub | undefined, n: number, out: string[]) {
      assert(out !== undefined);
      if (sub !== undefined && n > 0) {
        out.push(sub.name);
      }
    }
    """)
    line = next(i for i, ln in enumerate(lines, 1) if "out.push" in ln)
    types = {"sub": "Sub | undefined", "out": "string[]", "n": "number"}
    notes = _narrow(fn, Extraction(line, line, ("sub", "n", "out"), (), 0, 0), "typescript", types)
    holder = next(i for i, ln in enumerate(lines, 1) if "sub !== undefined" in ln)
    asserted = next(i for i, ln in enumerate(lines, 1) if "assert(" in ln)
    # An ordering test (``n > 0``) narrows nothing.
    assert notes == {"sub": holder, "out": asserted} and types == {"n": "number"}


@pytest.mark.parametrize(
    ("src", "reason"),
    [
        # A module-level name the host asserts on: no parameter carries it.
        ("""
        function f(out: string[]) {
          assert(cursor !== undefined);
          for (const x of out) {
            if (x) {
              cursor.next();
            } else if (x === "a") {
              cursor.prev();
            }
            cursor.mark(x);
          }
        }
        """, "narrowing_lost"),
        # ``let line: T;`` outside, assigned inside: the helper's write names nothing.
        ("""
        function f(out: string[], k: string) {
          let line: string;
          for (const x of out) {
            if (x === k) {
              line = x;
              out.push(line);
            } else if (x) {
              line = k;
              out.push(line);
            }
          }
        }
        """, "outer_declaration"),
    ],
)
def test_a_span_whose_helper_cannot_be_written_is_refused(src, reason):
    from repowise.core.analysis.health.refactoring.extract_method import _writable_spans

    fn, lines = _ts_function(src)
    start = next(i for i, ln in enumerate(lines, 1) if "for (const x" in ln)
    end = next(i for i in range(len(lines), 0, -1) if lines[i - 1].strip() == "}") - 1
    refused: set[str] = set()
    span = Extraction(start, end, ("out",) if reason == "narrowing_lost" else ("k", "out"), (), 0, 0)
    assert _writable_spans(fn, [span], "typescript", refused) == []
    assert refused == {reason}


def test_a_span_holding_an_assert_the_code_after_it_needs_is_refused():
    # zulip ``copy_messages.ts``: the span opened with ``assert(message !== undefined)``
    # and the loop read ``message.sender_full_name`` after it.
    from repowise.core.analysis.health.refactoring.extract_method import _writable_spans

    fn, lines = _ts_function("""
    function f(rows: Row[], out: string[]) {
      for (const row of rows) {
        const message = find(row);
        assert(message !== undefined);
        if (message.a) {
          out.push(message.a);
        }
        out.push(message.name);
      }
    }
    """)
    start = next(i for i, ln in enumerate(lines, 1) if "assert(" in ln)
    span = Extraction(start, start + 3, ("message", "out"), (), 0, 0)
    refused: set[str] = set()
    assert _writable_spans(fn, [span], "typescript", refused) == []
    assert refused == {"narrowing_lost"}


def test_an_output_declared_before_the_span_is_declared_again_in_the_helper():
    # ``blast-radius-header.tsx``: ``let gapClause: string;`` above, assigned in the span.
    from repowise.core.analysis.health.refactoring.extract_method import _undeclared_outputs_note

    fn, lines = _ts_function("""
    function f(n: number): string {
      let clause: string;
      if (n > 1) {
        clause = "many";
      } else {
        clause = "one";
      }
      return clause;
    }
    """)
    start = next(i for i, ln in enumerate(lines, 1) if "if (n > 1)" in ln)
    span = Extraction(start, start + 4, ("n",), ("clause",), 0, 0)
    assert _undeclared_outputs_note(fn, span) == [
        "Declare clause at the top of the helper, as the function does: the lines assign it "
        "without declaring it."
    ]
