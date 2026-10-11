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
