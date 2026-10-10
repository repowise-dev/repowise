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
    assert _composes(fn, (first, second), ()) == (((), ()), frozenset())
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


def _loop_section(n: int) -> str:
    """A section whose loop breaks and continues: both stay in the loop."""
    return f"""
            # Scan part {n}
            for row in rows_{n}:
                if not row:
                    continue
                if row == "stop":
                    break
                seen_{n}.append(row)
"""


def test_break_and_continue_inside_a_stage_are_allowed():
    sections = "".join(_loop_section(n) for n in range(8))
    seen = ", ".join(f"seen_{n}" for n in range(8))
    rows = ", ".join(f"rows_{n}" for n in range(8))
    src = f"""
def scan({rows}, {seen}):
    total = 0
{textwrap.indent(textwrap.dedent(sections), " " * 4)}
    return total
"""
    stages = _plans(src)["scan"].plan["stages"]
    lines = textwrap.dedent(src).splitlines()
    held = [
        lines[ln - 1].strip()
        for s in stages
        for ln in range(s["span"]["start"], s["span"]["end"] + 1)
    ]
    assert "break" in held and "continue" in held


def test_the_receiver_never_rides_the_parameter_object():
    method = textwrap.indent(_persist().replace("async def persist(", "async def persist(self, "), "    ")
    method = method.replace("session, repo_id, a, b", "self.store, session, repo_id, a, b")
    src = "class Store:\n" + method
    plan = _plans(src)["persist"].plan
    obj = plan["parameter_object"]
    assert obj is not None and "self" not in {f["name"] for f in obj["fields"]}
    for stage in plan["stages"]:
        assert "self" not in stage["context_params"]
        assert stage["new_symbol"]["kind"] == "method"
        assert stage["new_symbol"]["signature_text"].startswith("async def ")
        assert "(self, ctx" in stage["new_symbol"]["signature_text"]
        assert stage["call_site"]["new_text"].count("self.") == 1


def test_a_module_global_named_ctx_is_not_shadowed():
    src = _persist().replace("    degraded = []\n", "    degraded = [ctx]\n", 1)
    obj = _plans(src)["persist"].plan["parameter_object"]
    assert obj["var"] == "context"
    assert obj["construct_text"].startswith("context = _PersistContext(")


def test_an_async_stage_hands_back_a_tuple():
    src = _persist(extend=(2, 3)).replace(
        "pages += await step_3(", "more += await step_3("
    ).replace("    pages: list[str] = []\n", "    pages: list[str] = []\n    more: list[str] = []\n")
    src = src.replace("return pages, degraded", "return pages, more, degraded")
    stages = _plans(src)["persist"].plan["stages"]
    pair = [s for s in stages if len(s["returns"]) == 2]
    assert pair
    stage = pair[0]
    assert stage["new_symbol"]["signature_text"].endswith(
        "-> tuple[list[str], list[str]]:"
    )
    assert stage["call_site"]["new_text"].startswith(
        f"{', '.join(stage['returns'])} = await "
    )


def test_a_timer_is_never_split_by_a_stage_edge():
    src = _persist().replace(
        "            # Persist part 4\n",
        '            if timings is not None:\n                timings.start("persist.four")\n'
        "            # Persist part 4\n",
    ).replace(
        '                _skip("part 4", exc)\n                if strict:\n                    raise\n',
        '                _skip("part 4", exc)\n                if strict:\n                    raise\n'
        '            finally:\n                if timings is not None:\n'
        '                    timings.stop("persist.four")\n',
    )
    assert 'timings.stop("persist.four")' in src
    lines = textwrap.dedent(src).splitlines()
    start = next(i for i, ln in enumerate(lines, 1) if 'start("persist.four")' in ln)
    stop = next(i for i, ln in enumerate(lines, 1) if 'stop("persist.four")' in ln)
    for s in _plans(src)["persist"].plan["stages"]:
        inside = [s["span"]["start"] <= ln <= s["span"]["end"] for ln in (start, stop)]
        assert inside[0] == inside[1]


def test_a_typed_host_gets_typed_void_helpers():
    src = _persist().replace("strict, flag_0", "strict: bool, flag_0", 1)
    src = src.replace("other_7):", "other_7) -> tuple:", 1)
    stages = _plans(src)["persist"].plan["stages"]
    void = [s for s in stages if not s["returns"]]
    assert void
    assert all(s["new_symbol"]["signature_text"].endswith(") -> None:") for s in void)
    untyped = _plans(_persist())["persist"].plan["stages"]
    assert not any("-> None" in s["new_symbol"]["signature_text"] for s in untyped)


def test_a_staged_id_names_the_whole_split():
    from repowise.core.analysis.health.refactoring.identity import refactoring_public_id

    plan = _plans(_persist())["persist"]
    single = dict(plan.plan)
    single.pop("stages")
    fewer = dict(plan.plan, stages=plan.plan["stages"][:-1])
    ids = {
        refactoring_public_id(plan),
        refactoring_public_id(type(plan)(**{**plan.__dict__, "plan": single})),
        refactoring_public_id(type(plan)(**{**plan.__dict__, "plan": fewer})),
    }
    assert len(ids) == 3


def test_an_else_arm_opening_on_a_comment_is_in_the_flow_graph():
    # hermes ``run_doctor``: the stage read ``should_fix`` and bumped
    # ``fixed_count`` inside such an arm, and the plan passed neither.
    src = """
    def f(p, fix):
        n = 0
        if p:
            g()
        else:
            # fall back
            if fix:
                n += 1
        return n
    """
    fn = _functions(src)[0]
    lines = {st.start_line for b in fn.cfg.blocks for st in b.statements}
    assert {8, 9} <= lines
    reads = {u.name for b in fn.def_use.blocks.values() for u in b.uses}
    assert {"fix", "n"} <= reads


def test_an_input_bound_on_one_path_only_is_never_passed():
    # ours ``tool_symbol.py::get_symbol``: ``members`` was bound only under
    # ``if outline is not None:`` and the stage took it, raising on the other path.
    sections = "".join(_section(n) for n in range(8))
    src = f"""
async def persist(repo_path, a, b, c, d, e, timings, strict, outline, {", ".join(f"flag_{n}, other_{n}" for n in range(8))}):
    def _skip(step, exc):
        pass

    session = open_session(repo_path)
    repo_id = 1
    if outline is not None:
        members = outline.members
{textwrap.indent(textwrap.dedent(sections), " " * 4)}
    if outline is not None:
        report(members)
"""
    src = src.replace("await step_3(session, repo_id, a, b, c, d, e)", "await step_3(session, repo_id, members if outline is not None else None)")
    fn = _functions(src)[0]
    plan = find_stages(fn, get_language_map("python"))
    assert plan.stages
    assert all("members" not in x.params for x in plan.stages)


_TS_GUARD = """
async function run(opts, a, b, c, d, e) {
  const token = opts.token ?? a.token;
  if (!token) {
    throw new Error("token missing");
  }
  // part one
  try {
    if (a) { await one(token, a); } else if (b) { await two(token, b); }
  } catch (err) {
    if (c) { log(err); }
  }
  // part two
  try {
    if (d) { await three(token, d); } else if (e) { await four(token, e); }
  } catch (err) {
    if (c) { log(err); }
  }
  // part three
  for (const item of a.items) {
    if (item.ok) { await five(token, item); } else if (item.skip) { continue; }
  }
  // part four
  try {
    if (a && b) { await six(token); } else if (d || e) { await seven(token); }
  } catch (err) {
    if (c) { log(err); }
  }
  return send(token);
}
"""


def test_a_narrowing_guard_stays_in_the_function():
    fn = _functions(_TS_GUARD, "typescript", "ts")[0]
    plan = find_stages(fn, get_language_map("typescript"), max_returns=1)
    assert plan.stages
    guard = next(i for i, ln in enumerate(textwrap.dedent(_TS_GUARD).splitlines(), 1) if "if (!token)" in ln)
    assert all(not x.start_line <= guard <= x.end_line for x in plan.stages)


def test_a_loop_variable_a_stage_declares_is_never_handed_back():
    # openclaw ``template.js``: ``entry = helper(entries, entry, labelMap)``
    # for a ``for (const entry of ...)`` binder that ends with its loop.
    src = """
    function render(entries, extra) {
      const byId = new Map();
      for (const entry of entries) {
        if (entry.a && entry.id) {
          byId.set(entry.id, entry);
        } else if (entry.b || extra) {
          skip(entry);
        } else if (entry.c && entry.d) {
          keep(entry);
        }
      }
      const labels = new Map();
      for (const entry of entries) {
        if (entry.type === "label" && entry.target && entry.label) {
          labels.set(entry.target, entry.label);
        } else if (entry.type === "a") {
          labels.set(entry.id, "a");
        } else if (entry.type === "b") {
          labels.set(entry.id, "b");
        } else if (entry.type === "c") {
          labels.set(entry.id, "c");
        } else if (entry.type === "d") {
          labels.set(entry.id, "d");
        }
      }
      function tree() {
        for (const entry of entries) { if (entry.id && labels.has(entry.id)) { draw(entry); } }
      }
      return tree();
    }
    """
    fn = _functions(src, "javascript", "js")[0]
    plan = find_stages(fn, get_language_map("javascript"), max_returns=1)
    assert len(plan.stages) >= 2
    for x in plan.stages:
        assert "entry" not in x.returns and "entry" not in x.params


def test_typescript_texts_hand_back_and_type_what_they_can():
    from repowise.core.analysis.health.refactoring import render

    assert render.definite_type("typescript", "Outcome | undefined") == "Outcome"
    assert render.definite_type("typescript", "undefined") == "undefined"
    assert render.definite_type("python", "int | None") == "int | None"
    shape = render.HelperShape(
        language="typescript",
        name="settle",
        kind="function",
        is_async=True,
        params=(render.Slot("reply"), render.Slot("limit", "number")),
        returns=(render.Slot("outcome", "Outcome"),),
    )
    texts = render.render(shape)
    assert texts.returns == "return outcome;"
    assert texts.notes == ("Write the types of reply: the code declares none for them.",)
    py = render.render(render.HelperShape("python", "_f", "function", False, returns=(render.Slot("a"), render.Slot("b"))))
    assert py.returns == "return a, b"


def test_every_output_stage_says_how_it_hands_back():
    for stage in _plans(_persist())["persist"].plan["stages"]:
        text = stage["new_symbol"]["return_text"]
        assert text == (f"return {', '.join(stage['returns'])}" if stage["returns"] else None)


def test_an_import_only_a_stage_reads_leaves_the_function():
    stages = _plans(_persist())["persist"].plan["stages"]
    notes = [n for s in stages for n in s["new_symbol"].get("notes", []) if n.startswith("Remove ")]
    assert notes and all("step_1" in n or "other_step_1" in n for n in notes)
