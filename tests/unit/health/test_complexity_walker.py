"""Unit tests for the tree-sitter complexity walker.

These tests are best-effort: tree-sitter language packs may not all be
installed in CI. The walker returns ``[]`` when a language pack is
missing, so each assertion guards with ``pytest.skip`` rather than fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import walk_file, walk_file_complexity

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _walk(rel_path: str, language: str):
    p = FIXTURES / rel_path
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    results = walk_file_complexity(str(p), language, p.read_bytes())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return results


def _walk_classes(rel_path: str, language: str):
    p = FIXTURES / rel_path
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    fcx = walk_file(str(p), language, p.read_bytes())
    if not fcx.classes:
        pytest.skip(f"tree-sitter language pack missing / no classes for {language}")
    return {c.name: c for c in fcx.classes}


def _find(results, name):
    matches = [r for r in results if r.name == name]
    return matches[0] if matches else None


def _require_language(language: str) -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


def test_tcc_cohesive_vs_split():
    """TCC is 1.0 when every method pair shares a field, lower when the class
    splinters into disjoint field groups."""
    _require_language("python")

    cohesive = (
        b"class C:\n"
        b"    def a(self):\n        self.x = 1\n"
        b"    def b(self):\n        return self.x\n"
        b"    def c(self):\n        self.x += 1\n"
    )
    split = (
        b"class D:\n"
        b"    def a(self):\n        self.x = 1\n"
        b"    def b(self):\n        return self.x\n"
        b"    def c(self):\n        self.y = 1\n"
        b"    def d(self):\n        return self.y\n"
    )
    c = next(k for k in walk_file("c.py", "python", cohesive).classes if k.name == "C")
    d = next(k for k in walk_file("d.py", "python", split).classes if k.name == "D")

    assert c.tcc == 1.0
    # D has 6 method pairs, 2 connected (a-b via x, c-d via y) -> 1/3.
    assert d.tcc == pytest.approx(2 / 6)
    assert d.tcc < c.tcc


def test_python_nested_depth():
    results = _walk("python/nested.py", "python")
    deep = _find(results, "deeply_nested")
    shallow = _find(results, "shallow")
    assert deep is not None
    assert shallow is not None
    assert deep.max_nesting >= 4, f"expected ≥4 nesting, got {deep.max_nesting}"
    assert shallow.max_nesting == 0
    assert deep.ccn > shallow.ccn


def test_python_complex_method_ccn():
    results = _walk("python/complex.py", "python")
    many = _find(results, "many_branches")
    assert many is not None
    assert many.ccn >= 9, f"expected CCN ≥ 9, got {many.ccn}"


def test_python_comment_in_parameter_list_not_counted():
    """#1775: a comment inside a parameter list must not inflate param_count.

    ``Annotated[T, Form(...)]`` metadata and an explanatory comment block are
    idiomatic in FastAPI/Pydantic signatures. The comment node is named but
    carries no arity; before the fix it was counted, so a 5-parameter handler
    reported 6+ (and worse on longer signatures) and tripped a spurious
    high-arity finding.
    """
    src = """async def trigger_scan(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    # comment with, several, commas explaining the pattern
    agent_id: Annotated[str, Form(pattern=r\"^[a-z0-9]+(-[a-z0-9]+)*$\", max_length=128)],
    scan_root: Annotated[str, Form()],
    subpath: Annotated[str, Form()] = \"\",
) -> HTMLResponse:
    pass
"""
    fcx = walk_file("/tmp/trigger.py", "python", src.encode())
    fn = _find(fcx.functions, "trigger_scan")
    assert fn is not None
    assert fn.param_count == 5, f"expected 5 params, got {fn.param_count}"


def test_typescript_nested_depth():
    results = _walk("typescript/nested.ts", "typescript")
    deep = _find(results, "deeplyNested")
    if deep is None:
        pytest.skip("typescript nested function not detected")
    assert deep.max_nesting >= 4


def test_go_nested_depth():
    results = _walk("go/nested.go", "go")
    deep = _find(results, "DeeplyNested")
    if deep is None:
        pytest.skip("go function not detected")
    assert deep.max_nesting >= 4


def test_pascal_nested_depth():
    results = _walk("pascal/nested.pas", "pascal")
    deep = _find(results, "DeeplyNested")
    shallow = _find(results, "Shallow")
    assert deep is not None
    assert shallow is not None
    assert deep.max_nesting >= 4, f"expected ≥4 nesting, got {deep.max_nesting}"
    assert shallow.max_nesting == 0
    assert deep.ccn > shallow.ccn
    assert shallow.param_count == 1


def test_pascal_complex_method_ccn():
    results = _walk("pascal/complex.pas", "pascal")
    many = _find(results, "ManyBranches")
    assert many is not None
    assert many.ccn >= 9, f"expected CCN ≥ 9, got {many.ccn}"
    assert many.param_count == 5


def test_pascal_foreach_counts_as_a_loop():
    # Regression: ``for x in collection do`` parses as a distinct `foreach`
    # node (not a `for` variant), which was absent from loop_kinds -- the
    # walker skipped it entirely, undercounting both CCN and nesting for any
    # for-in loop (common over TList/TStringList/generics in real Delphi
    # code).
    src = (
        b"unit U;\ninterface\nimplementation\n"
        b"procedure Bar(Lst: TList);\nvar X: Integer;\nbegin\n"
        b"  for X in Lst do\n"
        b"  begin\n"
        b"    if X > 0 then\n"
        b"    begin\n"
        b"      WriteLn(X);\n"
        b"    end;\n"
        b"  end;\n"
        b"end;\nend.\n"
    )
    fn = _find(walk_file("u.pas", "pascal", src).functions, "Bar")
    assert fn is not None
    assert fn.ccn == 3, f"expected CCN 3 (base + foreach + if), got {fn.ccn}"
    assert fn.max_nesting == 2, f"expected the if nested inside the foreach, got {fn.max_nesting}"


def test_pascal_else_if_chain_is_flat_not_nested():
    # Regression: found on a real ~40-arm `else if` VK-code dispatch chain
    # (StringToVK in a real Delphi codebase) — every arm nests the grammar's
    # `ifElse`/`if` one level inside the previous arm's `else` field, so an
    # unflattened walk reported nesting 33 / cognitive 583 for a flat lookup
    # table. Each arm still charges CCN (it's a real decision point) but
    # must not open a fresh nesting level or inflate cognitive complexity.
    src = (
        b"unit U;\ninterface\nimplementation\n"
        b"function F(S: string): Integer;\nbegin\n"
        b"  if S = 'a' then Result := 1\n"
        b"  else if S = 'b' then Result := 2\n"
        b"  else if S = 'c' then Result := 3\n"
        b"  else if S = 'd' then Result := 4\n"
        b"  else if S = 'e' then Result := 5\n"
        b"  else Result := 0;\n"
        b"end;\nend.\n"
    )
    fn = _find(walk_file("u.pas", "pascal", src).functions, "F")
    assert fn is not None
    assert fn.ccn == 6, f"expected CCN 6 (5 arms + base), got {fn.ccn}"
    assert fn.max_nesting == 1, f"expected flat chain (nesting 1), got {fn.max_nesting}"


def test_pascal_no_class_metrics():
    # Pascal method bodies live in a top-level ``defProc`` outside the
    # ``declClass`` node (see languages.py's ``_PASCAL`` comment) -- class
    # metrics are deliberately unmapped rather than emitting a misleading
    # zero-method class, same posture as Go. Calls walk_file() directly
    # (rather than through _walk_classes(), which skips on ``not
    # fcx.classes`` -- exactly what this test asserts), so it needs its own
    # best-effort guard rather than inheriting one from a helper.
    pytest.importorskip("tree_sitter_pascal", reason="run `uv sync --all-packages`")
    fcx = walk_file(
        "unsupported.pas",
        "pascal",
        (
            b"unit U;\n interface\n type\n TFoo = class\n"
            b" public\n   procedure Bar;\n end;\n"
            b" implementation\n"
            b" procedure TFoo.Bar;\n begin\n end;\n"
            b" end.\n"
        ),
    )
    assert fcx.classes == []
    assert _find(fcx.functions, "Bar") is not None


def test_javascript_nested_depth():
    results = _walk("javascript/nested.js", "javascript")
    deep = _find(results, "deeplyNested")
    if deep is None:
        pytest.skip("js function not detected")
    assert deep.max_nesting >= 4


def test_javascript_module_level_arrow_callback_is_function_entry():
    _require_language("javascript")
    source = b"""
router.get("/users", async (req, res) => {
  if (req.query.active && req.user) {
    return res.json(await loadUsers());
  }
  return res.status(400).end();
});

function wrapper(items) {
  return items.map((item) => item.id);
}
"""
    fcx = walk_file("/tmp/routes.js", "javascript", source)
    names = [fn.name for fn in fcx.functions]
    assert "router.get callback" in names
    assert "wrapper" in names
    assert "item" not in names

    route = _find(fcx.functions, "router.get callback")
    assert route is not None
    assert route.ccn >= 3
    assert route.max_nesting >= 1


def test_javascript_test_suite_wrapper_callback_is_not_function_entry():
    _require_language("javascript")
    source = b"""
test.describe("routes", () => {
  beforeEach(() => resetDb());

  it("handles active users", async () => {
    if (ready()) {
      await request(app).get("/users");
    }
  });
});
"""
    fcx = walk_file("/tmp/routes.test.js", "javascript", source)
    names = [fn.name for fn in fcx.functions]
    assert "test.describe callback" not in names
    assert "it callback" in names
    assert "beforeEach callback" in names


def test_a_chained_callee_names_its_callback_by_identifier_path():
    """A zod schema's ``superRefine`` callback was named after the whole
    ``z.object({...})`` expression, a name thousands of characters long."""
    _require_language("typescript")
    fields = ", ".join(f"field{i}: z.string().optional()" for i in range(40))
    source = f"""
export const Schema = z
  .object({{ {fields} }})
  .strict()
  .superRefine((value, ctx) => {{
    if (value.field1 && !value.field2) {{
      ctx.addIssue({{ code: "custom", message: "field2" }});
    }}
  }});

it.each([{{ name: "a", x: 1 }}, {{ name: "b", x: 2 }}])("case $name", ({{ x }}) => {{
  expect(x).toBeGreaterThan(0);
}});

makeHandler<Entry[]>(config)((entry) => {{
  return entry.id;
}});
""".encode()
    names = [fn.name for fn in walk_file("/tmp/schema.ts", "typescript", source).functions]
    assert "z.object.strict.superRefine callback" in names
    assert "it.each callback" in names
    assert "makeHandler callback" in names
    assert all(len(name) <= 120 and "(" not in name for name in names)


def test_rust_flat_match_complexity():
    """A flat match (all arms are simple expressions) should count as 1 CCN
    point for the match itself; individual arms should NOT add CCN."""
    results = _walk("rust/flat_match.rs", "rust")
    flat = _find(results, "flat_match")
    if flat is None:
        pytest.skip("rust function not detected")
    # flat_match: base CCN 1 + match 1 = 2
    assert flat.ccn == 2, f"flat match CCN expected 2, got {flat.ccn}"

    cplx = _find(results, "complex_match")
    if cplx is None:
        pytest.skip("rust complex_match not detected")
    # complex_match: match has an arm with nested `if`, so arms count
    # individually. CCN = 1 (base) + 3 arms + 1 (if in arm) = 5
    assert cplx.ccn > flat.ccn, (
        f"complex match CCN ({cplx.ccn}) should exceed flat match CCN ({flat.ccn})"
    )

    multi = _find(results, "multi_stmt_match")
    if multi is None:
        pytest.skip("rust multi_stmt_match not detected")
    # multi_stmt_match: arm with multi-statement block → complex match
    # CCN = 1 (base) + 3 arms = 4
    assert multi.ccn > flat.ccn, (
        f"multi-stmt match CCN ({multi.ccn}) should exceed flat match CCN ({flat.ccn})"
    )


def test_python_match_arms_count_toward_ccn():
    """Python match/case is pattern matching — each case is a real branch.

    Unlike Rust ``match`` (a flat dispatch that counts once), a Python
    ``match`` with N ``case`` arms is structurally an ``if``/``elif`` chain
    and each arm must count toward CCN. Regression for #1774: the flat-match
    suppression was over-applied to Python, so a 4-arm match reported 2
    (base 1 + 1 for the match, all arms dropped) instead of counting each arm.
    """
    results = _walk("python/match.py", "python")
    match_fn = _find(results, "python_match")
    assert match_fn is not None, "python_match not detected"
    # base 1 + 4 case arms (1, 2, 3, wildcard _) = 5.
    assert match_fn.ccn == 5, f"python match CCN expected 5, got {match_fn.ccn}"
    # The arms are genuinely counted, not flattened away.
    assert match_fn.ccn >= 4, f"match arms must count toward CCN, got {match_fn.ccn}"


def test_unsupported_language_returns_empty():
    results = walk_file_complexity("/tmp/x.unknown", "klingon", b"")
    assert results == []


# ---- class-level metrics (LCOM4) -----------------------------------------


def test_python_class_cohesion():
    classes = _walk_classes("python/classes.py", "python")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    # All methods collaborate around shared state → single component.
    assert cohesive.lcom4 == 1
    # Two disjoint field clusters; the loner touches no field, so it is a
    # component but not a group to extract.
    assert splintered.lcom4 == 2
    assert len(splintered.components) == 3
    assert splintered.method_count == 5
    assert splintered.field_count == 2


def _formatter_overload_class(overload_decorator: str) -> bytes:
    return (
        f"{overload_decorator}\n\n"
        "class Formatter:\n"
        "    def __init__(self):\n"
        "        self.value = 0\n"
        "        self.other = 1\n\n"
        "    @overload\n"
        "    def format(self, x: int) -> str: ...\n"
        "    @overload\n"
        "    def format(self, x: str) -> str: ...\n"
        "    @overload\n"
        "    def format(self, x: float) -> str: ...\n"
        "    def format(self, x):\n"
        "        return str(self.value) + str(x)\n\n"
        "    def reset(self):\n"
        "        self.other = 0\n"
    ).encode()


def _formatter_class_metrics(source: bytes):
    _require_language("python")
    classes = walk_file("formatter.py", "python", source).classes
    if not classes:
        pytest.skip("tree-sitter language pack missing for python")
    fmt = next((c for c in classes if c.name == "Formatter"), None)
    assert fmt is not None
    return fmt


def test_python_class_cohesion_ignores_overload_stubs():
    fmt = _formatter_class_metrics(_formatter_overload_class("from typing import overload"))
    assert fmt.method_count == 3
    assert fmt.lcom4 == 1
    assert len(fmt.methods) == 3


def test_python_class_cohesion_ignores_typing_overload_decorator():
    src = (
        _formatter_overload_class("import typing")
        .decode()
        .replace("@overload\n", "@typing.overload\n")
    )
    fmt = _formatter_class_metrics(src.encode())
    assert fmt.method_count == 3
    assert fmt.lcom4 == 1


def test_python_class_cohesion_ignores_aliased_overload_decorator():
    src = (
        _formatter_overload_class("import typing as t")
        .decode()
        .replace("@overload\n", "@t.overload\n")
    )
    fmt = _formatter_class_metrics(src.encode())
    assert fmt.method_count == 3
    assert fmt.lcom4 == 1


def test_typescript_class_cohesion():
    classes = _walk_classes("typescript/classes.ts", "typescript")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    if cohesive is None or splintered is None:
        pytest.skip("typescript classes not detected")
    assert cohesive.lcom4 == 1
    assert splintered.lcom4 == 2


def test_class_metrics_carry_methods_and_size():
    classes = _walk_classes("python/classes.py", "python")
    cohesive = classes["Cohesive"]
    # methods are the same FunctionComplexity objects the function pass found
    assert len(cohesive.methods) == cohesive.method_count
    assert cohesive.total_nloc > 0
    assert cohesive.max_method_ccn >= 1


def test_unsupported_language_has_no_classes():
    fcx = walk_file("/tmp/x.unknown", "klingon", b"")
    assert fcx.classes == []
    assert fcx.functions == []


# ---- assertion-block detection (test-quality) ----------------------------


def test_python_assertion_blocks():
    results = _walk("python/assertions.py", "python")
    many = _find(results, "test_many_bare_asserts")
    assert many is not None
    # One uninterrupted run of 16 bare asserts.
    assert len(many.assertion_blocks) == 1
    assert many.assertion_blocks[0][2] == 16

    calls = _find(results, "test_unittest_calls")
    assert calls is not None
    # self.assertEqual / assertTrue calls counted as assertions.
    assert calls.assertion_blocks[0][2] == 3


def test_python_assertion_runs_split_on_non_assert():
    results = _walk("python/assertions.py", "python")
    split = _find(results, "test_split_runs")
    assert split is not None
    # A non-assert statement between the asserts breaks the run into two.
    assert [b[2] for b in split.assertion_blocks] == [2, 2]


def test_python_single_assert_is_not_a_block():
    results = _walk("python/assertions.py", "python")
    few = _find(results, "test_few_asserts")
    assert few is not None
    # Two asserts separated by an assignment → no run of ≥2.
    assert few.assertion_blocks == []


def test_typescript_expect_blocks():
    results = _walk("typescript/assertions.ts", "typescript")
    many = _find(results, "testManyExpects")
    if many is None:
        pytest.skip("typescript function not detected")
    assert many.assertion_blocks[0][2] == 16
    few = _find(results, "testFewExpects")
    assert few is not None
    assert few.assertion_blocks == []


# ---- full-tier language coverage: Kotlin / C++ / C# ----------------------
#
# Each new full-tier language is validated end-to-end against the walker:
# control-flow (nesting + CCN), class-level cohesion (LCOM4), and
# assertion-block detection — mirroring the Python/TypeScript fixtures.


def test_kotlin_nesting_and_ccn():
    results = _walk("kotlin/nested.kt", "kotlin")
    deep = _find(results, "deeplyNested")
    assert deep is not None
    assert deep.max_nesting >= 4, f"expected ≥4 nesting, got {deep.max_nesting}"
    many = _find(results, "manyBranches")
    assert many is not None
    # 6 ifs + 2 boolean operators over the base path.
    assert many.ccn >= 9, f"expected CCN ≥ 9, got {many.ccn}"
    shallow = _find(results, "shallow")
    assert shallow is not None and shallow.max_nesting == 0


def test_kotlin_class_cohesion():
    classes = _walk_classes("kotlin/classes.kt", "kotlin")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    assert cohesive.lcom4 == 1
    # Each cluster is an accessor pair over one field and the loner touches
    # none: no component spans two fields, so there is no cohesion signal.
    assert splintered.lcom4 == 1
    assert splintered.method_count == 5
    assert splintered.field_count == 2


def test_kotlin_assertion_blocks():
    results = _walk("kotlin/assertions.kt", "kotlin")
    many = _find(results, "testManyAsserts")
    assert many is not None
    assert many.assertion_blocks[0][2] == 16
    few = _find(results, "testFewAsserts")
    assert few is not None and few.assertion_blocks == []


def test_swift_nesting_and_ccn():
    results = _walk("swift/nested.swift", "swift")
    deep = _find(results, "deeplyNested")
    assert deep is not None
    assert deep.max_nesting >= 4, f"expected ≥4 nesting, got {deep.max_nesting}"
    many = _find(results, "manyBranches")
    assert many is not None
    # 6 ifs + 2 boolean operators over the base path.
    assert many.ccn >= 9, f"expected CCN ≥ 9, got {many.ccn}"
    shallow = _find(results, "shallow")
    assert shallow is not None and shallow.max_nesting == 0


def test_swift_scores_the_kotlin_fixture_s_twin_the_same():
    """``swift/nested.swift`` is ``kotlin/nested.kt`` line for line: the two
    maps must agree on every number the control flow gives."""
    swift = {r.name: (r.ccn, r.max_nesting, r.cognitive) for r in _walk("swift/nested.swift", "swift")}
    kotlin = {r.name: (r.ccn, r.max_nesting, r.cognitive) for r in _walk("kotlin/nested.kt", "kotlin")}
    assert swift == kotlin
    assert swift["deeplyNested"] == (5, 4, 10)


def test_swift_class_cohesion():
    classes = _walk_classes("swift/classes.swift", "swift")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    assert (cohesive.lcom4, cohesive.method_count, cohesive.field_count) == (1, 4, 2)
    # A struct is a class kind too. Each cluster is an accessor pair over one
    # field and the loner touches none, as in the Kotlin fixture.
    assert (splintered.lcom4, splintered.method_count, splintered.field_count) == (1, 5, 2)
    # ``self.total`` is read as a use of the field: every pair of Cohesive's
    # methods shares one, and few of Splintered's do. Kotlin's twin agrees.
    kotlin = _walk_classes("kotlin/classes.kt", "kotlin")
    assert cohesive.tcc == kotlin["Cohesive"].tcc == 1.0
    assert splintered.tcc == kotlin["Splintered"].tcc < 0.5


def test_cpp_nesting_and_ccn():
    results = _walk("cpp/nested.cpp", "cpp")
    deep = _find(results, "deeplyNested")
    assert deep is not None
    assert deep.max_nesting >= 4, f"expected ≥4 nesting, got {deep.max_nesting}"
    many = _find(results, "manyBranches")
    assert many is not None
    assert many.ccn >= 9, f"expected CCN ≥ 9, got {many.ccn}"


def test_cpp_class_cohesion():
    classes = _walk_classes("cpp/classes.cpp", "cpp")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    assert cohesive.lcom4 == 1
    # Each cluster is an accessor pair over one field and the loner touches
    # none: no component spans two fields, so there is no cohesion signal.
    assert splintered.lcom4 == 1
    assert splintered.method_count == 5
    assert splintered.field_count == 2


_CPP_MACRO_SCOPES = b"""namespace absl {
ABSL_NAMESPACE_BEGIN
namespace base_internal {
int GetCpuType(int x) { if (x) { return 1; } return 0; }
}  // namespace base_internal
ABSL_NAMESPACE_END
}  // namespace absl
FMT_END_EXPORT
namespace detail {
int helper() { return 3; }
}
template <typename T>
class ABSL_ATTRIBUTE_WARN_UNUSED FixedArray {
  int size() const { return n_; }
  int n_;
};
EXPORT_MACRO class Queue {
  int pop() { return 0; }
};
struct foo *make_foo(void) { return 0; }
FMT_BEGIN_NAMESPACE
FMT_EXPORT template <typename Context> class Store {
  int put() { return 1; }
};
"""


def test_cpp_namespace_or_type_after_a_macro_is_not_a_function(tmp_path):
    # A macro line before ``namespace x {`` / ``class X {`` makes tree-sitter
    # read the scope as a function: it must be walked, never scored.
    p = tmp_path / "scopes.h"
    p.write_bytes(_CPP_MACRO_SCOPES)
    results = walk_file_complexity(str(p), "cpp", _CPP_MACRO_SCOPES)
    if not results:
        pytest.skip("tree-sitter language pack missing for cpp")
    names = {r.name for r in results}
    assert {"GetCpuType", "helper", "size", "put", "pop", "make_foo"} <= names
    assert not names & {"namespace", "base_internal", "FixedArray", "Store", "Queue"}
    assert _find(results, "GetCpuType").ccn == 2


@pytest.mark.parametrize("language", ["cpp", "c"])
def test_function_behind_a_macro_with_an_aggregate_return_type_stays_a_function(
    tmp_path, language
):
    # The stray ``struct S`` lands in the head as it does for a misread scope,
    # but the declarator is a real function_declarator.
    source = b"""int plain(void) { return 0; }
MYAPI struct S fn1(void) { return s; }
MYAPI enum E fn2(void) { return A; }
MYAPI union U fn4(void) { return u; }
EXPORT_API const struct S *fn5(int a) { if (a) { return 0; } return 0; }
"""
    if language == "cpp":
        source += b"MYAPI class C fn3() { return C(); }\n"
    p = tmp_path / ("api.h" if language == "cpp" else "api.c")
    p.write_bytes(source)
    results = walk_file_complexity(str(p), language, source)
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    expected = {"fn1", "fn2", "fn4", "fn5"} | ({"fn3"} if language == "cpp" else set())
    assert "plain" in {r.name for r in results}
    assert expected <= {r.name for r in results}


def test_cpp_export_macro_class_and_annotated_fields_are_measured(tmp_path):
    # ``class API Big {`` used to lose the class and its first method, and
    # ``int x_ GUARDED_BY(mu_);`` hid the field from cohesion.
    source = b"""class LIB_API Big {
 public:
  void a() { x_ = 1; }
  void b() { y_ = 2; }
 private:
  int x_ GUARDED_BY(mu_);
  int y_ GUARDED_BY(mu_);
};
"""
    _require_language("cpp")
    p = tmp_path / "big.h"
    p.write_bytes(source)
    classes = {c.name: c for c in walk_file(str(p), "cpp", source).classes}
    assert classes["Big"].method_count == 2
    assert classes["Big"].field_count == 2


def _names_by_line(tmp_path, language: str, source: bytes) -> dict[int, str]:
    p = tmp_path / ("unit.cpp" if language == "cpp" else "unit.c")
    p.write_bytes(source)
    results = walk_file_complexity(str(p), language, source)
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return {r.start_line: r.name for r in results}


@pytest.mark.parametrize(
    ("source", "name"),
    [
        (b"const T& A::f() const { return x; }", "A::f"),
        (b"T& f() { return x; }", "f"),
        (b"A& operator=(const A& o) { return *this; }", "operator="),
        (b"bool operator==(const A& o) const { return true; }", "operator=="),
        (b"struct A { int operator()(int x) { return x; } };", "operator()"),
        (b"struct A { operator bool() const { return true; } };", "operator bool"),
        (b"A::operator const char*() const { return 0; }", "A::operator const char*"),
        (b"struct A { ~A() {} };", "~A"),
        (b"A::~A() {}", "A::~A"),
        (b"template <typename T> int Foo<T>::g() { return 1; }", "Foo<T>::g"),
    ],
)
def test_cpp_declarator_names(tmp_path, source, name):
    assert list(_names_by_line(tmp_path, "cpp", source).values()) == [name]


@pytest.mark.parametrize(
    ("source", "name"),
    [
        (b"CLI11_INLINE bool\nApp::_parse_arg(int a) { return true; }", "App::_parse_arg"),
        (b"CLI11_INLINE std::string App::q() { return s; }", "App::q"),
        (b"API_ std::string Convert(const char* s) { return s; }", "Convert"),
        (b"API_ Cardinality AtMost(int n) { return n; }", "AtMost"),
        (b"API_ W W::Get() { return W(); }", "W::Get"),
        (b"API_ Reporter* GetReporter() { return 0; }", "GetReporter"),
    ],
)
def test_cpp_macro_prefixed_definition_is_named_by_its_qualified_identifier(
    tmp_path, source, name
):
    assert list(_names_by_line(tmp_path, "cpp", source).values()) == [name]


@pytest.mark.parametrize(
    ("prefix", "scope"),
    [
        ("bool ", ""),
        ("A<B<int>>::", "A<B<int>>::"),
        ("EXPORT std::vector<int> A<B<int>>::", "A<B<int>>::"),
        # An unmatched ``>`` ends the scope run there.
        ("x>::", "::"),
    ],
)
def test_scope_start_keeps_only_the_trailing_scope_run(prefix, scope):
    from repowise.core.analysis.health.complexity.ast_utils import _scope_start

    assert prefix[_scope_start(prefix) :] == scope


def test_c_function_returning_a_function_pointer_is_named(tmp_path):
    source = b"int (*fp(int a))(int) { return 0; }\n"
    assert list(_names_by_line(tmp_path, "c", source).values()) == ["fp"]


def test_cpp_defaulted_and_deleted_members_are_not_function_units(tmp_path):
    source = b"""struct A {
  A() = default;
  A(const A&) = delete;
  int get() { return 1; }
};
"""
    assert _names_by_line(tmp_path, "cpp", source) == {4: "get"}


def test_cpp_class_body_skips_a_nested_type_read_as_a_function(tmp_path):
    source = b"""class Outer {
 public:
  int get() { return x_; }
  EXPORT_MACRO class Inner { int a() { return 1; } int b() { return 2; } };
  int x_;
};
"""
    p = tmp_path / "outer.h"
    p.write_bytes(source)
    classes = {c.name: c for c in walk_file(str(p), "cpp", source).classes}
    if "Outer" not in classes:
        pytest.skip("tree-sitter language pack missing for cpp")
    assert classes["Outer"].method_count == 1


def test_cpp_class_fused_into_a_misread_head_is_not_a_cohesion_unit(tmp_path):
    # When the grammar loses the ``;`` after a class (a macro or recovery
    # upstream), ``class Fused {...} MACRO class Next {`` reads as one function
    # whose return type is ``Fused``; its extent and members are not its own.
    source = b"""class Fused {
  int push() { return 1; }
}
FMT_EXPORT class Next {
  int get() { return 2; }
};
class Plain { int get() { return x_; } int x_; };
"""
    p = tmp_path / "fused.h"
    p.write_bytes(source)
    fcx = walk_file(str(p), "cpp", source)
    if not fcx.functions:
        pytest.skip("tree-sitter language pack missing for cpp")
    names = {c.name for c in fcx.classes}
    assert "Plain" in names
    assert "Fused" not in names


def test_cpp_assertion_blocks():
    results = _walk("cpp/assertions.cpp", "cpp")
    many = _find(results, "testManyAsserts")
    assert many is not None
    assert many.assertion_blocks[0][2] == 16
    few = _find(results, "testFewAsserts")
    assert few is not None and few.assertion_blocks == []


def test_csharp_nesting_and_ccn():
    results = _walk("csharp/nested.cs", "csharp")
    deep = _find(results, "DeeplyNested")
    assert deep is not None
    assert deep.max_nesting >= 4, f"expected ≥4 nesting, got {deep.max_nesting}"
    many = _find(results, "ManyBranches")
    assert many is not None
    assert many.ccn >= 9, f"expected CCN ≥ 9, got {many.ccn}"


def test_csharp_class_cohesion():
    classes = _walk_classes("csharp/classes.cs", "csharp")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    assert cohesive.lcom4 == 1
    # Each cluster is an accessor pair over one field and the loner touches
    # none: no component spans two fields, so there is no cohesion signal.
    assert splintered.lcom4 == 1
    assert splintered.method_count == 5
    assert splintered.field_count == 2


def test_csharp_assertion_blocks():
    results = _walk("csharp/assertions.cs", "csharp")
    many = _find(results, "TestManyAsserts")
    assert many is not None
    assert many.assertion_blocks[0][2] == 16
    few = _find(results, "TestFewAsserts")
    assert few is not None and few.assertion_blocks == []


def test_dart_nesting_and_ccn():
    results = _walk("dart/nested.dart", "dart")
    deep = _find(results, "deeplyNested")
    assert deep is not None
    assert deep.max_nesting >= 4, f"expected >=4 nesting, got {deep.max_nesting}"
    many = _find(results, "manyBranches")
    assert many is not None
    # 6 ifs + 2 boolean operators over the base path.
    assert many.ccn >= 9, f"expected CCN >= 9, got {many.ccn}"
    shallow = _find(results, "shallow")
    assert shallow is not None and shallow.max_nesting == 0
    assert shallow.param_count == 1


def test_dart_class_facts():
    # Dart has no wrapper node for ``this.member`` access, so LCOM4 sits at
    # its "no signal" safety valve (1) for every class — the class facts
    # under test are method grouping and size.
    classes = _walk_classes("dart/classes.dart", "dart")
    cohesive = classes.get("Cohesive")
    wide = classes.get("Wide")
    assert cohesive is not None and wide is not None
    assert cohesive.method_count == 3
    assert wide.method_count == 5
    assert cohesive.lcom4 == 1
    assert wide.lcom4 == 1


def test_dart_assertion_blocks():
    results = _walk("dart/assertions.dart", "dart")
    many = _find(results, "testManyAsserts")
    assert many is not None
    assert many.assertion_blocks, "expected a run of assert statements"
    assert many.assertion_blocks[0][2] == 5
    few = _find(results, "testFewAsserts")
    assert few is not None and few.assertion_blocks == []


_DART_MEMBERS = """\
class A {
  int _x = 0;
  A() {}
  A.named(int v) {}
  factory A.make() { return A(); }
  int get x => _x;
  set x(int v) { _x = v; }
  int f() => 1;
  bool operator ==(Object o) => true;
  int operator [](int i) => i;
  A operator -() => this;
  A operator -(A o) => this;
}
"""


def test_dart_members_get_distinct_names():
    _require_language("dart")
    results = walk_file_complexity("a.dart", "dart", _DART_MEMBERS.encode())
    names = sorted(r.name for r in results)
    assert names == sorted(
        [
            "A", "A.named", "A.make", "get x", "set x", "f",
            "operator ==", "operator []", "operator unary-", "operator -",
        ]
    )


def test_dart_named_and_factory_constructors_are_constructors():
    results = _walk_source(_DART_MEMBERS, "dart", "a.dart")
    assert all(results[n].is_constructor for n in ("A", "A.named", "A.make"))
    assert not any(results[n].is_constructor for n in ("get x", "set x", "f"))


@pytest.mark.parametrize(
    "handler", ["on StateError { h(); }", "on StateError catch (e) { h(); }", "catch (e) { h(); }"]
)
def test_dart_every_handler_shape_adds_one_ccn(handler: str):
    source = f"void f() {{\n  try {{ g(); }} {handler}\n}}\n"
    fn = _walk_source(source, "dart", "f.dart")["f"]
    assert (fn.ccn, fn.cognitive) == (2, 1)


def test_dart_bare_on_handlers_count_each_arm():
    source = "void f() {\n  try { g(); } on A {} on p.B<int> {} on C catch (e) {} catch (e) {} finally {}\n}\n"
    assert _walk_source(source, "dart", "f.dart")["f"].ccn == 5


def test_scala_nesting_and_ccn():
    results = _walk("scala/nested.scala", "scala")
    deep = _find(results, "deeplyNested")
    assert deep is not None
    assert deep.max_nesting >= 4, f"expected >=4 nesting, got {deep.max_nesting}"
    many = _find(results, "manyBranches")
    assert many is not None
    # 6 ifs + 2 boolean operators over the base path.
    assert many.ccn >= 9, f"expected CCN >= 9, got {many.ccn}"
    shallow = _find(results, "shallow")
    assert shallow is not None and shallow.max_nesting == 0
    assert shallow.param_count == 1


def test_scala_match_and_guards():
    results = _walk("scala/nested.scala", "scala")
    guarded = _find(results, "matchGuards")
    assert guarded is not None
    # 4 cases + 2 guards (guards make the match non-flat, so arms count).
    assert guarded.ccn == 7, f"expected CCN 7, got {guarded.ccn}"
    flat = _find(results, "flatMatch")
    assert flat is not None
    # A flat match counts once for the dispatch, not per arm.
    assert flat.ccn == 2, f"expected CCN 2, got {flat.ccn}"
    for_guard = _find(results, "forGuard")
    assert for_guard is not None
    # for-comprehension (loop) + inline guard; the guard is flat (no nesting).
    assert for_guard.ccn == 3, f"expected CCN 3, got {for_guard.ccn}"
    assert for_guard.max_nesting == 1


def test_scala_class_cohesion():
    classes = _walk_classes("scala/classes.scala", "scala")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    assert cohesive.lcom4 == 1
    assert splintered.lcom4 == 2
    assert splintered.method_count == 5
    assert splintered.field_count == 2
    # ``object`` and ``trait`` bodies group methods like a class body.
    registry = classes.get("Registry")
    assert registry is not None and registry.method_count == 2
    shape = classes.get("Shape")
    assert shape is not None and shape.method_count == 1


def test_scala_assertion_blocks():
    results = _walk("scala/assertions.scala", "scala")
    many = _find(results, "testManyAsserts")
    assert many is not None
    assert many.assertion_blocks, "expected a run of assert calls"
    assert many.assertion_blocks[0][2] == 5
    few = _find(results, "testFewAsserts")
    assert few is not None and few.assertion_blocks == []


def test_ruby_nesting_and_ccn():
    results = _walk("ruby/nested.rb", "ruby")
    deep = _find(results, "deeply_nested")
    assert deep is not None
    # if > while > if > if — keyword tokens must not double-count.
    assert deep.ccn == 5, f"expected CCN 5, got {deep.ccn}"
    assert deep.max_nesting == 4, f"expected nesting 4, got {deep.max_nesting}"
    many = _find(results, "many_branches")
    assert many is not None
    # modifier-if + if + 2 elsif + && + || over the base path; elsif chains
    # and the one-line modifier stay flat.
    assert many.ccn == 7, f"expected CCN 7, got {many.ccn}"
    assert many.max_nesting == 1, f"expected nesting 1, got {many.max_nesting}"
    wordy = _find(results, "wordy")
    assert wordy is not None and wordy.ccn == 3  # ``and`` / ``or`` count too
    shallow = _find(results, "shallow")
    assert shallow is not None and shallow.max_nesting == 0
    assert shallow.param_count == 1
    mods = _find(results, "modifier_loops")
    assert mods is not None
    assert mods.ccn == 3  # until_modifier + while_modifier
    assert mods.max_nesting == 0  # one-line modifiers are flat


def test_ruby_blocks_are_not_closures():
    # ``.each do … end`` / ``.map { … }`` bodies roll up into the method:
    # no nesting, no extra entries — the block-heavy-code decision.
    results = _walk("ruby/nested.rb", "ruby")
    blocky = _find(results, "block_heavy")
    assert blocky is not None
    assert blocky.ccn == 1 and blocky.max_nesting == 0
    assert not any("anonymous" in r.name for r in results)


def test_ruby_case_and_rescue():
    results = _walk("ruby/nested.rb", "ruby")
    flat = _find(results, "flat_case")
    assert flat is not None
    # A flat case counts once for the dispatch, not per ``when`` arm.
    assert flat.ccn == 2, f"expected CCN 2, got {flat.ccn}"
    heavy = _find(results, "heavy_case")
    assert heavy is not None
    # 3 when arms + the nested if.
    assert heavy.ccn == 5, f"expected CCN 5, got {heavy.ccn}"
    pat = _find(results, "pattern_match")
    assert pat is not None
    assert pat.ccn == 2, f"expected CCN 2, got {pat.ccn}"  # flat case/in
    risky = _find(results, "risky_io")
    assert risky is not None
    assert risky.ccn == 3, f"expected CCN 3, got {risky.ccn}"  # 2 rescue arms
    implicit = _find(results, "implicit_rescue")
    assert implicit is not None
    assert implicit.ccn == 2  # method-level rescue counts like a catch


def test_ruby_class_metrics_and_lcom4_no_signal():
    classes = _walk_classes("ruby/classes.rb", "ruby")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    assert splintered.method_count == 5
    assert splintered.max_method_ccn == 2
    # LCOM4 sits at the "no signal" valve for Ruby (receiver-less @ivar
    # idiom): even a splintered class must NOT read as low-cohesion.
    assert cohesive.lcom4 == 1
    assert splintered.lcom4 == 1
    util = classes.get("Util")
    assert util is not None and util.method_count == 2  # def + def self.


def test_ruby_assertion_blocks():
    results = _walk("ruby/assertions.rb", "ruby")
    many = _find(results, "test_many_asserts")
    assert many is not None
    assert many.assertion_blocks, "expected a run of assert calls"
    assert many.assertion_blocks[0][2] == 5
    few = _find(results, "test_few_asserts")
    assert few is not None and few.assertion_blocks == []


def test_php_nesting_and_ccn():
    results = _walk("php/nested.php", "php")
    deep = _find(results, "deeply_nested")
    assert deep is not None
    assert deep.ccn == 5, f"expected CCN 5, got {deep.ccn}"
    assert deep.max_nesting == 4, f"expected nesting 4, got {deep.max_nesting}"
    many = _find(results, "many_branches")
    assert many is not None
    # if + if + && + elseif + || + else if + base path
    assert many.ccn == 7, f"expected CCN 7, got {many.ccn}"
    assert many.max_nesting == 1, f"expected nesting 1, got {many.max_nesting}"
    wordy = _find(results, "wordy")
    assert wordy is not None and wordy.ccn == 3  # and + or
    shallow = _find(results, "shallow")
    assert shallow is not None and shallow.max_nesting == 0
    assert shallow.param_count == 1
    loops = _find(results, "loops_galore")
    assert loops is not None
    assert loops.ccn == 4  # for + foreach + do-while + 1
    assert loops.max_nesting == 1
    tc = _find(results, "try_catch_finally")
    assert tc is not None
    assert tc.ccn == 4  # if + 2 catch clauses + 1


def test_php_lambdas_and_closures():
    results = _walk("php/nested.php", "php")
    fn = _find(results, "lambda_and_arrow")
    assert fn is not None
    # anonymous_function branch rolls into lambda_and_arrow
    assert fn.ccn >= 3


def test_php_classes_lcom4():
    classes = _walk_classes("php/classes.php", "php")
    cohesive = classes.get("CohesiveClass")
    split = classes.get("SplitClass")
    assert cohesive is not None and split is not None
    assert cohesive.method_count == 4
    assert split.method_count == 4
    assert cohesive.lcom4 == 1
    assert split.lcom4 >= 2
    trait = classes.get("LoggableTrait")
    assert trait is not None
    assert trait.method_count == 2


def test_php_assertion_blocks():
    results = _walk("php/assertions.php", "php")
    many = _find(results, "testManyAsserts")
    assert many is not None
    assert many.assertion_blocks, "expected a run of assert calls"
    assert many.assertion_blocks[0][2] == 5
    few = _find(results, "testFewAsserts")
    assert few is not None and few.assertion_blocks == []


def _walk_source(source: str, language: str, filename: str):
    _require_language(language)
    results = walk_file_complexity(filename, language, source.encode())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return {r.name: r for r in results}


# A try neither branches nor nests: each catch opens one level for its own
# body, and the guarded and finally bodies stay at the try's depth.
@pytest.mark.parametrize(
    ("language", "filename", "source", "ccn", "nesting", "cognitive"),
    [
        (
            "python",
            "f.py",
            "def f(a):\n try:\n  g()\n except ValueError:\n  if a:\n   return 2\n return 3\n",
            3,
            2,
            3,
        ),
        (
            "python",
            "f.py",
            "def f(a):\n try:\n  if a:\n   g()\n finally:\n  if a:\n   h()\n",
            3,
            1,
            2,
        ),
        (
            "python",
            "f.py",
            "def f():\n try:\n  try:\n   g()\n  except KeyError:\n   h()\n except ValueError:\n  k()\n",
            3,
            1,
            2,
        ),
        (
            "java",
            "F.java",
            "class F {\n  int f(boolean a) {\n    try (Reader r = open()) {\n      g();\n"
            "    } catch (IOException e) {\n      if (a) { return 2; }\n    }\n    return 3;\n  }\n}\n",
            3,
            2,
            3,
        ),
        (
            "kotlin",
            "f.kt",
            "fun f(a: Boolean): Int {\n    val x = try {\n        g()\n    } catch (e: Exception) {\n"
            "        if (a) 2 else 3\n    }\n    return x\n}\n",
            3,
            2,
            3,
        ),
    ],
    ids=["try-except-if", "try-finally", "nested-try", "java-try-with-resources", "kotlin-try-expression"],
)
def test_try_does_not_nest(language, filename, source, ccn, nesting, cognitive):
    fn = _walk_source(source, language, filename)["f"]
    assert (fn.ccn, fn.max_nesting, fn.cognitive) == (ccn, nesting, cognitive)


def test_catch_still_nests_its_body():
    source = (
        "def f(a):\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        log()\n"
        "        log()\n"
        "        log()\n"
        "    return 3\n"
    )
    fn = _walk_source(source, "python", "f.py")["f"]
    assert fn.max_nesting == 1
    # The nesting locator names the except clause, not the try around it.
    assert fn.deepest_block == (4, 7)


def test_apex_nesting_and_ccn():
    results = _walk("apex/nested.cls", "apex")
    deep = _find(results, "deeplyNested")
    assert deep is not None
    # if > for > if > while > catch: a try opens no level of its own.
    assert deep.max_nesting == 5
    many = _find(results, "manyBranches")
    assert many is not None
    # if + else-if + if + ternary + && + || over the base path; ``??`` is no decision.
    assert many.ccn == 7
    # A ``switch on`` counts once for the dispatch, not per ``when``.
    dispatch = _find(results, "dispatch")
    assert dispatch is not None and dispatch.ccn == 2
    shallow = _find(results, "shallow")
    assert shallow is not None and shallow.max_nesting == 0 and shallow.param_count == 1


def test_apex_property_accessors_are_one_unit_named_for_the_property():
    results = _walk("apex/nested.cls", "apex")
    names = [r.name for r in results]
    # ``total { get { ... } set; }`` is one unit; ``label { get; set; }`` holds
    # no code and is none.
    assert names.count("total") == 1
    assert "label" not in names
    assert _find(results, "total").ccn == 2


def test_apex_trigger_is_a_unit():
    source = (
        "trigger AccountTrigger on Account (before insert) {\n"
        "    for (Account a : Trigger.new) {\n"
        "        if (a.Name == null) { a.addError('x'); }\n"
        "    }\n"
        "}\n"
    )
    fn = _walk_source(source, "apex", "AccountTrigger.trigger")["AccountTrigger"]
    assert (fn.ccn, fn.max_nesting) == (3, 2)


def test_apex_class_cohesion():
    classes = _walk_classes("apex/classes.cls", "apex")
    assert classes["Cohesive"].lcom4 == 1
    # Bare ``a`` and ``this.b`` both reach a member.
    assert classes["Splintered"].lcom4 == 2
    assert classes["Splintered"].method_count == 4
    # The accessor unit is a method; the auto-property is a field only.
    assert classes["WithProperties"].method_count == 2
    # Interfaces and enums nested in a class are not cohesion units.
    assert "Shape" not in classes and "Mode" not in classes


def test_apex_assertions_and_test_cases():
    results = _walk("apex/assertions.cls", "apex")
    many = _find(results, "testManyAsserts")
    assert many is not None and many.assertion_count == 5
    # ``Assert.areEqual`` / ``Assert.isTrue`` form a run; ``System.assertEquals``
    # counts as an assertion but, like any split ``x.assertY`` call, joins no run.
    assert many.assertion_blocks == [(9, 10, 2)]
    by_name = {r.name: r for r in results}
    # ``@IsTest`` in any case, and the legacy ``testMethod`` modifier.
    for case in ("testManyAsserts", "testFewAsserts", "legacyCase", "checksNothing"):
        assert by_name[case].is_test_case, case
    for helper in ("makeData", "compute"):
        assert not by_name[helper].is_test_case, helper



def test_java_switch_arm_comment_does_not_increase_ccn():
    _require_language("java")

    def get_ccn(arm: str) -> int:
        source = f"""
        class Example {{
            String f(int x) {{
                switch (x) {{
                    case 0 -> {{ return "a"; }}
                    case 1 -> {{ {arm} }}
                    default -> {{ return "z"; }}
                }}
                return "";
            }}
        }}
        """
        results = walk_file_complexity(
            "/tmp/example.java", "java", source.encode()
        )
        result = _find(results, "f")
        assert result is not None
        return result.ccn

    plain = get_ccn('return "b";')
    commented = get_ccn('// explanatory comment\nreturn "b";')
    multiple_statements = get_ccn(
        'String value = "b"; return value;'
    )

    assert commented == plain
    assert multiple_statements > plain


_BODYLESS_SOURCES = {
    "java": (
        "A.java",
        b"abstract class A {\n  A() { }\n  abstract void f(int x);\n  native void n();\n  void g() { int y = 1; }\n}\n"
        b"interface I {\n  void h();\n  default int d() { return 1; }\n}\n",
        {"A", "g", "d"},
        2,
    ),
    "csharp": (
        "a.cs",
        b"abstract class A {\n  public A() { }\n  public abstract void F(int x);\n  static extern int E(int a);\n"
        b"  partial void P();\n  public void G() { var y = 1; }\n  int M() => 1;\n}\n"
        b"interface I {\n  void H();\n  int D() { return 1; }\n}\n",
        {"A", "G", "M", "D"},
        3,
    ),
    "php": (
        "a.php",
        b"<?php\nabstract class A {\n  abstract public function f($x);\n  public function g() { return 1; }\n}\n"
        b"interface I {\n  public function h();\n}\n",
        {"g"},
        1,
    ),
    "kotlin": (
        "a.kt",
        b"abstract class A {\n  abstract fun f(x: Int)\n  fun g(): Int { return 1 }\n  fun e() = 2\n}\n"
        b"interface I {\n  fun h()\n  fun d(): Int { return 1 }\n}\n",
        {"g", "e", "d"},
        2,
    ),
    "apex": (
        "A.cls",
        b"public abstract class A {\n  public abstract void f(Integer x);\n  public void g() { Integer y = 1; }\n}\n"
        b"public interface I {\n  void h();\n}\n",
        {"g"},
        1,
    ),
}


@pytest.mark.parametrize("language", sorted(_BODYLESS_SOURCES))
def test_bodyless_methods_are_not_units(language):
    """Abstract, interface and extern declarations have no code: no function
    entry and no class method. Members with a body stay units."""
    _require_language(language)
    path, source, units, a_methods = _BODYLESS_SOURCES[language]
    fcx = walk_file(path, language, source)
    assert {f.name for f in fcx.functions} == units
    classes = {c.name: c for c in fcx.classes}
    assert classes["A"].method_count == a_methods


# ---------------------------------------------------------------------------
# A function whose body is one expression scores like its block twin (#4792)
# ---------------------------------------------------------------------------


def _fns(language: str, path: str, source: bytes) -> dict:
    """The functions of *source* by name; skips when the grammar is missing."""
    _require_language(language)
    fcx = walk_file(path, language, source)
    if not fcx.functions:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return {f.name: f for f in fcx.functions}


def _shape(fn):
    """What a function's complexity reads as, ignoring its name and position."""
    return (
        fn.ccn,
        fn.max_nesting,
        fn.cognitive,
        fn.bumps,
        [(c.operator_count, c.enclosing_construct) for c in fn.complex_conditions],
    )


def test_typescript_arrow_ternary_body_scores_like_its_block_twin():
    fns = _fns(
        "typescript",
        "a.ts",
        b"export const f = (a, b) => a && b ? 1 : 2;\n"
        b"function g(a, b) { return a && b ? 1 : 2; }\n",
    )
    assert fns["f"].ccn == 3
    assert [(c.operator_count, c.enclosing_construct) for c in fns["f"].complex_conditions] == [
        (1, "ternary")
    ]
    assert _shape(fns["f"]) == _shape(fns["g"])


def test_typescript_arrow_boolean_body_counts_its_operator():
    fns = _fns(
        "typescript",
        "a.ts",
        b"export const f = (a, b) => a && b;\nfunction g(a, b) { return a && b; }\n",
    )
    assert fns["f"].ccn == 2
    assert _shape(fns["f"]) == _shape(fns["g"])


def test_javascript_arrow_nested_ternary_body_nests():
    fns = _fns(
        "javascript",
        "a.js",
        b"const f = (a, b) => a ? (b ? 1 : 2) : 3;\n"
        b"const g = (a, b) => { return a ? (b ? 1 : 2) : 3; };\n",
    )
    assert fns["f"].ccn == 3
    assert _shape(fns["f"]) == _shape(fns["g"])


def test_javascript_parenthesized_arrow_body_is_unchanged():
    """A wrapper node already holds the expression, so it was counted."""
    fns = _fns(
        "javascript",
        "a.js",
        b"const f = (a) => (a ? 1 : 2);\nconst g = (a) => { return a ? 1 : 2; };\n",
    )
    assert fns["f"].ccn == 2
    assert _shape(fns["f"]) == _shape(fns["g"])


def test_scala_if_body_is_a_decision_point():
    fns = _fns(
        "scala",
        "a.scala",
        b"object O {\n"
        b"  def f(a: Boolean): Int = if (a) 1 else 2\n"
        b"  def g(a: Boolean): Int = { if (a) 1 else 2 }\n"
        b"}\n",
    )
    assert fns["f"].ccn == 2
    assert _shape(fns["f"]) == _shape(fns["g"])


def test_scala_compound_if_body_reaches_complex_conditional():
    fns = _fns(
        "scala",
        "a.scala",
        b"object O {\n"
        b"  def f(a: Boolean, b: Boolean, c: Boolean): Int = if (a && b && c) 1 else 2\n"
        b"  def g(a: Boolean, b: Boolean, c: Boolean): Int = { if (a && b && c) 1 else 2 }\n"
        b"}\n",
    )
    assert fns["f"].ccn == 4
    assert [c.operator_count for c in fns["f"].complex_conditions] == [2]
    assert _shape(fns["f"]) == _shape(fns["g"])


def test_scala_match_body_scores_like_its_block_twin():
    fns = _fns(
        "scala",
        "a.scala",
        b"object O {\n"
        b'  def f(x: Int): String = x match { case 1 => "a"; case 2 => "b"; case _ => "c" }\n'
        b'  def g(x: Int): String = { x match { case 1 => "a"; case 2 => "b"; case _ => "c" } }\n'
        b"}\n",
    )
    assert _shape(fns["f"]) == _shape(fns["g"])


def test_python_lambda_ternary_body_scores_like_its_def_twin():
    fns = _fns(
        "python",
        "a.py",
        b"g = lambda a, b: 1 if a and b else 2\n"
        b"def h(a, b):\n    return 1 if a and b else 2\n",
    )
    lam = next(f for n, f in fns.items() if n.startswith("<anonymous"))
    assert lam.ccn == 3
    assert _shape(lam) == _shape(fns["h"])


def test_python_lambda_without_a_decision_stays_ccn_1():
    fns = _fns("python", "a.py", b"f = lambda x: x + 1\n")
    assert next(iter(fns.values())).ccn == 1


@pytest.mark.parametrize(
    ("language", "path", "source"),
    [
        (
            "csharp",
            "a.cs",
            b"class C {\n  int F(bool a, bool b) => a && b ? 1 : 2;\n"
            b"  int G(bool a, bool b) { return a && b ? 1 : 2; }\n}\n",
        ),
        (
            "kotlin",
            "a.kt",
            b"fun f(a: Boolean, b: Boolean): Int = if (a && b) 1 else 2\n"
            b"fun g(a: Boolean, b: Boolean): Int { return if (a && b) 1 else 2 }\n",
        ),
    ],
)
def test_wrapper_node_expression_bodies_are_unchanged(language, path, source):
    """C# ``=>`` members and Kotlin ``= expr`` functions already scored right:
    a wrapper node holds the expression, so they match their block twin."""
    fns = _fns(language, path, source)
    key_f, key_g = ("F", "G") if language == "csharp" else ("f", "g")
    assert fns[key_f].ccn == 3
    assert _shape(fns[key_f]) == _shape(fns[key_g])


def test_walk_accepts_a_stand_in_body_without_a_node_type():
    """The dispatch pass walks a bare ``children`` holder, not a tree node."""
    from repowise.core.analysis.health.complexity.cyclomatic import (
        _is_decision_body,
        _walk_function_body,
    )
    from repowise.core.analysis.health.complexity.dispatch import _Siblings
    from repowise.core.analysis.health.complexity.languages import get_language_map

    lmap = get_language_map("python")
    assert lmap is not None
    stand_in = _Siblings([])
    assert _is_decision_body(stand_in, lmap) is False  # type: ignore[arg-type]
    assert _walk_function_body(stand_in, lmap)[0] == 1  # type: ignore[arg-type]
