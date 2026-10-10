"""Extract Method models ``await``: an awaiting span becomes an async helper.

A span lifted out of an async function that awaits inside it only works as a
helper declared async whose call site is awaited. The slicer marks such spans
``needs_async`` from the language's ``await_kinds`` during its one metrics
pass; the plan carries the flag, and a span whose enclosing function is not
async (a C++ coroutine) is a judgment call rather than a mechanical step.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import (
    analyze_file,
    find_extractions,
    function_is_async,
)
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.models import (
    RefactoringContext,
    RefactoringSuggestion,
)
from repowise.core.analysis.health.refactoring.preconditions import classify_step


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _functions(language: str, ext: str, src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file(f"m.{ext}", language, textwrap.dedent(src).encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return res.functions


def _flags(language: str, ext: str, src: str) -> dict[tuple[int, int], bool]:
    """``needs_async`` per candidate span of the file's first function."""
    fn = _functions(language, ext, src)[0]
    return {
        (x.start_line, x.end_line): x.needs_async
        for x in find_extractions(fn, get_language_map(language))
    }


def _plans(language: str, ext: str, src: str):
    fns = _functions(language, ext, src)
    ctx = RefactoringContext(
        file_path=f"m.{ext}",
        language=language,
        nloc=100,
        findings=[_Finding("complex_method", f.name, f.start_line, 1.5) for f in fns],
        function_analyses=fns,
    )
    return ExtractMethodDetector().detect(ctx)


# The two halves of one async function: lines 3-10 await, lines 11-18 do not.
_PY = """
async def load(session, rows, limit):
    total = 0
    for r in rows:
        if r.kind == "a":
            total += await session.count(r)
        elif r.kind == "b":
            total -= 1
        else:
            total += 2
    seen = 0
    for r in rows:
        if r.size > limit:
            seen += 1
        elif r.size < 0:
            seen -= 1
        else:
            seen += 2
    return total + seen
"""


def _spans_within(flags: dict[tuple[int, int], bool], lo: int, hi: int) -> set[bool]:
    return {flag for (s, e), flag in flags.items() if lo <= s and e <= hi}


def test_python_awaiting_span_needs_async_and_a_plain_one_does_not():
    flags = _flags("python", "py", _PY)
    assert _spans_within(flags, 3, 10) == {True}
    assert _spans_within(flags, 11, 18) == {False}


@pytest.mark.parametrize(
    "replacement",
    [
        "async with session.begin():\n                total += 1",
        "async for x in session.stream(r):\n                total += x",
    ],
)
def test_python_async_with_and_async_for_need_async(replacement: str):
    src = _PY.replace("total += await session.count(r)", replacement)
    flags = _flags("python", "py", src)
    assert _spans_within(flags, 3, 11) == {True}
    assert _spans_within(flags, 12, 19) == {False}


_TS = """
async function load(session: S, rows: R[], limit: number): Promise<number> {
  let total = 0;
  for (const r of rows) {
    if (r.kind === "a") {
      total += await session.count(r);
    } else {
      total -= 1;
    }
  }
  let seen = 0;
  for (const r of rows) {
    if (r.size > limit) {
      seen += 1;
    } else {
      seen -= 1;
    }
  }
  return total + seen;
}
"""


def test_typescript_await_marks_only_the_awaiting_span():
    flags = _flags("typescript", "ts", _TS)
    assert _spans_within(flags, 3, 10) == {True}
    assert _spans_within(flags, 11, 18) == {False}


def test_typescript_for_await_needs_async():
    src = _TS.replace(
        "  for (const r of rows) {\n    if (r.kind",
        "  for await (const r of session.stream()) {\n    if (r.kind",
    ).replace("total += await session.count(r);", "total += r.n;")
    assert "for await" in src
    flags = _flags("typescript", "ts", src)
    assert _spans_within(flags, 3, 10) == {True}


def test_typescript_await_inside_an_async_arrow_does_not_count():
    src = _TS.replace(
        "total += await session.count(r);",
        "total += 1;\n      const later = async () => { await session.flush(); };",
    )
    assert set(_flags("typescript", "ts", src).values()) == {False}


_RUST = """
async fn load(session: &S, rows: &[R], limit: i64) -> i64 {
    let mut total = 0;
    for r in rows {
        if r.kind == 1 {
            total += session.count(r).await;
        } else {
            total -= 1;
        }
    }
    let mut seen = 0;
    for r in rows {
        if r.size > limit {
            seen += 1;
        } else {
            seen -= 1;
        }
    }
    total + seen
}
"""


def test_rust_dot_await_marks_only_the_awaiting_span():
    flags = _flags("rust", "rs", _RUST)
    assert _spans_within(flags, 3, 10) == {True}
    assert _spans_within(flags, 11, 18) == {False}


# The shape of ``attention.py::_health_items``: an async function whose second
# half aggregates rows read by awaiting ``session.execute`` inside a loop header.
_HEALTH_ITEMS = """
async def _health_items(session, repo_id, scoped, paths):
    rows = (await session.execute(select_rows(scoped))).all()
    total = await session.scalar(count_rows(scoped)) or 0
    if not rows:
        return [], int(total), ""

    lead_biomarker = {}
    history_lead = {}
    for path, biomarker in (
        await session.execute(select_leads(scoped, paths))
    ).all():
        if category(biomarker) == HISTORY:
            history_lead.setdefault(path, biomarker)
        else:
            lead_biomarker.setdefault(path, biomarker)
    for path, biomarker in history_lead.items():
        lead_biomarker.setdefault(path, biomarker)

    items = [
        {
            "id": f"health-{row.file_path}",
            "severity": severity(max(1.0, 10.0 - float(row.impact or 0.0))),
            "subtype": lead_biomarker.get(row.file_path),
            "weight": float(row.impact or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""
"""


def test_health_items_plan_is_an_async_helper_and_stays_mechanical():
    plans = _plans("python", "py", _HEALTH_ITEMS)
    assert plans, "the _health_items shape should still be offered a plan"
    plan = plans[0]
    assert plan.plan["needs_async"] is True
    assert plan.plan["async_host"] is True
    assert "async_helper_unexpressible" not in classify_step(plan).reasons


_CPP = """
task<int> load(S& session, std::vector<R>& rows, int limit) {
    int total = 0;
    for (auto& r : rows) {
        if (r.kind == 1) {
            total += co_await session.count(r);
        } else {
            total -= 1;
        }
    }
    int seen = 0;
    for (auto& r : rows) {
        if (r.size > limit) {
            seen += 1;
        } else {
            seen -= 1;
        }
    }
    co_return total + seen;
}
"""


def test_cpp_co_await_needs_async_in_a_host_that_cannot_say_so():
    fn = _functions("cpp", "cpp", _CPP)[0]
    lmap = get_language_map("cpp")
    flags = {(x.start_line, x.end_line): x.needs_async for x in find_extractions(fn, lmap)}
    assert _spans_within(flags, 3, 10) == {True}
    assert _spans_within(flags, 11, 18) == {False}
    assert function_is_async(fn.fn_node, lmap) is False


@pytest.mark.parametrize(
    ("language", "ext", "src", "expected"),
    [
        ("python", "py", "async def f():\n    pass\n", True),
        ("python", "py", "def f():\n    pass\n", False),
        ("typescript", "ts", "async function f() { return 1; }", True),
        ("typescript", "ts", "function f() { return 1; }", False),
        ("rust", "rs", "async fn f() -> i32 { 1 }", True),
        ("rust", "rs", "fn f() -> i32 { let b = async { 1 }; 1 }", False),
    ],
)
def test_function_is_async_reads_the_declaration_not_the_body(language, ext, src, expected):
    fn = _functions(language, ext, src)[0]
    assert function_is_async(fn.fn_node, get_language_map(language)) is expected


def _step(plan: dict) -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path="m.cpp",
        target_symbol="load",
        line_start=1,
        line_end=12,
        plan=plan,
        evidence={"slice_nloc": 8, "ccn_removed": 2},
        impact_delta=0.5,
        effort_bucket="S",
        blast_radius={"scope": "local"},
        confidence="high",
        source_biomarker="complex_method",
    )


def test_an_awaiting_span_with_no_async_host_is_a_judgment_call():
    base = {"span": {"start": 3, "end": 9}, "params": [], "returns": []}
    blocked = classify_step(_step({**base, "needs_async": True, "async_host": False}))
    assert blocked.classification == "judgment"
    assert blocked.reasons == ("async_helper_unexpressible",)
    for plan in (
        {**base, "needs_async": True, "async_host": True},
        {**base, "needs_async": False},
        base,  # stored before the field existed
    ):
        assert classify_step(_step(plan)).classification == "mechanical"
