"""Extract Method spans carry text anchors: the exact first and last lines, grown
until each is unique (in the file when it can be, else in the function), so an
agent finds a stage by its text after earlier stages have moved its lines. A
staged split no text anchors falls back to the best single span; a function
with neither is refused.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.agent_prompts import render_plan
from repowise.core.analysis.health.dataflow import Extraction, analyze_file
from repowise.core.analysis.health.fix_first.build import build_fix_first
from repowise.core.analysis.health.refactoring import extract_method
from repowise.core.analysis.health.refactoring.anchors import (
    anchor_plan,
    file_source,
    function_scope,
    occurrences,
    span_anchors,
)
from repowise.core.analysis.health.refactoring.extract_method import (
    REFUSED_DETAIL,
    ExtractMethodDetector,
    _serve_anchored,
)
from repowise.core.analysis.health.refactoring.models import RefactoringContext
from repowise.core.analysis.health.refactoring.recipe import (
    ELSEWHERE_NOTE,
    LINE_RULE,
    RENAME_NOTE,
    SCRIPT_RULE,
    build_recipe,
)
from repowise.core.analysis.health.refactoring.render import list_plan
from repowise.core.analysis.health.refactoring_summary import summarize_plans
from tests.unit.health.test_extract_method_stages import _persist, _plans

_ROOT = Path(__file__).resolve().parents[3]


def _fn(src: bytes, name: str = "f", language: str = "python", ext: str = "py"):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file(f"m.{ext}", language, src, flagged_only=False)
    return next(f for f in res.functions if f.name == name)


def _scope(src: bytes, name: str = "f", language: str = "python", ext: str = "py"):
    fn_node = _fn(src, name, language, ext).fn_node
    return function_scope(fn_node, file_source(fn_node))


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

_TWIN = b"""\
def f(rows):
    for r in rows:
        print(r)
    x = 1
    for r in rows:
        print(r)
"""


def test_a_repeated_first_line_grows_until_unique():
    # Lines 5-7: "for r in rows:" and "total += r" both occur twice.
    assert span_anchors(_scope(_REPEATS), 5, 7) == {
        "first": "    for r in rows:\n        total += r\n        total -= 1",
        "last": "        total -= 1",
        "unique_in": "file",
    }


def test_a_span_whose_ends_repeat_in_full_is_unanchored():
    assert span_anchors(_scope(_TWIN), 5, 6) is None


def test_crlf_anchors_match_the_bytes_on_disk():
    raw = _REPEATS.replace(b"\n", b"\r\n")
    anchors = span_anchors(_scope(raw), 2, 4)
    # Lines 3-4 repeat as lines 5-6, so the last anchor reaches back to line 2.
    assert anchors["first"] == "    total = 0"
    assert anchors["last"] == "    total = 0\r\n    for r in rows:\r\n        total += r"
    for text in (anchors["first"], anchors["last"]):
        assert raw.count(text.encode()) == 1
    # A string edit from the first anchor through the last replaces exactly the span.
    start = raw.index(anchors["first"].encode())
    end = raw.index(anchors["last"].encode()) + len(anchors["last"].encode())
    edited = raw[:start] + b"    total = _sum(rows)" + raw[end:]
    assert edited.split(b"\r\n") == [
        b"def f(rows):",
        b"    total = _sum(rows)",
        b"    for r in rows:",
        b"        total += r",
        b"        total -= 1",
        b"    return total",
        b"",
    ]


def test_tabs_are_kept_as_written():
    src = b"def f(rows):\n\ttotal = 0\n\tfor r in rows:\n\t\ttotal += r\n\treturn total\n"
    assert span_anchors(_scope(src), 2, 4) == {
        "first": "\ttotal = 0",
        "last": "\t\ttotal += r",
        "unique_in": "file",
    }


def test_a_span_at_the_function_start_and_end():
    # No trailing newline: the span's last line is the file's last.
    scope = _scope(b"def f(a):\n    x = a + 1\n    y = x * 2\n    return y")
    assert (scope.start, scope.end) == (1, 4)
    assert span_anchors(scope, 2, 4) == {
        "first": "    x = a + 1",
        "last": "    return y",
        "unique_in": "file",
    }
    # A span outside the function has nothing to anchor.
    assert span_anchors(scope, 4, 5) is None


def test_a_one_line_span_and_overlapping_anchors():
    assert span_anchors(_scope(_REPEATS), 7, 7) == {
        "first": "        total -= 1",
        "last": "        total -= 1",
        "unique_in": "file",
    }
    # "y = 2" repeats, so the first anchor grows over the line the last one is.
    src = b"def f(a):\n    x = 1\n    y = 2\n    z = 3\n    x = 1\n    y = 2\n"
    anchors = span_anchors(_scope(src), 3, 4)
    assert anchors["first"] == "    y = 2\n    z = 3"
    assert anchors["last"] == "    z = 3"


def test_blank_lines_above_the_first_token_keep_line_numbers():
    src = b"\n\n# lead\ndef f(a):\n    x = a + 1\n    return x\n"
    scope = _scope(src)
    assert scope.start == 4
    assert span_anchors(scope, 5, 6)["first"] == "    x = a + 1"


def test_a_twin_elsewhere_in_the_file_grows_the_anchor_or_says_so():
    src = textwrap.dedent(
        """\
        def g(rows):
            for r in rows:
                print(r)

        def f(rows):
            for r in rows:
                print(r)
            return rows
        """
    ).encode()
    scope = _scope(src)
    # Lines 6-7 are g's body too; line 8 makes the first anchor file-unique.
    anchors = span_anchors(scope, 6, 8)
    assert anchors["first"] == "    for r in rows:\n        print(r)\n    return rows"
    assert anchors["unique_in"] == "file"
    # Lines 6-7 alone repeat g's whole body: unique only inside f, and served so.
    assert span_anchors(scope, 6, 7) == {
        "first": "    for r in rows:",
        "last": "        print(r)",
        "unique_in": "function",
    }


def test_typescript_anchors_follow_its_lines():
    src = textwrap.dedent(
        """\
        export function f(rows: number[]): number {
          let total = 0;
          for (const r of rows) {
            total += r;
          }
          return total;
        }
        """
    ).encode()
    scope = _scope(src, "f", "typescript", "ts")
    assert span_anchors(scope, 3, 5) == {
        "first": "  for (const r of rows) {",
        "last": "  }",
        "unique_in": "file",
    }


def test_anchor_plan_is_all_or_nothing():
    scope = _scope(_REPEATS)
    ok = {"span": {"start": 2, "end": 4}, "stages": [{"span": {"start": 2, "end": 4}}]}
    assert anchor_plan(ok, scope)
    assert ok["anchors"] == ok["stages"][0]["anchors"]
    mixed = {"span": {"start": 2, "end": 4}, "stages": [{"span": {"start": 2, "end": 99}}]}
    assert not anchor_plan(mixed, scope)
    assert "anchors" not in mixed and "anchors" not in mixed["stages"][0]


def test_every_stage_of_a_staged_plan_is_anchored_once_in_the_function():
    src = textwrap.dedent(_persist())
    plan = _plans(_persist())["persist"].plan
    rows = src.split("\n")
    grown = 0
    for stage in plan["stages"]:
        anchors, span = stage["anchors"], stage["span"]
        for text in (anchors["first"], anchors["last"]):
            assert occurrences(src, text) == 1
        assert rows[span["start"] - 1] == anchors["first"].split("\n")[0]
        assert rows[span["end"] - 1] == anchors["last"].split("\n")[-1]
        grown += "\n" in anchors["last"]
    # Every section ends on "if strict: raise", so a last line alone repeats.
    assert grown
    assert plan["anchors"] == plan["stages"][0]["anchors"]


def _detail(plan: dict) -> dict:
    return {"id": "x", "refactoring_type": "extract_method", "file_path": "m.py",
            "target_symbol": "persist", "plan": plan}


def test_anchors_are_detail_only_and_reach_the_recipe():
    plan = _plans(_persist())["persist"].plan
    assert "anchors" not in list_plan(plan)
    recipe = build_recipe(_detail(plan))
    extracts = [s for s in recipe["steps"] if s["action"] == "extract"]
    assert [s["anchors"] for s in extracts] == [s["anchors"] for s in plan["stages"]]
    assert "starts and ends with the lines shown" in extracts[0]["text"]
    assert SCRIPT_RULE in recipe["does_not"] and LINE_RULE in recipe["does_not"]


def _stage(first: str, last: str, unique_in: str = "file") -> dict:
    return {"span": {"start": 3, "end": 9}, "params": [], "returns": [],
            "anchors": {"first": first, "last": last, "unique_in": unique_in}}


def test_the_prompt_fences_an_anchor_past_its_backticks_and_flags_a_twin():
    text = render_plan(_detail(_stage('    s = """```x```"""', "    return s", "function")))
    assert '````\n    s = """```x```"""\n````' in text
    assert "```\n    return s\n```" in text
    assert ELSEWHERE_NOTE in text
    assert ELSEWHERE_NOTE not in render_plan(_detail(_stage("    a = 1", "    b = 2")))


def test_the_apply_rules_read_the_same_in_every_renderer():
    def sentence(rule: dict) -> str:
        reason = rule["reason"]
        return f"Do not {rule['constraint']}. {reason[0].upper()}{reason[1:]}."

    text = render_plan(_detail(_stage("    a = 1", "    b = 2"))).replace("**", "")
    ui = _ROOT / "packages/ui/src/health/ai-prompts"
    steps_ts = (ui / "refactoring-plan-steps.ts").read_text(encoding="utf-8")
    prompts_ts = (ui / "refactoring-prompts.ts").read_text(encoding="utf-8")
    for wording in (sentence(SCRIPT_RULE), sentence(LINE_RULE)):
        assert wording in text
        assert f'"{wording}"' in steps_ts
    assert f'"{ELSEWHERE_NOTE}"' in steps_ts
    assert f'"{RENAME_NOTE}"' in steps_ts
    # The opportunity fallback reads the same constants.
    assert "SCRIPT_RULE_TEXT," in prompts_ts and "LINE_RULE_TEXT," in prompts_ts


def test_an_unanchorable_span_refuses_the_plan_with_a_reason():
    scope = _scope(_TWIN)
    finding = SimpleNamespace(details={})
    out: list = []
    _serve_anchored(out, SimpleNamespace(plan={"span": {"start": 5, "end": 6}}), [finding], scope)
    assert out == [] and finding.details == {REFUSED_DETAIL: "span_unanchored"}
    served = SimpleNamespace(plan={"span": {"start": 2, "end": 4}})
    _serve_anchored(out, served, [finding], scope)
    assert out == [served]
    assert served.plan["anchors"]["first"] == "    for r in rows:\n        print(r)\n    x = 1"


def _persist_ctx() -> RefactoringContext:
    src = textwrap.dedent(_persist()).encode()
    fns = analyze_file("m.py", "python", src, flagged_only=False).functions
    if not fns:
        pytest.skip("tree-sitter language pack missing for python")
    findings = [
        SimpleNamespace(biomarker_type="complex_method", function_name=f.name,
                        line_start=f.start_line, health_impact=2.0, details={})
        for f in fns
    ]
    return RefactoringContext(
        file_path="m.py", language="python", nloc=300, findings=findings, function_analyses=fns
    )


def _unanchor(monkeypatch, *, single: bool) -> None:
    """Make every staged plan (and with *single*, every plan) unanchorable."""
    real = extract_method.anchor_plan

    def fake(plan, scope):
        if plan.get("stages") or single:
            return False
        return real(plan, scope)

    monkeypatch.setattr(extract_method, "anchor_plan", fake)


def _persist_plans(ctx: RefactoringContext) -> list:
    return [p for p in ExtractMethodDetector().detect(ctx) if p.target_symbol == "persist"]


def test_a_staged_split_no_text_anchors_falls_back_to_one_span(monkeypatch):
    ctx = _persist_ctx()
    (staged,) = _persist_plans(ctx)
    first = staged.plan["stages"][0]["span"]
    # The fixture's function has no single span of its own: give it stage 1's.
    single = Extraction(first["start"], first["end"], (), (), slice_nloc=10, ccn_removed=2)
    monkeypatch.setattr(extract_method, "_choose", lambda *_: single)
    _unanchor(monkeypatch, single=False)
    (plan,) = _persist_plans(ctx)
    assert "stages" not in plan.plan and plan.plan["anchors"]
    assert all(REFUSED_DETAIL not in f.details for f in ctx.findings)


def test_a_function_neither_plan_anchors_is_refused(monkeypatch):
    _unanchor(monkeypatch, single=True)
    ctx = _persist_ctx()
    assert _persist_plans(ctx) == []
    refused = {f.function_name: f.details.get(REFUSED_DETAIL) for f in ctx.findings}
    assert refused["persist"] == "span_unanchored"


def _fix_first_plan_leads(ctx: RefactoringContext, plans: list) -> int:
    """How many Fix first items lead with a stored Extract Method plan's step."""
    metrics = [{"file_path": "m.py", "score": 3.0, "nloc": 300, "is_test": False,
                "commit_count_90d": 5, "dependents": 2, "analyzed_commit": "abc",
                "updated_at": datetime(2026, 10, 1)}]
    findings = [
        {"file_path": "m.py", "biomarker_type": f.biomarker_type, "severity": "high",
         "function_name": f.function_name, "line_start": f.line_start, "health_impact": 2.0,
         "public_id": f"finding_{i}", "dimension": "defect",
         "details": {"ccn": 40, "nloc": 120, "max_nesting": 4,
                     "deepest_block": {"start": 20, "end": 30}, **f.details}}
        for i, f in enumerate(ctx.findings)
        if f.function_name == "persist"
    ]
    rows = [{"public_id": f"refac_{i}", "refactoring_type": p.refactoring_type,
             "file_path": "m.py", "target_symbol": p.target_symbol, "plan": p.plan,
             "evidence": p.evidence} for i, p in enumerate(plans)]
    queue = build_fix_first(metrics=metrics, findings=findings, plans=rows, limit=None)
    # With or without a plan the function is one item; only its first step differs.
    assert len(queue.items) == 1
    return sum(
        bool(item.action.steps) and item.action.steps[0].text.startswith(("Extract", "Stage"))
        for item in queue.items
    )


@pytest.mark.parametrize("anchored", [True, False])
def test_fix_first_and_refactoring_totals_count_a_refusal_alike(monkeypatch, anchored):
    if not anchored:
        _unanchor(monkeypatch, single=True)
    ctx = _persist_ctx()
    plans = _persist_plans(ctx)
    total = summarize_plans(plans)["total"]
    assert total == (1 if anchored else 0)
    assert _fix_first_plan_leads(ctx, plans) == total


def _opportunity(kind: str, plan: dict | None) -> dict:
    import json

    fixture = json.loads(
        (_ROOT / "tests/fixtures/agent_prompts/refactoring.json").read_text(encoding="utf-8")
    )
    detail = dict(fixture["opportunities"][0]["detail"])
    step = {**detail["steps"][0], "refactoring_type": kind, "plan_id": "p1"}
    detail["steps"] = [step]
    detail["plans"] = [{**_detail(plan or {}), "id": "p1", "refactoring_type": kind}]
    return detail


@pytest.mark.parametrize(
    ("kind", "plan", "rules"),
    [
        ("move_method", {"method": "m", "from_class": "A", "to_class": "B"}, ()),
        ("extract_method", {"span": {"start": 3, "end": 9}}, (SCRIPT_RULE,)),
        ("extract_method", _stage("    a = 1", "    b = 2"), (SCRIPT_RULE, LINE_RULE)),
    ],
)
def test_an_opportunity_states_only_the_rules_its_steps_need(kind, plan, rules):
    from repowise.core.agent_prompts import render_opportunity

    text = render_opportunity(_opportunity(kind, plan)).replace("**", "")
    for rule in (SCRIPT_RULE, LINE_RULE):
        assert (f"Do not {rule['constraint']}." in text) == (rule in rules)
