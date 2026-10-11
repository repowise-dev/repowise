"""The values a staged or single Extract Method span takes and hands back.

Each case is a shape whose stage interface was wrong on real code (hermes,
openclaw) when an independent name-use pass re-checked every served stage:
a parameter the dialect did not see, an ``except`` clause's own reads and
binding, a ``global`` write, and a ``switch`` / ``match`` arm write the flow
graph takes as certain.
"""

from __future__ import annotations

import textwrap

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import find_extractions
from tests.unit.health.test_extract_method_stages import (
    _functions,
    _item_section,
    _loop_fn,
    _plans,
    _ts_section,
)


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
