"""Extract Method knows whether the helper is a method and what it writes.

A span that uses ``self`` / ``this`` / a Go receiver has to become a method
sharing that instance, and a span that assigns the receiver's fields changes
the object the rest of the function sees. The slicer records both in the same
per-statement walk it already runs; each language's def/use dialect names its
receiver. Where lifting the span would change what the receiver is (a Go
value receiver written, ``this`` in a function outside a class), the step is a
judgment call. A language that cannot tell says unknown, never "no".
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, replace

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import (
    analyze_file,
    find_extractions,
    get_defuse_dialect,
)
from repowise.core.analysis.health.dataflow.dialects.base import (
    NO_RECEIVER,
    BaseDefUseDialect,
)
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.identity import refactoring_public_id
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


def _receiver(language: str, fn):
    return get_defuse_dialect(language).receiver(fn.fn_node, get_language_map(language))


def _facts(language: str, ext: str, src: str, index: int = 0):
    """``(uses_receiver, receiver_assigns)`` per candidate span, keyed by span."""
    fn = _functions(language, ext, src)[index]
    rec = _receiver(language, fn)
    return {
        (x.start_line, x.end_line): (x.uses_receiver, x.receiver_assigns)
        for x in find_extractions(fn, get_language_map(language), rec)
    }


def _span(facts: dict, start: int):
    """The facts of the candidate span opening on *start* (all agree)."""
    found = {v for (s, _e), v in facts.items() if s == start}
    assert len(found) == 1, (start, facts)
    return found.pop()


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


# Lines 4-11 compute a local; lines 12-20 write the instance's fields.
_PY = """
class Acc:
    def run(self, items, limit):
        total = 0
        for it in items:
            if it > limit:
                total += it
            elif it < 0:
                total -= it
            else:
                total += 1
        if total > 10:
            self.big = True
            self.hits[total] = 1
        elif total < 0:
            self.small += 1
        else:
            self.small = 0
        self.done = total
        return total
"""


def test_python_span_writing_self_fields_is_a_method_and_names_them():
    facts = _facts("python", "py", _PY)
    assert _span(facts, 4) == (False, ())
    assert _span(facts, 12) == (True, ("big", "done", "hits", "small"))


def test_python_plan_carries_new_symbol():
    plans = {p.plan["span"]["start"]: p for p in _plans("python", "py", _PY)}
    (plan,) = plans.values()
    sym = {
        k: v
        for k, v in plan.plan["new_symbol"].items()
        if k not in ("params", "returns", "signature_text")
    }
    if plan.plan["span"]["start"] >= 12:
        assert sym == {
            "kind": "method",
            "async": False,
            "receiver": "self",
            "uses_receiver": True,
            "assigns": ["big", "done", "hits", "small"],
        }
    else:
        assert sym == {
            "kind": "function",
            "async": False,
            "receiver": None,
            "uses_receiver": False,
            "assigns": [],
        }
    assert "receiver_hazard" not in plan.plan
    assert classify_step(plan).classification == "mechanical"


def test_python_staticmethod_and_module_function_have_no_receiver():
    src = """
    class A:
        @staticmethod
        def s(self, x):
            return x

        def m(self):
            return self

    def free(self):
        return self
    """
    fns = {f.name: f for f in _functions("python", "py", src)}
    assert _receiver("python", fns["s"]) is NO_RECEIVER
    assert _receiver("python", fns["free"]) is NO_RECEIVER
    assert _receiver("python", fns["m"]).names == frozenset({"self"})


_TS = """
class Acc {
  run(items: number[], limit: number) {
    let total = 0;
    for (const it of items) {
      if (it > limit) {
        total += it;
      } else {
        total -= it;
      }
    }
    if (total > 10) {
      items.forEach((x) => { this.n++; });
    } else {
      this.small = total;
    }
    this.done = total;
    return total;
  }
}
"""


def test_typescript_this_in_an_arrow_counts_and_increments_are_writes():
    facts = _facts("typescript", "ts", _TS)
    assert _span(facts, 4) == (False, ())
    assert _span(facts, 12) == (True, ("done", "n", "small"))


@pytest.mark.parametrize(
    ("src", "bound"),
    [
        ("class A { m() { return 1; } }", True),
        ("class A { f = () => { return 1; }; }", True),
        ("function f() { return 1; }", False),
        ("const o = { m() { return 1; } };", False),
    ],
)
def test_typescript_this_is_bound_only_inside_a_class(src, bound):
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    lmap = get_language_map("typescript")
    root = Parser(_get_language("typescript")).parse(src.encode()).root_node
    stack, fn = [root], None
    while stack and fn is None:
        node = stack.pop()
        if node.type in lmap.function_kinds | lmap.lambda_kinds:
            fn = node
        stack.extend(reversed(node.children))
    assert get_defuse_dialect("typescript").receiver(fn, lmap).bound is bound


_PLAIN_JS = """
function run(items, limit) {
  // Comment lines keep the span under the share of the function a plan
  // may take, so the span below is the one offered.
  //
  //
  //
  //
  //
  //
  //
  //
  const a = items.length;
  const b = limit * 2;
  const c = a + b;
  const d = c - limit;
  const e = d * a;
  const f = e + 1;
  log(a, b, c, d, e, f);
  if (a > limit) {
    this.big = true;
    this.count = a;
  } else if (a > 5) {
    this.mid = true;
    this.count = b;
  } else {
    this.small = true;
    this.count = c;
  }
  this.done = f;
  return f;
}
"""


def test_this_in_a_plain_function_is_a_judgment_call():
    (plan,) = _plans("javascript", "js", _PLAIN_JS)
    assert plan.plan["new_symbol"]["assigns"]
    assert plan.plan["receiver_hazard"] == "receiver_unbound"
    assert plan.plan["new_symbol"]["kind"] is None
    assert classify_step(plan).reasons == ("receiver_unbound",)


_GO = """
package m

func (s {recv}) Run(items []int, limit int) int {{
	total := 0
	for _, it := range items {{
		if it > limit {{
			total += it
		}} else {{
			total -= it
		}}
	}}
	if total > 10 {{
		s.big = true
		s.n++
	}} else {{
		s.small = 1
	}}
	s.done = total
	return total
}}
"""


def test_go_value_receiver_write_is_flagged_and_pointer_is_not():
    facts = _facts("go", "go", _GO.format(recv="Acc"))
    assert _span(facts, 13) == (True, ("big", "done", "n", "small"))
    fn = _functions("go", "go", _GO.format(recv="Acc"))[0]
    assert _receiver("go", fn).copy is True
    fn = _functions("go", "go", _GO.format(recv="*Acc"))[0]
    assert _receiver("go", fn).copy is False


def test_go_hazard_classifies_judgment():
    from repowise.core.analysis.health.refactoring.extract_method import _receiver_hazard

    fn = _functions("go", "go", _GO.format(recv="Acc"))[0]
    rec = _receiver("go", fn)
    assert _receiver_hazard(rec, True, ("big",)) == "receiver_copy_written"
    assert _receiver_hazard(rec, True, ()) is None
    assert _receiver_hazard(replace(rec, copy=False), True, ("big",)) is None


_JAVA = """
class Acc {{
  {static}int run(int[] items, int limit) {{
    int total = 0;
    for (int it : items) {{
      if (it > limit) {{
        total += it;
      }} else {{
        total -= it;
      }}
    }}
    if (total > 10) {{
      this.big = true;
      {bare}
    }} else {{
      this.small = 1;
    }}
    this.done = total;
    return total;
  }}
}}
"""


def test_java_bare_field_write_makes_writes_unknown():
    facts = _facts("java", "java", _JAVA.format(static="", bare="count++;"))
    assert _span(facts, 4) == (None, ())  # a bare name may be a field
    assert _span(facts, 12) == (True, None)
    facts = _facts("java", "java", _JAVA.format(static="", bare="items[0] = 1;"))
    assert _span(facts, 12) == (True, ("big", "done", "small"))


def test_java_static_method_has_no_receiver():
    fn = _functions("java", "java", _JAVA.format(static="static ", bare="count++;"))[0]
    assert _receiver("java", fn) is NO_RECEIVER


def test_rust_and_cpp_receivers():
    rust = """
    impl A {
        fn f(&mut self) {}
        fn g() {}
    }
    """
    fns = {f.name: f for f in _functions("rust", "rs", rust)}
    assert _receiver("rust", fns["f"]).names == frozenset({"self"})
    assert _receiver("rust", fns["g"]) is NO_RECEIVER
    cpp = """
    class A {
      void f() { x = 1; }
      static void g() { }
    };
    void A::h() { }
    void free_fn() { }
    """
    fns = {f.name: f for f in _functions("cpp", "cpp", cpp)}
    rec = {name: _receiver("cpp", fn) for name, fn in fns.items()}
    assert rec["f"].implicit is True
    assert rec["g"] is NO_RECEIVER
    assert rec["A::h"] is None  # whether it is static lives in the class
    assert rec["free_fn"] is NO_RECEIVER


def test_a_language_without_a_receiver_model_says_unknown():
    assert BaseDefUseDialect().receiver(None, get_language_map("python")) is None


def test_new_symbol_does_not_change_the_public_id():
    (plan,) = _plans("python", "py", _PY)
    bare = RefactoringSuggestion(
        **{
            **plan.__dict__,
            "plan": {k: v for k, v in plan.plan.items() if k != "new_symbol"},
        }
    )
    assert refactoring_public_id(plan) == refactoring_public_id(bare)


def test_the_same_walk_gives_whole_function_facts():
    """One walk serves a span and a whole body: a function's awaits and
    receiver assignments come from its body's statements."""
    from repowise.core.analysis.health.dataflow.slice import _scan_for, _span_metrics

    src = """
    class A:
        async def run(self, rows):
            if not rows:
                return
            for r in rows:
                if r is None:
                    raise ValueError(r)
                self.seen += 1
                yield await r.load()
    """
    (fn,) = _functions("python", "py", src)
    lmap = get_language_map("python")
    body = fn.fn_node.child_by_field_name("body").named_children
    m = _span_metrics(body, _scan_for(lmap, _receiver("python", fn), fn.fn_node))
    assert (m.jump, m.awaits) == (True, True)
    assert (m.receiver_use, m.receiver_assigns) == (True, frozenset({"seen"}))


def _first_named(language: str, src: str, kinds: frozenset[str]):
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    root = Parser(_get_language(language)).parse(textwrap.dedent(src).encode()).root_node
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in kinds:
            return node
        stack.extend(reversed(node.children))
    raise AssertionError(kinds)


_PY_INNER = """
class A:
    def m(self):
        def inner(v):
            {body}
"""


@pytest.mark.parametrize(
    ("language", "src", "kind", "expected"),
    [
        # A nested def reaching the outer self: unknown, not "no receiver".
        ("python", _PY_INNER.format(body="self.x = v"), "function_definition", None),
        ("python", _PY_INNER.format(body="return v"), "function_definition", "none"),
        ("go", "package m\nfunc (s *S) M() { f := func() { s.n = 1 }; f() }\n", "func_literal", None),
        ("go", "package m\nfunc (s *S) M() { f := func() { x := 1; _ = x }; f() }\n", "func_literal", "none"),
        ("java", "class A { void m() { Runnable r = () -> { x = 1; }; } }", "lambda_expression", None),
        ("java", "class A { static void m() { Runnable r = () -> { x = 1; }; } }", "lambda_expression", "none"),
        ("cpp", "struct A { void m() { auto f = [this]() { x = 1; }; } };", "lambda_expression", None),
    ],
)
def test_a_nested_function_reaching_the_outer_receiver_is_unknown(language, src, kind, expected):
    lmap = get_language_map(language)
    outer_kinds = lmap.function_kinds
    node = _first_named(language, src, frozenset({kind}))
    if kind in outer_kinds:  # the nested def, not the method holding it
        stack = list(node.child_by_field_name("body").children)
        while stack:
            cur = stack.pop()
            if cur.type == kind:
                node = cur
                break
            stack.extend(cur.children)
    rec = get_defuse_dialect(language).receiver(node, lmap)
    assert (rec is NO_RECEIVER if expected == "none" else rec is None), rec


def test_super_counts_as_a_receiver_use():
    src = """
    class A(B):
        def run(self, items, limit):
            total = 0
            for it in items:
                if it > limit:
                    total += it
                elif it < 0:
                    total -= it
                else:
                    total += 1
            print(total)
            if total > 10:
                super().flush(total)
                print(total)
            else:
                print(-total)
                print(limit)
            return total
    """
    facts = _facts("python", "py", src)
    assert _span(facts, 12) == (True, ())


def test_unpacking_targets_are_assignments_and_an_unread_shape_is_unknown():
    src = """
    class A:
        def run(self, items, limit):
            total = 0
            for it in items:
                if it > limit:
                    total += it
                elif it < 0:
                    total -= it
                else:
                    total += 1
            print(total)
            if total > 10:
                self.a, (self.b, x) = total, (1, 2)
                print(x)
            else:
                self.c = 0
                print(limit)
            return total
    """
    assert _span(_facts("python", "py", src), 12) == (True, ("a", "b", "c"))
    rebinding = src.replace("self.c = 0", "self = other")
    assert _span(_facts("python", "py", rebinding), 12) == (True, None)


def test_typescript_static_method_this_is_the_class():
    node = _first_named(
        "typescript", "class A { static m() { return this; } }", frozenset({"method_definition"})
    )
    assert get_defuse_dialect("typescript").receiver(node, get_language_map("typescript")).bound is False
