"""Staged Extract Method: a brain method split into helpers called in order.

One helper cannot take a CCN 30+ function under the bar, so the plan cuts its
effective body (under a dominant ``try`` / ``with`` wrapper) into stages, each
a helper with CCN <= 10 and NLOC <= 60, and credits the summed drop. The
fixture mirrors ``update_cmd/persistence.py::_persist_full_update_async``: an
``async with`` session inside a ``try``, sections each wrapped in
``try / except: _skip(...); if strict: raise``, a list that sections extend.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import Extraction, analyze_file
from repowise.core.analysis.health.dataflow.stages import (
    STAGE_MAX_CCN,
    STAGE_MAX_NLOC,
    _composes,
    find_stages,
)
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.models import RefactoringContext
from repowise.core.analysis.health.refactoring.recipe import build_recipe
from repowise.core.analysis.health.refactoring.render import list_plan


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _functions(src: str, language: str = "python", ext: str = "py"):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file(f"m.{ext}", language, textwrap.dedent(src).encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return res.functions


def _plans(src: str, language: str = "python", ext: str = "py"):
    fns = _functions(src, language, ext)
    ctx = RefactoringContext(
        file_path=f"m.{ext}",
        language=language,
        nloc=300,
        findings=[_Finding("complex_method", f.name, f.start_line, 2.0) for f in fns],
        function_analyses=fns,
    )
    return {p.target_symbol: p for p in ExtractMethodDetector().detect(ctx)}


def _section(n: int, *, extend: bool = False) -> str:
    """One persist step: a banner, a timed block and the degrade handler."""
    write = (
        f"pages += await step_{n}(session, repo_id, a, b, c, d, e)"
        if extend
        else f"await step_{n}(session, repo_id, a, b, c, d, e)"
    )
    return f"""
            # Persist part {n}
            try:
                with timed(timings, "persist.part_{n}"):
                    if flag_{n}:
                        {write}
                    elif other_{n}:
                        await other_step_{n}(session, repo_id)
            except Exception as exc:
                _skip("part {n}", exc)
                if strict:
                    raise
"""


def _persist(sections: int = 8, *, extend: tuple[int, ...] = (2, 5)) -> str:
    flags = ", ".join(f"flag_{n}, other_{n}" for n in range(sections))
    body = "".join(_section(n, extend=n in extend) for n in range(sections))
    return f"""
async def persist(repo_path, a, b, c, d, e, timings, strict, {flags}):
    from store import other_step_1, step_1

    degraded = []

    def _skip(step, exc):
        degraded.append(f"{{step}}: {{exc}}")

    pages: list[str] = []
    engine = create_engine(repo_path)
    try:
        async with get_session(engine) as session:
            repo_id = await upsert_repository(session, repo_path)
{textwrap.indent(textwrap.dedent(body), " " * 12)}
        return pages, degraded
    finally:
        await engine.dispose()
"""


def test_persistence_shape_yields_a_staged_plan():
    plan = _plans(_persist())["persist"]
    stages = plan.plan["stages"]
    assert len(stages) >= 2
    # The top-level keys are stage 1, so a single-span reader reads step one.
    assert plan.plan["span"] == stages[0]["span"]
    assert plan.plan["call_site"] == stages[0]["call_site"]
    for stage in stages:
        assert stage["ccn"] <= STAGE_MAX_CCN and stage["nloc"] <= STAGE_MAX_NLOC
        assert stage["needs_async"] and stage["new_symbol"]["async"]
        assert stage["new_symbol"]["signature_text"].startswith("async def ")
        assert "await " in stage["call_site"]["new_text"]
    removed = sum(s["ccn"] - 1 for s in stages)
    assert plan.evidence["ccn_removed"] == removed
    residual = plan.plan["orchestrator"]
    assert residual["ccn_after"] == residual["ccn_before"] - removed
    # Credited the summed drop, not one span's share.
    assert plan.impact_delta == pytest.approx(2.0 * removed / residual["ccn_before"], abs=1e-3)
    # Stages carry the raise their sections hold (it propagates unchanged).
    lines = textwrap.dedent(_persist()).splitlines()
    assert any(
        "raise" in lines[ln - 1]
        for s in stages
        for ln in range(s["span"]["start"], s["span"]["end"] + 1)
    )


def test_stage_names_come_from_timed_labels_and_banners():
    stages = _plans(_persist(sections=8, extend=()))["persist"].plan["stages"]
    # A stage covering one section takes its label; one spanning two has none.
    for stage in stages:
        name = stage["suggested_name"]
        assert name is None or name.startswith("_persist_part")


def test_shared_values_travel_on_a_parameter_object():
    plan = _plans(_persist()).get("persist").plan
    obj = plan["parameter_object"]
    assert obj is not None
    names = {f["name"] for f in obj["fields"]}
    assert {"session", "repo_id", "timings", "strict", "_skip", "a"} <= names
    assert obj["declaration_text"].startswith("@dataclass(frozen=True)\nclass _PersistContext:")
    assert obj["construct_text"].startswith("ctx = _PersistContext(")
    first = plan["stages"][0]
    assert first["params"][0] == "ctx"
    assert first["new_symbol"]["params"][0] == {
        "name": "ctx",
        "type": "_PersistContext",
        "mode": "in",
    }
    assert set(first["context_params"]) <= names
    assert any("ctx.session" in n for n in first["new_symbol"]["notes"])


def test_few_shared_values_stay_plain_parameters():
    src = _persist().replace(
        "session, repo_id, a, b, c, d, e)", "session, repo_id)"
    ).replace('_skip("part', 'log("part').replace("if strict:\n", "if flag_0:\n")
    plan = _plans(src)["persist"].plan
    assert plan["stages"]
    assert plan["parameter_object"] is None
    assert all(s["context_params"] == [] for s in plan["stages"])


def test_a_list_extended_in_a_branch_is_passed_in_and_handed_back():
    stages = _plans(_persist())["persist"].plan["stages"]
    carrying = [s for s in stages if "pages" in s["returns"]]
    assert carrying
    for stage in carrying:
        # Written on one path only, so the stage takes the list and returns it.
        modes = {p["name"]: p["mode"] for p in stage["new_symbol"]["params"]}
        assert modes["pages"] == "inout"
        assert stage["call_site"]["new_text"].startswith(
            ", ".join(stage["returns"]) + " = await "
        )


def test_a_return_inside_a_section_never_lands_in_a_stage():
    src = _persist().replace(
        "                if strict:\n                    raise\n",
        "                if strict:\n                    return pages, degraded\n",
        1,
    )
    fn = _functions(src)[0]
    plan = find_stages(fn, get_language_map("python"))
    returns = [
        i + 1
        for i, line in enumerate(textwrap.dedent(src).splitlines())
        if line.strip().startswith("return pages") and i + 1 < fn.end_line - 3
    ]
    assert returns
    for stage in plan.stages:
        assert not any(stage.start_line <= ln <= stage.end_line for ln in returns)


def test_composition_refuses_a_value_no_stage_hands_on():
    src = """
    def run(rows, limit):
        total = 0
        for r in rows:
            if r > limit:
                total += r
        seen = total * 2
        for r in rows:
            if r < seen:
                print(r)
        return seen
    """
    fn = _functions(src)[0]
    first = Extraction(3, 6, ("limit", "rows"), ("total",), 4, 2)
    second = Extraction(7, 10, ("rows", "total"), ("seen",), 4, 2)
    assert _composes(fn, (first, second), ()) == ((), ())
    # The first stage keeps ``total`` to itself: the second would read nothing.
    keeps = Extraction(3, 6, ("limit", "rows"), (), 4, 2)
    assert _composes(fn, (keeps, second), ()) is None
    # The second stage was not given ``total``.
    assert _composes(fn, (first, Extraction(7, 10, ("rows",), ("seen",), 4, 2)), ()) is None
    # Nor hands ``seen`` back to the ``return`` after it.
    assert _composes(fn, (first, Extraction(7, 10, ("rows", "total"), (), 4, 2)), ()) is None


def test_a_sync_host_gets_sync_stages():
    src = _persist().replace("async def persist", "def persist").replace("await ", "")
    src = src.replace("async with", "with")
    stages = _plans(src)["persist"].plan["stages"]
    assert stages
    for stage in stages:
        assert not stage["needs_async"]
        assert stage["new_symbol"]["signature_text"].startswith("def ")
        assert "await" not in stage["call_site"]["new_text"]


def test_a_function_one_helper_can_fix_is_never_staged():
    src = _persist(sections=4, extend=())
    assert _functions(src)[0].ccn <= 2 * STAGE_MAX_CCN
    plan = _plans(src).get("persist")
    assert plan is None or "stages" not in plan.plan


def test_stages_are_detail_only_and_recipe_steps():
    suggestion = _plans(_persist())["persist"]
    plan = suggestion.plan
    listed = list_plan(plan)
    assert not {"stages", "parameter_object", "orchestrator", "call_site"} & listed.keys()
    recipe = build_recipe(
        {
            "id": "x",
            "refactoring_type": "extract_method",
            "file_path": "m.py",
            "target_symbol": "persist",
            "plan": plan,
        }
    )
    steps = recipe["steps"]
    assert steps[0]["action"] == "add_helper"
    extracts = [s for s in steps if s["action"] == "extract"]
    assert len(extracts) == len(plan["stages"])
    assert extracts[0]["span"] == plan["stages"][0]["span"]
    assert extracts[0]["text"].startswith(f"Stage 1 of {len(plan['stages'])}: ")
    assert recipe["summary"].startswith(f"Split `persist` into {len(plan['stages'])} helpers")


def test_python_helper_hands_back_a_tuple():
    from repowise.core.analysis.health.refactoring import render

    shape = render.HelperShape(
        language="python",
        name="_split",
        kind="function",
        is_async=True,
        params=(render.Slot("rows"),),
        returns=(render.Slot("kept", "list[str]"), render.Slot("dropped", "list[str]")),
    )
    texts = render.render(shape)
    assert texts.signature == "async def _split(rows) -> tuple[list[str], list[str]]:"
    assert texts.call == "kept, dropped = await _split(rows)"


def test_typescript_parameter_object():
    from repowise.core.analysis.health.refactoring import render

    fields = (render.Slot("session", "Session"), render.Slot("repoId"))
    text = render.render_context("typescript", "PersistContext", "ctx", fields)
    assert text.declaration == "interface PersistContext {\n  session: Session;\n  repoId: <type>;\n}"
    assert text.construct == "const ctx: PersistContext = { session, repoId };"
    assert render.context_name("typescript", "persistAll") == "PersistAllContext"
    assert render.context_name("python", "persist_all") == "_PersistAllContext"


def test_a_stage_is_told_to_import_what_the_function_imports_locally():
    stages = _plans(_persist())["persist"].plan["stages"]
    told = [
        n
        for s in stages
        for n in s["new_symbol"].get("notes", [])
        if n.startswith("Import ") and "step_1" in n
    ]
    assert told == ["Import other_step_1, step_1 in the helper: the function imports them inside its body."]
