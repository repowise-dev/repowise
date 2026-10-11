"""The values a staged or single Extract Method span takes and hands back.

Each case is a shape whose stage interface was wrong on real code (hermes,
openclaw) when an independent name-use pass re-checked every served stage:
a parameter the dialect did not see, an ``except`` clause's own reads and
binding, a ``global`` write, and a ``switch`` / ``match`` arm write the flow
graph takes as certain.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import find_extractions
from repowise.core.analysis.health.refactoring.extract_method import (
    STAGE_REFUSED_DETAIL,
    ExtractMethodDetector,
)
from repowise.core.analysis.health.refactoring.models import RefactoringContext
from tests.unit.health.test_extract_method_stages import (
    _functions,
    _item_section,
    _loop_fn,
    _plans,
    _ts_section,
)


class _Finding:
    def __init__(self, fn):
        self.biomarker_type, self.function_name = "complex_method", fn.name
        self.line_start, self.health_impact, self.details = fn.start_line, 2.0, {}


def _refused(src: str, language: str = "python", ext: str = "py") -> dict[str, list[str]]:
    """Per function, the stage refusal reasons the detector records."""
    fns = _functions(src, language, ext)
    findings = [_Finding(f) for f in fns]
    ctx = RefactoringContext(
        file_path=f"m.{ext}", language=language, nloc=300, findings=findings, function_analyses=fns
    )
    ExtractMethodDetector().detect(ctx)
    return {f.function_name: f.details.get(STAGE_REFUSED_DETAIL, []) for f in findings}


def _spans(src: str, name: str, language: str = "python", ext: str = "py"):
    fn = next(f for f in _functions(src, language, ext) if f.name == name)
    return find_extractions(fn, get_language_map(language))


def _stage_holding(plan, src: str, text: str) -> dict:
    lines = textwrap.dedent(src).splitlines()
    at = next(i for i, ln in enumerate(lines, 1) if ln.strip() == text)
    return next(s for s in plan.plan["stages"] if s["span"]["start"] <= at <= s["span"]["end"])


# -- parameters the signature binds --------------------------------------------


def test_a_typed_splat_parameter_is_bound():
    # hermes ``moa_loop.py::create(self, **api_kwargs: Any)``: the stages read
    # ``api_kwargs`` and the plan passed nothing.
    fn = _functions("def f(a, *args: int, b=1, **kw: str):\n    return a, args, b, kw\n")[0]
    assert [p.name for p in fn.def_use.params] == ["a", "args", "b", "kw"]
    src = _loop_fn(extras={2: "\n            note(kw)"}).replace(
        "opt_7):\n    seen", "opt_7, **kw: int):\n    seen"
    )
    plan = _plans(src)["run"]
    assert "kw" in _stage_holding(plan, src, "note(kw)")["params"]


def test_javascript_default_and_destructured_parameters_are_bound():
    # openclaw ``runGatewayCpuScenarios(options, params = {})`` and
    # ``runBarnacleAutoResponse({ github, context })``: stages read them and
    # the plan passed neither.
    fn = _functions(
        "function f(a, b = 1, { c, d = 2 }, [e], ...r) { return a + b + c + d + e + r; }",
        "javascript",
        "js",
    )[0]
    assert [p.name for p in fn.def_use.params] == ["a", "b", "c", "d", "e", "r"]
    body = textwrap.indent(textwrap.dedent("".join(_ts_section(n) for n in range(8))), "    ")
    opts = ", ".join(f"opt{n}" for n in range(8))
    src = (
        f"function run(items, a = 0, {{ b, seen, {opts} }} = {{}}) {{\n"
        f"  for (const item of items) {{\n{body}\n  }}\n  return seen;\n}}\n"
    )
    stages = _plans(src, "javascript", "js")["run"].plan["stages"]
    assert len(stages) >= 2
    for stage in stages:
        lines = src.splitlines()[stage["span"]["start"] - 1 : stage["span"]["end"]]
        read = {f"opt{n}" for n in range(8) if any(f"opt{n})" in ln for ln in lines)}
        assert read <= set(stage["params"])
        assert "b" in stage["params"] and "seen" in stage["params"]


# -- the head of an exception handler --------------------------------------------


def test_an_except_clause_reads_its_type_and_binds_its_name():
    fn = _functions(
        """
        def f(x):
            from m import AuthError
            try:
                y = g(x)
            except AuthError as exc:
                log(exc)
            except (ValueError, KeyError):
                pass
        """
    )[0]
    handlers = [b for b in fn.cfg.blocks if b.kind == "handler"]
    assert [b.statements[0].start_line for b in handlers] == [6, 8]
    first, second = (fn.def_use.block(b.id) for b in handlers)
    assert [(d.var, d.line) for d in first.defs] == [("exc", 6)]
    assert [(u.name, u.line) for u in first.uses] == [("AuthError", 6), ("log", 7), ("exc", 7)]
    assert {u.name for u in second.uses} == {"ValueError", "KeyError"}


def test_a_closure_handler_binds_its_own_name():
    # hermes ``run_doctor``: a nested check's ``except Exception as e`` read
    # as the outer ``e`` once the outer handlers bound one.
    fn = _functions(
        """
        def f(x):
            try:
                g(x)
            except Exception as e:
                log(e)

            def check():
                try:
                    h()
                except OSError as e:
                    log(e)
            return check
        """
    )[0]
    assert "e" not in {o.name for o in fn.def_use.captured.reads}


def test_a_span_in_a_handler_takes_the_bound_exception():
    # hermes ``gateway/delivery.py::deliver``: the span reading ``str(e)``
    # inside ``except Exception as e`` did not take ``e``.
    src = """
    def deliver(targets, results):
        for target in targets:
            try:
                send(target)
            except Exception as e:
                if target.chat_id and target.platform:
                    kind = classify(str(e))
                    if kind:
                        mark(target, kind)
                results[target.name] = {"error": str(e), "kind": target.kind}
        return results
    """
    fn = _functions(src)[0]
    spans = find_extractions(fn, get_language_map("python"))
    assert spans and all(
        "e" in x.params for x in spans if x.start_line >= 7 and x.end_line <= 11
    )


def test_a_stage_reimports_an_exception_type_the_function_imports():
    # hermes ``cmd_gateway_enroll``: ``except AuthError`` read a function-local
    # import and the stage was not told to import it.
    handler = (
        "\n            try:\n                refresh(item)\n"
        "            except AuthError as exc:\n                report(exc)"
    )
    src = _loop_fn(extras={3: handler}).replace(
        "    seen = []", "    from auth import AuthError\n\n    seen = []"
    )
    plan = _plans(src)["run"]
    notes = _stage_holding(plan, src, "except AuthError as exc:")["new_symbol"].get("notes", [])
    assert any(n.startswith("Import AuthError in the helper") for n in notes)


# -- names the module or an enclosing function owns ------------------------------

_GLOBAL_FN = """
def resolve(items, a, b, {opts}):
    global _CACHE
    _CACHE = None
    seen = []
    last = None
    for item in items:
{body}
        last = item
    return seen, last
"""


def test_a_global_write_never_leaves_its_declaration():
    # hermes ``resolve_nous_access_token``: a stage wrote ``_CACHE = ...`` under
    # the function's ``global`` and the helper would bind a local instead.
    body = "".join(
        _item_section(n, extra="\n            _CACHE = item" if n == 3 else "") for n in range(8)
    )
    src = _GLOBAL_FN.format(
        opts=", ".join(f"opt_{n}" for n in range(8)),
        body=textwrap.indent(textwrap.dedent(body), " " * 8),
    )
    write = next(i for i, ln in enumerate(src.splitlines(), 1) if ln.strip() == "_CACHE = item")
    plan = _plans(src)["resolve"]
    for stage in plan.plan.get("stages") or [plan.plan]:
        assert not stage["span"]["start"] <= write <= stage["span"]["end"]
    fn = _functions(src)[0]
    assert all(
        not x.start_line <= write <= x.end_line
        for x in find_extractions(fn, get_language_map("python"))
    )


def test_a_span_holding_the_declaration_and_every_write_is_offered():
    src = """
    def warm(keys, flag):
        n = len(keys)
        global _CACHE
        if flag and n:
            _CACHE = {}
            for k in keys:
                _CACHE[k] = n
        log(n)
        return n
    """
    fn = _functions(src)[0]
    spans = find_extractions(fn, get_language_map("python"))
    assert any(x.start_line == 4 and x.end_line == 8 for x in spans)


# -- a write in a switch arm ---------------------------------------------------------


def _ts_loop(mode_stmt: str) -> str:
    body = textwrap.indent(textwrap.dedent("".join(_ts_section(n) for n in range(8))), "    ")
    opts = ", ".join(f"opt{n}" for n in range(8))
    return (
        f"function run(items, a, b, seen, mode, last, {opts}) {{\n"
        f"  for (const item of items) {{\n{body}\n    {mode_stmt}\n"
        "    note(last);\n  }\n  return seen;\n}\n"
    )


def test_a_stage_never_reads_past_a_switch_arm_write_it_did_not_take():
    # The flow graph keeps a ``switch`` whole, so its arm's write looks
    # certain and hides the value from before it: a stage holding the
    # ``switch`` and the read after it took no ``last``, and the helper read
    # an unbound name whenever the arm did not run.
    for stmt in ("if (mode === 1) { last = item; }", "switch (mode) { case 1: last = item; break; }"):
        src = _ts_loop(stmt)
        lines = src.splitlines()
        arm = next(i for i, ln in enumerate(lines, 1) if ln.strip() == stmt)
        read = arm + 1
        stages = _plans(src, "typescript", "ts")["run"].plan["stages"]
        assert len(stages) >= 2
        for stage in stages:
            if stage["span"]["start"] <= arm and read <= stage["span"]["end"]:
                assert "last" in stage["params"], stmt


def test_a_global_refusal_is_counted_under_its_reason():
    body = "".join(
        _item_section(n, extra="\n            _CACHE = item" if n == 3 else "") for n in range(8)
    )
    src = _GLOBAL_FN.format(
        opts=", ".join(f"opt_{n}" for n in range(8)),
        body=textwrap.indent(textwrap.dedent(body), " " * 8),
    )
    assert _refused(src)["resolve"] == ["outer_write"]


def test_a_switch_arm_refusal_is_counted_and_a_dead_arm_name_is_not_refused():
    hazard = _ts_loop("switch (mode) { case 1: last = item; break; }")
    assert _refused(hazard, "typescript", "ts")["run"] == ["switch_arm_write"]
    # An arm writing a name nothing reads past the edge decides nothing.
    quiet = _ts_loop("switch (mode) { case 1: tally = item; break; }").replace(
        "mode, last,", "mode, last, tally,"
    )
    assert _refused(quiet, "typescript", "ts")["run"] == []
    stages = _plans(quiet, "typescript", "ts")["run"].plan["stages"]
    arm = next(i for i, ln in enumerate(quiet.splitlines(), 1) if "switch (mode)" in ln)
    assert any(s["span"]["start"] <= arm <= s["span"]["end"] for s in stages)


# -- closures and outer scopes ----------------------------------------------------

_NONLOCAL = """
def outer(items, flag):
    count = 0

    def bump():
        BODY

    for it in items:
        if it and flag:
            bump()
            if it.big:
                bump()
                log(it)
        print(count)
    return count
"""


def test_a_span_calling_a_closure_that_writes_nonlocal_is_not_offered():
    # The closure rebinds the function's ``count`` with no declaration in the
    # function itself: a helper taking ``count`` would print a stale copy.
    plain = _spans(_NONLOCAL.replace("BODY", "log(1)"), "outer")
    assert any(x.start_line == 9 for x in plain)
    assert _spans(_NONLOCAL.replace("BODY", "nonlocal count\n        count += 1"), "outer") == []


def test_only_the_function_s_own_global_statements_count():
    # A nested function's ``global`` is its own: the enclosing function's
    # write of that name stays a local of the enclosing function.
    src = """
    def outer(items, flag):
        _D = 0

        def inner():
            global _D
            _D = 1

        for it in items:
            if it and flag:
                _D = it
                if it.big:
                    log(it)
                    _D += 1
        return _D, inner
    """
    fn = _functions(src)[0]
    assert fn.def_use.outer == {}
    own = _functions("def f():\n    global _C\n    _C = 1\n")[0]
    assert own.def_use.outer == {"_C": frozenset({2})}


# -- exception handler heads ---------------------------------------------------------


def test_a_read_of_the_exception_name_after_its_handler_takes_it_in():
    # Python unbinds ``e`` at the handler's end, which is not modelled: the
    # read after it is an error in the original, and the span is offered with
    # ``e`` as an input (documented over-supply, never a lost value).
    src = """
    def f(items, flag):
        total = len(items)
        try:
            run(items)
        except ValueError as e:
            log(e)
        print(total)
        if flag and items:
            note(e)
            if items[0] or flag:
                warn(e)
                count(items)
            done(items)
        return items
    """
    spans = [x for x in _spans(src, "f") if x.start_line >= 8]
    assert spans and all("e" in x.params for x in spans)


_TS_CATCH = """function f(items, flag) {
  for (const it of items) {
    try {
      run(it);
    } catch ({ message }) {
      if (flag && it.big) {
        log(message);
        if (it.small || flag) {
          warn(message, it);
        }
      }
      report(message);
    }
  }
  return items;
}
"""


def test_typescript_catch_binds_its_parameter_pattern():
    fn = _functions(_TS_CATCH, "typescript", "ts")[0]
    # The handler entry turns ``branch`` when an ``if`` opens it: find it by its head.
    handler = next(b for b in fn.cfg.blocks if b.statements and b.statements[0].kind == "catch_clause")
    head = fn.def_use.block(handler.id)
    assert [(d.var, d.declares) for d in head.defs] == [("message", True)]
    spans = _spans(_TS_CATCH, "f", "typescript", "ts")
    assert spans and all("message" in x.params for x in spans)


def test_a_closure_catch_parameter_is_its_own():
    fn = _functions(
        """function f(xs) {
  try { run(xs); } catch (err) { log(err); }
  const g = () => {
    try {
      run();
    } catch (err) {
      log(err);
    }
  };
  return g;
}
""",
        "typescript",
        "ts",
    )[0]
    captured = fn.def_use.captured
    assert "err" not in {o.name for o in (*captured.reads, *captured.shared, *captured.writes)}


def test_except_star_and_nested_handlers_get_heads():
    fn = _functions(
        """
        def f(x):
            try:
                g(x)
            except* ValueError as group:
                try:
                    h(group)
                except KeyError as inner:
                    log(inner, group)
        """
    )[0]
    heads = {
        (b.statements[0].start_line, b.statements[0].is_head)
        for b in fn.cfg.blocks
        if b.kind == "handler"
    }
    assert heads == {(5, True), (8, True)}
    defs = {(d.var, d.line) for d in fn.def_use.definitions}
    assert {("group", 5), ("inner", 8)} <= defs


@pytest.mark.parametrize(
    ("language", "ext", "src"),
    [
        ("java", "java", "class A { int f(int x) { try { g(x); } catch (Exception e) { h(e); } return x; } }"),
        ("csharp", "cs", "class A { int F(int x) { try { G(x); } catch (Exception e) { H(e); } return x; } }"),
    ],
)
def test_other_languages_read_nothing_new_at_a_handler_head(language, ext, src):
    fn = _functions(src, language, ext)[0]
    handler = next(b for b in fn.cfg.blocks if b.kind == "handler")
    head = handler.statements[0]
    assert head.is_head
    bdu = fn.def_use.block(handler.id)
    assert [d for d in bdu.defs if d.line == head.start_line] == []
    assert [u for u in bdu.uses if u.line == head.start_line and u.name == "Exception"] == []


def test_a_handler_rebinding_a_name_in_a_loop_carries_nothing():
    # hermes ``_install_memory_provider_external_dependencies``: each handler
    # binds its own ``exc``; the span holding the second one is not loop-carried.
    src = """
    def install(deps, results):
        for dep in deps:
            try:
                setup(dep)
            except Exception as exc:
                results.append(str(exc))
            if dep.check and dep.name:
                try:
                    verify(dep)
                    if dep.slow or dep.big:
                        wait(dep)
                except Exception as exc:
                    results.append(str(exc))
            log(dep)
            record(dep, results)
            notify(dep)
            flush(results)
        return results
    """
    assert any(x.start_line == 8 for x in _spans(src, "install"))


_TRY_FN = """
def run(items, a, b, {opts}):
    seen = []
    last = None
    done = 0
    try:
        for item in items:
{body}
            last = item
    except Exception:
        report(done)
        raise
    return seen, last
"""


def test_a_stage_writing_what_a_handler_reads_is_refused_and_counted():
    # A helper that raises loses its writes, so the handler would see the
    # count from before the stage instead of the one the original reached.
    def src(extra: str) -> str:
        body = "".join(_item_section(n, extra=extra if n == 3 else "") for n in range(8))
        return _TRY_FN.format(
            opts=", ".join(f"opt_{n}" for n in range(8)),
            body=textwrap.indent(textwrap.dedent(body), " " * 12),
        )

    hazard = src("\n                done = done + 1")
    assert "exception_state" in _refused(hazard)["run"]
    stages = _plans(hazard)["run"].plan["stages"]
    lines = textwrap.dedent(hazard).splitlines()
    write = next(i for i, ln in enumerate(lines, 1) if "done = done + 1" in ln)
    after = next(i for i, ln in enumerate(lines, 1) if 'item.kind == "k4"' in ln)
    assert len(stages) >= 2
    # A stage may end on the write (nothing after it can raise), not run past it.
    assert not any(s["span"]["start"] <= write and after <= s["span"]["end"] for s in stages)
    assert _refused(src(""))["run"] == []
