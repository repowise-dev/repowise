"""An Extract Method plan writes out the helper's header and its call.

An agent applying a plan used to rebuild the helper from bare names: whether
it is a method, async, which types its parameters take, how the call declares
the output. The plan now carries ``new_symbol.signature_text`` and
``call_site.new_text`` in the file's language, typed parameters read off
their declarations, and ``<name>`` / ``<type>`` placeholders where the code
anchors neither. Spec text only: nothing here edits a file.
"""

from __future__ import annotations

import pathlib
import re
import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.dataflow import (
    Extraction,
    analyze_file,
    get_defuse_dialect,
)
from repowise.core.analysis.health.refactoring import extract_method as em
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.models import (
    RefactoringContext,
    RefactoringSuggestion,
)
from repowise.core.analysis.health.refactoring.render import (
    PARAM_MODES,
    HelperShape,
    Slot,
    private_name,
    render,
    symbol_params,
)


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


# One loop removing three decision points; the guard's ``return`` keeps any
# span from running on into the filler, so the loop is the one offered.
# ``{call}`` awaits or not.
_PY = """
class Store:
    async def run(self, items: list[int], limit: int) -> int:
        total: int = 0
        for i in items:
            if i > limit:
                total += i
            elif i < 0:
                total -= i
            else:
                {call}
            print(i)
            print(i)
        if limit is None:
            return 0
{tail}        return total
"""


def _py_plan(call: str = "await self.flush(i)"):
    tail = "".join(f"        print({n})\n" for n in range(14))
    src = _PY.replace("{call}", call).replace("{tail}", tail)
    fns = _functions("python", "py", src)
    ctx = RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=[_Finding("complex_method", f.name, f.start_line, 1.5) for f in fns],
        function_analyses=fns,
    )
    (plan,) = ExtractMethodDetector().detect(ctx)
    return plan.plan


def test_python_plan_carries_an_awaited_method_header_and_call():
    plan = _py_plan()
    sym = plan["new_symbol"]
    assert plan["suggested_name"] is None  # nothing anchors a name here
    assert sym["signature_text"] == (
        "async def <name>(self, items: list[int], limit: int) -> int:"
    )
    assert plan["call_site"] == {
        "replace_span": plan["span"],
        "new_text": "total = await self.<name>(items, limit)",
    }
    # The receiver is not a parameter of a method.
    assert sym["params"] == [
        {"name": "items", "type": "list[int]", "mode": "in"},
        {"name": "limit", "type": "int", "mode": "in"},
    ]
    assert sym["returns"] == [{"name": "total", "type": "int"}]


def test_a_span_that_does_not_await_is_not_awaited():
    plan = _py_plan("print(-i)")
    assert plan["new_symbol"]["signature_text"].startswith("def <name>(items: list[int]")
    assert "await" not in plan["call_site"]["new_text"]


def test_python_names_take_the_private_form():
    assert private_name("python", "compute_total") == "_compute_total"
    assert private_name("python", "_already") == "_already"
    assert private_name("go", "computeTotal") == "computeTotal"
    assert private_name("python", None) is None


def _shape(language: str, **kw) -> HelperShape:
    base = dict(
        language=language,
        name="loadTotals",
        kind="method",
        is_async=False,
        params=(Slot("items", "T1"), Slot("limit", None)),
        returns=(Slot("total", None),),
    )
    return HelperShape(**{**base, **kw})


@pytest.mark.parametrize(
    ("shape", "sig", "call"),
    [
        (
            _shape("typescript", is_async=True, out_declared=True, returns=(Slot("total", "number"),)),
            "private async loadTotals(items: T1, limit): Promise<number> {",
            "const total = await this.loadTotals(items, limit);",
        ),
        (
            _shape("javascript", kind="function", out_declared=True, out_rebound=True),
            "function loadTotals(items: T1, limit) {",
            "let total = loadTotals(items, limit);",
        ),
        (
            _shape("go", receiver="s", receiver_decl="(s *S)", out_declared=True),
            "func (s *S) loadTotals(items T1, limit <type>) <type> {",
            "total := s.loadTotals(items, limit)",
        ),
        (
            # ``x, err :=`` in the span redeclared ``total``; alone it assigns.
            _shape("go", receiver="s", receiver_decl="(s *S)", out_declared=True,
                   out_written_before=True),
            "func (s *S) loadTotals(items T1, limit <type>) <type> {",
            "total = s.loadTotals(items, limit)",
        ),
        (
            _shape("java", kind="function", out_declared=True),
            "private static <type> loadTotals(T1 items, <type> limit) {",
            "var total = loadTotals(items, limit);",
        ),
        (
            _shape("rust", is_async=True, receiver_decl="mut self", out_declared=True, out_rebound=True),
            "async fn loadTotals(&mut self, items: T1, limit: <type>) -> <type> {",
            "let mut total = self.loadTotals(items, limit).await;",
        ),
        (
            _shape("cpp", returns=(Slot("total", "int"),), out_declared=True),
            "int loadTotals(T1 items, <type> limit) {",
            "int total = loadTotals(items, limit);",
        ),
        (
            _shape("cpp", receiver_decl="const", returns=(Slot("total", "const Row&"),)),
            "const Row loadTotals(T1 items, <type> limit) const {",
            "total = loadTotals(items, limit);",
        ),
        (
            _shape("c", kind="function", out_declared=True),
            "static <type> loadTotals(T1 items, <type> limit) {",
            "<type> total = loadTotals(items, limit);",
        ),
        (
            _shape("cpp", kind="function", name=None, returns=()),
            "static void <name>(T1 items, <type> limit) {",
            "<name>(items, limit);",
        ),
        (
            _shape("python", kind="method", receiver="cls", name="_load", returns=()),
            "@classmethod\ndef _load(cls, items: T1, limit):",
            "cls._load(items, limit)",
        ),
    ],
)
def test_each_language_writes_its_own_header_and_call(shape, sig, call):
    assert render(shape)[:2] == (sig, call)


def test_rust_borrows_a_value_the_host_reads_after_the_call():
    shape = _shape(
        "rust",
        kind="function",
        params=(
            Slot("rows", "Vec<Row>", read_after=True),
            Slot("n", "usize", read_after=True),
            Slot("cfg", None, read_after=True),
            Slot("tmp", "String"),
        ),
        returns=(),
    )
    sig, call, notes = render(shape)
    assert sig == "fn loadTotals(rows: &Vec<Row>, n: usize, cfg: <type>, tmp: String) {"
    assert call == "loadTotals(&rows, n, cfg, tmp);"
    assert notes == (
        "Pass cfg by reference (&) unless the type is Copy: the caller still reads it "
        "after the call.",
    )


def test_an_awaiting_span_in_a_host_that_is_not_async_gets_no_call():
    out = render(_shape("python", is_async=True, async_host=False, receiver="self"))
    assert out.signature.startswith("async def loadTotals(self")
    assert out.call is None
    assert "not async" in out.notes[0]


def test_a_cpp_coroutine_helper_leaves_its_return_type_to_fill():
    out = render(_shape("cpp", is_async=True, returns=(Slot("total", "int"),), out_declared=True))
    assert out.signature == "<type> loadTotals(T1 items, <type> limit) {"
    assert out.call == "int total = co_await loadTotals(items, limit);"
    assert out.notes == ("Give the helper the coroutine return type the caller co_awaits.",)


def test_cpp_const_member_function_is_named_by_its_dialect():
    src = "class A { int f(int a) const { int x = a; return x; } };"
    (fn,) = _functions("cpp", "cpp", src)
    assert get_defuse_dialect("cpp").receiver_decl(fn.fn_node) == "const"
    src = "class A { int f(int a) { int x = a; return x; } };"
    (fn,) = _functions("cpp", "cpp", src)
    assert get_defuse_dialect("cpp").receiver_decl(fn.fn_node) is None


def test_types_are_found_for_a_non_ascii_name():
    """Tree points count bytes, so the lookup spans the name's bytes."""
    src = """
    def f(rows):
        zähler: int = 0
        for r in rows:
            zähler += 1
        return zähler
    """
    (fn,) = _functions("python", "py", src)
    span = Extraction(4, 5, ("rows", "zähler"), ("zähler",), slice_nloc=2, ccn_removed=1)
    assert em._declared_types(fn, span, get_defuse_dialect("python")) == {"zähler": "int"}


def test_an_unknown_helper_form_writes_no_text():
    """``kind`` None: the span may reach its object through a name a function
    would not have, so a function header would be a confident wrong spec."""
    assert render(_shape("python", kind=None)) is None
    assert render(_shape("kotlin")) is None


def test_a_value_the_helper_also_returns_is_inout():
    params = symbol_params((Slot("acc", "int"), Slot("xs")), (Slot("acc", "int"),))
    assert [p["mode"] for p in params] == ["inout", "in"]


_TYPED_SOURCES = {
    "python": ("py", "def f(a: int, b, c: list[str] = None):\n    x: dict[str, int] = {}\n"),
    "typescript": ("ts", "function f(a: number, b?: string, c = 3) { const x: Map<string, number> = new Map(); }"),
    "go": ("go", "package m\nfunc f(a, b int, c string) { var x []string; y := 2 }"),
    "java": ("java", "class C { void f(final int a, String[] b, int c) { int x = 3; var y = 2; } }"),
    "rust": ("rs", "fn f(a: i32, mut b: &mut Vec<u8>, (c, d): (i32, i32)) { let x: u64 = 3; }"),
    "cpp": ("cpp", "void f(int a, const char *b, std::vector<int>& c) { auto x = 2; int *p = nullptr; }"),
}
_EXPECTED_TYPES = {
    "python": {"a": "int", "b": None, "c": "list[str]", "x": "dict[str, int]"},
    "typescript": {"a": "number", "b": "string", "c": None, "x": "Map<string, number>"},
    "go": {"a": "int", "b": "int", "c": "string", "x": "[]string", "y": None},
    "java": {"a": "int", "b": "String[]", "c": "int", "x": "int", "y": None},
    "rust": {"a": "i32", "b": "&mut Vec<u8>", "c": None, "d": None, "x": "u64"},
    "cpp": {"a": "int", "b": "const char*", "c": "std::vector<int>&", "x": None, "p": "int*"},
}


@pytest.mark.parametrize("language", sorted(_TYPED_SOURCES))
def test_declared_types_are_read_off_the_parse(language: str):
    ext, src = _TYPED_SOURCES[language]
    (fn,) = _functions(language, ext, src)
    dialect = get_defuse_dialect(language)
    found = {}
    for d in fn.def_use.definitions:
        row = d.line - 1
        node = fn.fn_node.descendant_for_point_range((row, d.column), (row, d.column + len(d.var)))
        found[d.var] = dialect.declared_type(node)
    assert {k: found.get(k) for k in _EXPECTED_TYPES[language]} == _EXPECTED_TYPES[language]


def test_a_type_comes_from_the_latest_typed_write_before_the_span_end():
    src = """
    def f(rows):
        count: int = 0
        for r in rows:
            if r:
                count += 1
            elif r is None:
                count -= 1
            print(r)
        print(1)
        print(2)
        print(3)
        print(4)
        return count
    """
    (fn,) = _functions("python", "py", src)
    # The loop: ``count`` is read and written there, last typed at line 3.
    span = Extraction(4, 9, ("count", "rows"), ("count",), slice_nloc=6, ccn_removed=3)
    assert em._declared_types(fn, span, get_defuse_dialect("python")) == {"count": "int"}


def test_param_modes_match_the_wire_type():
    src = (pathlib.Path(__file__).resolve().parents[3] / "packages/types/src/refactoring.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(r"export type ExtractParamMode =(.*?);", src, re.DOTALL)
    assert match
    assert set(re.findall(r'"([a-z_]+)"', match.group(1))) == set(PARAM_MODES)


def test_list_rows_leave_the_texts_to_plan_detail():
    """A list serves every plan, so its rows stay as they were before the
    texts existed; one plan's detail carries them."""
    import dataclasses
    import json

    from repowise.core.analysis.health.refactoring.recommendations import build_recommendations

    plan = _py_plan()
    suggestion = RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path="m.py",
        target_symbol="run",
        line_start=3,
        line_end=30,
        plan=plan,
        evidence={"slice_nloc": 9, "ccn_removed": 3},
        impact_delta=1.0,
        effort_bucket="S",
        blast_radius={"scope": "local"},
        confidence="high",
    )
    before = dict(plan)
    before.pop("call_site")
    before["new_symbol"] = {
        k: v
        for k, v in plan["new_symbol"].items()
        if k not in ("params", "returns", "signature_text")
    }
    (with_texts,) = build_recommendations([suggestion])
    (without,) = build_recommendations([dataclasses.replace(suggestion, plan=before)])
    assert json.dumps(with_texts.as_dict()) == json.dumps(without.as_dict())
    detail = with_texts.detail_dict()["plan"]
    assert detail["call_site"] == plan["call_site"]
    assert detail["new_symbol"]["signature_text"] == plan["new_symbol"]["signature_text"]
    # The stored plan itself is untouched by the list projection.
    assert "call_site" in suggestion.plan


def test_cli_json_plan_rows_leave_the_texts_out():
    from repowise.cli.commands.health_cmd.refactoring_targets import _list_row

    plan = _py_plan()
    row = _list_row({"id": "p", "plan": plan})
    assert "call_site" not in row["plan"]
    assert "signature_text" not in row["plan"]["new_symbol"]
    assert row["plan"]["new_symbol"]["kind"] == plan["new_symbol"]["kind"]
    assert _list_row({"id": "q", "plan": None}) == {"id": "q", "plan": None}


def test_read_after_names_the_inputs_the_host_still_uses():
    src = """
    def f(rows, cfg):
        total = 0
        for r in rows:
            total += r
        print(cfg, total)
        return total
    """
    (fn,) = _functions("python", "py", src)
    span = Extraction(4, 5, ("cfg", "rows", "total"), ("total",), slice_nloc=2, ccn_removed=1)
    assert em._read_after(fn, span) == {"cfg", "total"}
