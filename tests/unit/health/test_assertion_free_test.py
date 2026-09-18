"""``assertion_free_test``: a test case that checks nothing.

Real source is driven through the walker rather than hand-built
``FunctionComplexity`` rows, as the sibling test-quality suites do: the failures
worth catching here are grammar-shaped, and a hand-built row would assert only
that the biomarker compares two integers.

The load-bearing case is the Java one. A mock verification must count as an
oracle here and must NOT count as one for ``mock_saturated_test``, so the two
suites pin the same call to opposite verdicts on purpose.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.assertion_free_test import BIOMARKER
from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.complexity import walk_file


def _require(language: str) -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


def _detect(source: str, path: str = "tests/test_sample.py", language: str = "python"):
    fc = walk_file(path, language, source.encode("utf-8"))
    ctx = FileContext(
        file_path=path,
        language=language,
        nloc=fc.file_nloc,
        has_test_file=True,
        module=None,
        function_metrics={f.name: f for f in fc.functions},
        all_functions=tuple(fc.functions),
    )
    return BIOMARKER.detect(ctx)


def _flagged(source: str, path: str = "tests/test_sample.py", language: str = "python"):
    return sorted(f.function_name for f in _detect(source, path, language))


def _counts(source: str, path: str, language: str) -> dict[str, tuple[int, int, bool]]:
    """``{function: (assertion_count, verification_count, is_test_case)}``."""
    fc = walk_file(path, language, source.encode("utf-8"))
    return {f.name: (f.assertion_count, f.verification_count, f.is_test_case) for f in fc.functions}


# --------------------------------------------------------------------------
# Which functions are test cases at all
# --------------------------------------------------------------------------


def test_python_helpers_and_fixtures_are_not_test_cases() -> None:
    _require("python")
    counts = _counts(
        "def test_bare():\n"
        "    do_thing()\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
        "\n"
        "class TestThing:\n"
        "    def setUp(self):\n"
        "        self.x = 1\n"
        "\n"
        "    def test_method(self):\n"
        "        self.assertEqual(1, 1)\n",
        "tests/test_sample.py",
        "python",
    )
    assert counts["test_bare"][2] is True
    assert counts["test_method"][2] is True
    assert counts["helper"][2] is False
    assert counts["setUp"][2] is False


def test_go_counts_only_test_functions() -> None:
    _require("go")
    counts = _counts(
        "package p\n"
        "\n"
        "func TestBare(t *testing.T) { doThing() }\n"
        "func Test_table_driven(t *testing.T) { doThing() }\n"
        "func BenchmarkX(b *testing.B) { doThing() }\n"
        "func ExampleY() { fmt.Println(1) }\n"
        "func testScanner() Scanner { return nil }\n"
        "func TestingHelper() int { return 1 }\n"
        "func helper(t *testing.T) int { return 1 }\n",
        "pkg/thing_test.go",
        "go",
    )
    # A benchmark has nothing to assert and an Example's oracle is its output
    # comment, so neither is a case this marker can judge.
    assert counts["TestBare"][2] is True
    assert counts["Test_table_driven"][2] is True
    assert counts["BenchmarkX"][2] is False
    assert counts["ExampleY"][2] is False
    assert counts["helper"][2] is False
    # ``go test`` runs ``TestXxx`` only when Xxx does not start lowercase, so
    # these two are helpers the runner never collects. Matching them
    # case-insensitively was measurably the largest Go false positive.
    assert counts["testScanner"][2] is False
    assert counts["TestingHelper"][2] is False


def test_js_suite_and_setup_callbacks_are_not_test_cases() -> None:
    _require("typescript")
    source = (
        'describe("S", () => {\n'
        "  beforeEach(() => { jest.resetAllMocks(); });\n"
        '  it("checks", () => { expect(s.send).toHaveBeenCalled(); });\n'
        '  it("bare", async () => { await s.send(m); });\n'
        '  it.each([1, 2])("param %i", (n) => { expect(n).toBe(n); });\n'
        '  test("bare too", () => { doThing(); });\n'
        "  function localHelper() { return 1; }\n"
        "});\n"
    )
    fc = walk_file("src/x.test.ts", "typescript", source.encode("utf-8"))
    cases = {f.name for f in fc.functions if f.is_test_case}
    assert "beforeEach callback" not in cases
    assert "localHelper" not in cases
    assert "it callback" in cases
    assert "test callback" in cases
    # ``it.each([1,2])`` carries its arguments in the entry name; the first
    # dotted segment is still the framework function.
    assert "it.each([1, 2]) callback" in cases


def test_a_language_with_no_row_classifies_nothing() -> None:
    _require("ruby")
    source = "def test_bare\n  do_thing\nend\n"
    fc = walk_file("spec/thing_spec.rb", "ruby", source.encode("utf-8"))
    assert [f.is_test_case for f in fc.functions] == [False]
    assert _flagged(source, "spec/thing_spec.rb", "ruby") == []


def test_is_test_case_does_not_read_the_path() -> None:
    _require("python")
    # ``HealthWalkCache`` keys on language and bytes with no path component, so
    # a path-derived field would be served from a byte-identical file elsewhere.
    body = b"def test_bare():\n    do()\n"
    in_tests = walk_file("tests/test_a.py", "python", body)
    in_src = walk_file("src/prod.py", "python", body)
    assert [f.is_test_case for f in in_tests.functions] == [True]
    assert [f.is_test_case for f in in_src.functions] == [True]


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


def test_fires_on_a_test_that_asserts_nothing() -> None:
    _require("python")
    findings = _detect("def test_bare():\n    service.do_it()\n")
    assert [f.biomarker_type for f in findings] == ["assertion_free_test"]
    assert findings[0].function_name == "test_bare"
    assert findings[0].details["function"] == "test_bare"


def test_silent_when_the_test_asserts() -> None:
    _require("python")
    assert _flagged("def test_ok():\n    assert 1 == 1\n") == []


def test_silent_on_a_production_file() -> None:
    _require("python")
    assert _flagged("def test_bare():\n    do_thing()\n", "src/pkg/module.py", "python") == []


def test_silent_on_a_helper_with_no_assertions() -> None:
    _require("python")
    assert _flagged("def build_user():\n    return User()\n") == []


def test_context_manager_oracles_count() -> None:
    _require("python")
    # The call sits in the ``with`` header rather than in a statement of its
    # own, which hid it from every tier. It is the dominant Python false
    # positive this marker would otherwise report.
    source = (
        "import pytest\n"
        "\n"
        "def test_pytest_raises():\n"
        "    with pytest.raises(ValueError):\n"
        "        boom()\n"
        "\n"
        "def test_unittest_raises(self):\n"
        "    with self.assertRaises(ValueError):\n"
        "        boom()\n"
    )
    assert _flagged(source) == []


def test_a_with_block_never_joins_an_assertion_run() -> None:
    _require("python")
    # Runs are what the calibrated block markers read, so a ``with`` header is
    # capped at the broad tier and must break a run exactly as it always has.
    source = (
        "import pytest\n"
        "\n"
        "def test_runs():\n"
        "    assert a == 1\n"
        "    with pytest.raises(ValueError):\n"
        "        boom()\n"
        "    assert b == 2\n"
    )
    fc = walk_file("tests/test_sample.py", "python", source.encode("utf-8"))
    assert fc.functions[0].assertion_blocks == []


# --------------------------------------------------------------------------
# Verification is an oracle here and is not one next door
# --------------------------------------------------------------------------


def test_java_verify_only_test_is_not_assertion_free() -> None:
    _require("java")
    source = (
        "class FooTest {\n"
        "  @Test void verifyOnly() { verify(sender).send(m); }\n"
        "  @Test void bddVerifyOnly() { then(sender).should().send(m); }\n"
        "  @Test void noMoreInteractions() { verifyNoMoreInteractions(sender); }\n"
        "  @Test void bare() { service.doIt(); }\n"
        "}\n"
    )
    counts = _counts(source, "src/test/java/FooTest.java", "java")
    # Java is classified and counted but never reported (see the gate test
    # below); what must hold is that each verify-only test carries an oracle.
    assert counts["verifyOnly"][1] == 1
    assert counts["bddVerifyOnly"][1] == 1
    assert counts["noMoreInteractions"][1] == 1
    assert counts["bare"][:2] == (0, 0)


def test_java_verification_stays_out_of_the_assertion_count() -> None:
    _require("java")
    counts = _counts(
        "class FooTest {\n"
        "  @Test void verifyOnly() { verify(sender).send(m); }\n"
        "  @Test void assertOnly() { assertEquals(1, 2); }\n"
        "}\n",
        "src/test/java/FooTest.java",
        "java",
    )
    # The count ``mock_saturated_test`` divides by must not gain the
    # verification, or that marker goes blind on exactly the tests it targets.
    assert counts["verifyOnly"] == (0, 1, True)
    assert counts["assertOnly"] == (1, 0, True)


def test_java_annotation_forms_all_register() -> None:
    _require("java")
    source = (
        "class FooTest {\n"
        "  @Test void plain() { doIt(); }\n"
        "  @org.junit.jupiter.api.Test void qualified() { doIt(); }\n"
        "  @ParameterizedTest @ValueSource(ints = {1}) void parameterized(int n) { doIt(); }\n"
        "  @Disabled @Test void disabled() { doIt(); }\n"
        "  private void helper() { int x = 1; }\n"
        "}\n"
    )
    counts = _counts(source, "src/test/java/FooTest.java", "java")
    assert {name for name, row in counts.items() if row[2]} == {
        "disabled",
        "parameterized",
        "plain",
        "qualified",
    }


def test_go_subtest_assertions_belong_to_the_parent() -> None:
    _require("go")
    # A table-driven test asserts inside a ``t.Run`` closure, which is not its
    # own entry, so the parent must carry the assertion or every table test in
    # the language reads as assertion-free.
    source = (
        "package p\n"
        "\n"
        "func TestTable(t *testing.T) {\n"
        "\tfor _, tc := range cases {\n"
        "\t\tt.Run(tc.name, func(t *testing.T) {\n"
        "\t\t\tif got != tc.want {\n"
        '\t\t\t\tt.Errorf("bad")\n'
        "\t\t\t}\n"
        "\t\t})\n"
        "\t}\n"
        "}\n"
    )
    assert _flagged(source, "pkg/thing_test.go", "go") == []


def test_a_concise_arrow_body_is_the_assertion() -> None:
    _require("typescript")
    # ``it("x", () => expect(a).toBe(b))`` holds no statement at all, so the
    # sibling scan never sees the assertion and the test read as bare.
    source = (
        'describe("S", () => {\n'
        '  it("concise", () => expect(add(1, 2)).toBe(3));\n'
        '  it("concise chai", () => expect(x).to.be.null);\n'
        '  it("concise await", async () => await expect(p).to.be.fulfilled);\n'
        '  it("bare", () => doThing());\n'
        "});\n"
    )
    assert _flagged(source, "src/x.test.ts", "typescript") == ["it callback"]


def test_chai_property_assertions_count() -> None:
    _require("typescript")
    # ``expect(x).to.be.null`` ends in a property, so the statement is a member
    # expression and not a call, and every lookup above it declines. It was the
    # largest TypeScript false positive.
    source = (
        'describe("S", () => {\n'
        '  it("null", () => { expect(content).to.be.null });\n'
        '  it("true", () => { expect(ok).to.be.true });\n'
        '  it("bare", () => { doThing(); });\n'
        "});\n"
    )
    assert _flagged(source, "src/x.test.ts", "typescript") == ["it callback"]


def test_property_assertions_never_join_an_assertion_run() -> None:
    _require("typescript")
    source = (
        'describe("S", () => {\n'
        '  it("runs", () => {\n'
        "    expect(a).toBe(1);\n"
        "    expect(b).to.be.null;\n"
        "    expect(c).toBe(3);\n"
        "  });\n"
        "});\n"
    )
    fc = walk_file("src/x.test.ts", "typescript", source.encode("utf-8"))
    runs = [b for f in fc.functions for b in f.assertion_blocks]
    # Two narrow assertions split by a broad one, exactly as before this
    # existed: a broad statement breaks a run rather than bridging it.
    assert runs == []


def test_an_assertion_in_a_nested_callback_belongs_to_the_test() -> None:
    _require("typescript")
    # An expression-bodied arrow holds no statement, so its assertion reached
    # no count at all until the walk counted lambda bodies. It was the largest
    # remaining TypeScript false positive.
    source = (
        'describe("S", () => {\n'
        '  it("waits", async () => {\n'
        "    await waitFor(() => expect(sent).toBe(1));\n"
        "  });\n"
        '  it("each", () => {\n'
        "    rows.forEach((r) => expect(r).toBe(1));\n"
        "  });\n"
        '  it("bare", () => { doThing(); });\n'
        "});\n"
    )
    assert _flagged(source, "src/x.test.ts", "typescript") == ["it callback"]


def test_a_suite_does_not_absorb_its_childrens_assertions() -> None:
    _require("typescript")
    # ``describe`` is transparent to the walker, but a nested *test case* must
    # still never count as its parent's oracle, or one asserting sibling would
    # silence the whole file.
    source = (
        'describe("outer", () => {\n'
        '  it("checks", () => { expect(a).toBe(1); });\n'
        '  it("bare", () => { doThing(); });\n'
        "});\n"
    )
    assert _flagged(source, "src/x.test.ts", "typescript") == ["it callback"]


def test_js_verification_counts_through_the_narrow_tier() -> None:
    _require("typescript")
    # jest spells verification with ``expect``, so it already reaches the
    # narrow tier and needs no row of its own. Pinned because it is the reason
    # ``verify_names`` is a Java-only concept today.
    source = (
        'describe("S", () => {\n'
        '  it("verifies", () => { expect(sender.send).toHaveBeenCalled(); });\n'
        '  it("bare", () => { sender.send(m); });\n'
        "});\n"
    )
    assert _flagged(source, "src/x.test.ts", "typescript") == ["it callback"]


def test_silent_on_a_jsx_file_walked_without_the_jsx_grammar() -> None:
    _require("typescript")
    # A ``.tsx`` file arrives tagged ``typescript`` and loses its assertions to
    # the non-JSX grammar, so every finding on one would be this bug rather
    # than a real result. The guard lapses once the tag is right.
    source = (
        'describe("C", () => {\n'
        '  it("renders", () => {\n'
        "    render(<Todo items={items} />);\n"
        '    expect(screen.getByText("x")).toBeInTheDocument();\n'
        "  });\n"
        "});\n"
    )
    assert _flagged(source, "src/C.spec.tsx", "typescript") == []
    # Walked as tsx the assertion is visible, so the guard must not apply.
    assert _flagged(source, "src/C.spec.tsx", "tsx") == []
    bare = 'describe("C", () => { it("bare", () => { render(<A />); }); });\n'
    assert _flagged(bare, "src/C.spec.tsx", "tsx") == ["it callback"]
    assert _flagged(bare, "src/C.spec.tsx", "typescript") == []


def test_a_private_assertion_helper_counts() -> None:
    _require("python")
    # ``_assert_no_secret_leak(msg)`` is an assertion helper by any reading,
    # but the narrow prefixes anchor at the start and the underscore breaks the
    # anchor. Python marks helpers private this way constantly.
    source = (
        "def test_private_helper():\n"
        "    _assert_no_secret_leak(msg)\n"
        "\n"
        "def test_plain_helper():\n"
        "    _build_user()\n"
    )
    assert _flagged(source) == ["test_plain_helper"]


def test_go_and_java_are_classified_but_never_reported() -> None:
    _require("go")
    _require("java")
    # Both languages classify test cases and count assertions correctly -- that
    # is how their precision was measured -- but the marker does not report on
    # them, because each delegates its oracle to a helper this pass cannot see.
    go = "package p\n\nfunc TestBare(t *testing.T) {\n\tdoThing()\n}\n"
    java = "class FooTest {\n  @Test void bare() { service.doIt(); }\n}\n"
    assert _counts(go, "pkg/thing_test.go", "go")["TestBare"][2] is True
    assert _counts(java, "src/test/java/FooTest.java", "java")["bare"][2] is True
    assert _flagged(go, "pkg/thing_test.go", "go") == []
    assert _flagged(java, "src/test/java/FooTest.java", "java") == []
