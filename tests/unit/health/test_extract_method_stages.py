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
from dataclasses import dataclass, replace

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import Extraction, analyze_file
from repowise.core.analysis.health.dataflow.slice import _unwrap_container
from repowise.core.analysis.health.dataflow.stages import (
    STAGE_MAX_CCN,
    STAGE_MAX_NLOC,
    _Blocks,
    _composes,
    _Cutter,
    _descent,
    _FunctionFacts,
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
    assert plan.evidence["recovered_share"] == round(removed / residual["ccn_before"], 3)
    assert plan.impact_delta > 0
    # Stages carry the raise their sections hold (it propagates unchanged).
    lines = textwrap.dedent(_persist()).splitlines()
    assert any(
        "raise" in lines[ln - 1]
        for s in stages
        for ln in range(s["span"]["start"], s["span"]["end"] + 1)
    )


def test_stage_names_are_numbered_placeholders():
    plan = _plans(_persist(sections=8, extend=()))["persist"].plan
    names = [stage["suggested_name"] for stage in plan["stages"]]
    # Labels and banners name nothing: the agent applying the plan names each.
    assert names == ["_extracted_helper"] + [f"_extracted_helper_{n}" for n in range(2, len(names) + 1)]
    for stage in plan["stages"]:
        name = stage["suggested_name"]
        assert stage["new_symbol"]["signature_text"].startswith(f"async def {name}(")
        assert f"await {name}(" in stage["call_site"]["new_text"]


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

    # The author's declared type is kept; the note says the narrower one is safe.
    widened = render.render(
        render.HelperShape(
            language="typescript",
            name="settle",
            kind="function",
            is_async=False,
            returns=(render.Slot("outcome", "Outcome | undefined"),),
        )
    )
    assert widened.signature.endswith(": Outcome | undefined {")
    assert widened.notes == (
        "outcome is declared `Outcome | undefined`, but every path through these lines "
        "writes it, so the helper may return `Outcome`.",
    )
    inout = render.render(
        render.HelperShape(
            language="typescript",
            name="settle",
            kind="function",
            is_async=False,
            params=(render.Slot("outcome", "Outcome | undefined"),),
            returns=(render.Slot("outcome", "Outcome | undefined"),),
        )
    )
    assert inout.notes == ()
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


def test_code_no_path_reaches_has_no_bound_inputs():
    from repowise.core.analysis.health.dataflow.assigned import DefiniteAssignment

    fn = _functions(
        """
    def f(x):
        y = x + 1
        return y
        z = y
    """
    )[0]
    assigned = DefiniteAssignment(fn)
    assert {"x", "y"} <= assigned.before(4, 4)
    assert assigned.before(5, 5) is None


_WORDS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")


def _item_section(n: int, *, extra: str = "") -> str:
    """One section of a loop body: a banner and a branch on the item. Each
    reads its own option, so no single span carries more than one section."""
    return f"""
        # Handle {_WORDS[n]}
        if item.kind == "k{n}" and item.ready:
            handle_{n}(item, a, seen, opt_{n}){extra}
        elif item.other_{n} or b:
            skip_{n}(item, last)
"""


def _loop_fn(*, extras: dict[int, str] | None = None, tail: str = "last = item") -> str:
    extras = extras or {}
    body = "".join(_item_section(n, extra=extras.get(n, "")) for n in range(8))
    return f"""
def run(items, a, b, {", ".join(f"opt_{n}" for n in range(8))}):
    seen = []
    last = None
    for item in items:
{textwrap.indent(textwrap.dedent(body), " " * 8)}
        {tail}
    return seen, last
"""


def _spans(plan) -> list[tuple[int, int]]:
    return [(s["span"]["start"], s["span"]["end"]) for s in plan.plan["stages"]]


def test_a_loop_body_is_cut_and_the_loop_stays_in_the_function():
    # hermes ``cron/scheduler.py::_deliver_result``: the work sits in one ``for``.
    src = _loop_fn()
    lines = textwrap.dedent(src).splitlines()
    loop = next(i for i, ln in enumerate(lines, 1) if ln.strip().startswith("for item"))
    plan = _plans(src)["run"]
    spans = _spans(plan)
    assert len(spans) >= 2
    assert all(s > loop for s, _ in spans)
    assert all("item" in s["params"] for s in plan.plan["stages"])
    residual = plan.plan["orchestrator"]
    # The loop's own decision point stays with the function.
    assert residual["ccn_after"] >= 2


def test_a_value_the_next_iteration_reads_comes_back_out_of_its_stage():
    # ``last`` is written in the last section and read by the first on the next
    # iteration: the stage writing it hands it back, the reading one takes it in.
    src = _loop_fn(extras={7: "\n            last = item"})
    stages = _plans(src)["run"].plan["stages"]
    lines = textwrap.dedent(src).splitlines()
    write = next(
        i for i, ln in enumerate(lines, 1) if ln.strip() == "last = item" and ln.startswith(" " * 12)
    )
    writer = next(s for s in stages if s["span"]["start"] <= write <= s["span"]["end"])
    assert "last" in writer["returns"] and "last" in writer["params"]
    assert all("last" in s["params"] for s in stages)


def test_a_continue_leaving_a_section_keeps_that_section_in_the_function():
    src = _loop_fn(extras={3: "\n            if item.done:\n                continue"})
    lines = textwrap.dedent(src).splitlines()
    jump = next(i for i, ln in enumerate(lines, 1) if ln.strip() == "continue")
    spans = _spans(_plans(src)["run"])
    assert len(spans) >= 2
    assert not any(s <= jump <= e for s, e in spans)


def test_one_if_arm_holding_the_work_is_cut():
    body = "".join(_item_section(n) for n in range(8)).replace("item", "cfg")
    src = f"""
def apply(cfg, a, b, seen, last, {", ".join(f"opt_{n}" for n in range(8))}):
    if cfg.enabled:
{textwrap.indent(textwrap.dedent(body), " " * 8)}
    return seen
"""
    lines = textwrap.dedent(src).splitlines()
    head = next(i for i, ln in enumerate(lines, 1) if ln.strip() == "if cfg.enabled:")
    spans = _spans(_plans(src)["apply"])
    assert len(spans) >= 2 and all(s > head for s, _ in spans)


@pytest.mark.parametrize(
    ("language", "ext", "src", "expected"),
    [
        ("python", "py", "for r in rows:\n    if r:\n        break", False),
        ("python", "py", "if r:\n    continue", True),
        # A ``for ... else`` arm is not the loop's body: its break leaves the outer loop.
        ("python", "py", "for r in rows:\n    pass\nelse:\n    break", True),
        ("typescript", "ts", "switch (k) { case 1: break; }", False),
        ("typescript", "ts", "switch (k) { case 1: continue; }", True),
        ("typescript", "ts", "inner: for (const r of rows) { break inner; }", True),
        ("typescript", "ts", "items.forEach(() => { return; });", False),
    ],
)
def test_a_jump_leaves_a_statement_only_when_nothing_in_it_holds_it(language, ext, src, expected):
    from repowise.core.analysis.health.dataflow.loop_cut import escapes as _escapes

    wrapped = (
        f"def f(rows, r, k, items):\n    while True:\n{textwrap.indent(src, ' ' * 8)}\n"
        if language == "python"
        else f"function f(rows, r, k, items) {{ while (true) {{ {src} }} }}"
    )
    fn = _functions(wrapped, language, ext)[0]
    lmap = get_language_map(language)
    loop = next(c for c in fn.fn_node.child_by_field_name("body").named_children if c.type in lmap.loop_kinds)
    body = loop.child_by_field_name("body")
    first = next(c for c in body.named_children if "comment" not in c.type)
    assert _escapes(first, lmap) is expected


def test_a_statement_reading_its_own_target_on_a_later_line_is_never_staged():
    # hermes ``run_conversation``: ``messages = [\n msg for msg in messages ...]``
    # orders its write before its reads by line, so the stage lost ``messages``.
    src = _loop_fn(extras={2: "\n            seen = [\n                s for s in seen if s\n            ]"})
    lines = textwrap.dedent(src).splitlines()
    rebind = next(i for i, ln in enumerate(lines, 1) if ln.strip() == "seen = [")
    spans = _spans(_plans(src)["run"])
    assert not any(s <= rebind <= e for s, e in spans)


def test_an_import_with_a_fallback_binds_on_every_path():
    # hermes ``load_gateway_config``: ``try: from m import r as _pr`` /
    # ``except: _pr = None`` binds ``_pr``; the stage needs no older value.
    fallback = """
        try:
            from reg import registry as hooks
        except ImportError:
            hooks = None
"""
    body = "".join(_item_section(n) + (fallback if n == 7 else "") for n in range(8))
    src = f"""
def run(items, a, b, last, {", ".join(f"opt_{n}" for n in range(8))}):
    seen = []
    hooks = None
    for item in items:
{textwrap.indent(textwrap.dedent(body), " " * 8)}
    return seen, hooks
"""
    stages = _plans(src)["run"].plan["stages"]
    writer = next(s for s in stages if "hooks" in s["returns"])
    assert "hooks" not in writer["params"]


def test_an_input_only_another_stage_writes_refuses_the_plan():
    from repowise.core.analysis.health.dataflow.stages import _inputs_held

    one = Extraction(10, 20, ("a",), (), 5, 3)
    two = Extraction(30, 40, ("x",), (), 5, 3)
    assert not _inputs_held((one, two), {"a": [1], "x": [15]})
    assert _inputs_held((one, two), {"a": [1], "x": [15, 25]})
    assert _inputs_held((replace(one, returns=("x",)), two), {"a": [1], "x": [15]})
    # Only a later stage hands ``p`` back: unbound at the first stage's call.
    three = Extraction(50, 60, (), ("p",), 5, 3)
    first = Extraction(10, 20, ("p",), (), 5, 3)
    assert not _inputs_held((first, two, three), {"p": [55], "x": [1]})
    # Written before the stages too (a loop-carried value): held.
    assert _inputs_held((first, two, three), {"p": [2, 55], "x": [1]})


def _stage_writing(stages, lines, text):
    line = next(i for i, ln in enumerate(lines, 1) if ln.strip() == text)
    return next(s for s in stages if s["span"]["start"] <= line <= s["span"]["end"])


def test_a_counter_only_its_own_stage_reads_comes_back_out():
    # ``count`` is read and bumped by one stage and never after the loop: the
    # next iteration's call must still see the bumped value.
    src = _loop_fn(extras={3: "\n            count += 1"}, tail="seen.sort()")
    src = src.replace("    last = None\n", "    last = None\n    count = 0\n")
    stages = _plans(src)["run"].plan["stages"]
    writer = _stage_writing(stages, textwrap.dedent(src).splitlines(), "count += 1")
    assert "count" in writer["returns"] and "count" in writer["params"]


def test_a_value_the_while_header_reads_comes_back_out():
    body = "".join(_item_section(n) for n in range(8))
    src = f"""
def run(items, a, b, last, {", ".join(f"opt_{n}" for n in range(8))}):
    seen = []
    pos = 0
    while pos < len(items):
        item = items[pos]
{textwrap.indent(textwrap.dedent(body), " " * 8)}
        if item.skip and item.far:
            pos += 2
        elif item.step or a:
            pos += 1
        else:
            pos = len(items)
    return seen
"""
    stages = _plans(src)["run"].plan["stages"]
    writer = _stage_writing(stages, textwrap.dedent(src).splitlines(), "pos += 2")
    assert "pos" in writer["returns"]


def test_an_async_loop_body_gets_awaited_stages():
    src = _loop_fn().replace("def run(", "async def run(").replace("for item in items:", "async for item in items:")
    src = src.replace("handle_", "await handle_")
    stages = _plans(src)["run"].plan["stages"]
    assert len(stages) >= 2
    assert all(s["new_symbol"]["async"] and "await " in s["call_site"]["new_text"] for s in stages)


def test_an_import_whose_handler_only_passes_is_not_bound_by_it():
    fallback = """
        try:
            from reg import registry as hooks
            hooks = wrap(hooks)
        except ImportError:
            pass
"""
    body = "".join(_item_section(n) + (fallback if n == 7 else "") for n in range(8))
    src = f"""
def run(items, a, b, last, {", ".join(f"opt_{n}" for n in range(8))}):
    seen = []
    hooks = None
    for item in items:
{textwrap.indent(textwrap.dedent(body), " " * 8)}
    return seen, hooks
"""
    stages = _plans(src)["run"].plan["stages"]
    writer = next(s for s in stages if "hooks" in s["returns"])
    assert "hooks" in writer["params"]


def _ts_section(n: int) -> str:
    return f"""
    // Handle {_WORDS[n]}
    if (item.kind === "k{n}" && item.ready) {{
      handle{n}(item, a, seen, opt{n});
    }} else if (item.other{n} || b) {{
      skip{n}(item);
    }}
"""


def test_a_stage_ending_in_a_constant_keeps_its_carried_output():
    # The trailing-constant check must see the carried outputs merged in, not
    # liveness alone: this loop-body stage ends in ``flag = False`` (flag is
    # read after the loop) and also advances the loop-carried ``total`` the
    # ``while`` header reads, so its signature is (flag, total) — a real
    # split, not a constant-returning helper.
    src = """
def run(items, limit):
    total = 0
    n = 0
    flag = True
    while total < limit:
        x = items[n]
        if x > limit:
            total += x
        else:
            total -= x
        if total > 1000:
            total = 1000
        flag = False
        n += 1
    return flag
"""
    (fn,) = _functions(src)
    lmap = get_language_map("python")
    blocks = _Blocks(fn, lmap, None, {})
    facts = _FunctionFacts(fn, lmap)
    body = fn.fn_node.child_by_field_name("body")
    looped = [(b, loops) for b, loops in _descent(_unwrap_container(body, lmap.block_kinds), blocks, lmap) if loops]
    assert looped, "expected the cut to descend into the loop body"
    block, loops = looped[0]
    code, pre = blocks.code(block)
    cutter = _Cutter(facts, lmap, None, block, code, pre, 3, loops)
    j = next(k for k, st in enumerate(code) if "flag = False" in (st.text or b"").decode())
    stage = cutter._stage(0, j)
    assert stage is not None
    assert set(stage.returns) == {"flag", "total"}


@pytest.mark.parametrize("shape", ["labelled", "do_while"])
def test_typescript_loop_bodies_are_cut(shape):
    body = textwrap.indent(textwrap.dedent("".join(_ts_section(n) for n in range(8))), "    ")
    opts = ", ".join(f"opt{n}" for n in range(8))
    if shape == "labelled":
        loop = f"  outer: for (const item of items) {{\n{body}\n  }}\n"
    else:
        loop = (
            f"  let item = items[0];\n  do {{\n{body}\n"
            "    if (item.next && item.ok) { item = item.next; } else if (item.done || a) { item = null; }\n"
            "  } while (item);\n"
        )
    src = f"function run(items, a, b, seen, {opts}) {{\n{loop}  return seen;\n}}\n"
    plan = _plans(src, "typescript", "ts")["run"]
    if shape == "labelled":
        # The flow graph keeps a labelled statement whole, so nothing in it
        # is known to be bound: no stage is cut there.
        assert "stages" not in plan.plan
        return
    assert len(plan.plan["stages"]) >= 2
    if shape == "do_while":
        writer = _stage_writing(plan.plan["stages"], src.splitlines(), "if (item.next && item.ok) { item = item.next; } else if (item.done || a) { item = null; }")
        assert writer["returns"] == ["item"]
