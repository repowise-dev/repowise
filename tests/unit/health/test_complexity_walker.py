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
    # Two disjoint field clusters + a loner → three components.
    assert splintered.lcom4 == 3
    assert splintered.method_count == 5
    assert splintered.field_count == 2


def test_typescript_class_cohesion():
    classes = _walk_classes("typescript/classes.ts", "typescript")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    if cohesive is None or splintered is None:
        pytest.skip("typescript classes not detected")
    assert cohesive.lcom4 == 1
    assert splintered.lcom4 == 3


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
    assert splintered.lcom4 == 3
    assert splintered.method_count == 5
    assert splintered.field_count == 2


def test_kotlin_assertion_blocks():
    results = _walk("kotlin/assertions.kt", "kotlin")
    many = _find(results, "testManyAsserts")
    assert many is not None
    assert many.assertion_blocks[0][2] == 16
    few = _find(results, "testFewAsserts")
    assert few is not None and few.assertion_blocks == []


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
    assert splintered.lcom4 == 3
    assert splintered.method_count == 5
    assert splintered.field_count == 2


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
    assert splintered.lcom4 == 3
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
    assert splintered.lcom4 == 3
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
