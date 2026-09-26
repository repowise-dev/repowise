"""The two-tier assertion vocabulary: what each tier counts, and for whom.

Written as real source through the walker rather than as hand-built rows,
because every case here is grammar-shaped: a receiver read in the wrong order,
a matcher chain whose assertion verb sits in the middle, a ``t.Run`` that looks
exactly like a ``t.Fatal`` to a name-only rule.

The load-bearing test in this file is
:func:`test_a_dialect_never_changes_the_narrow_blocks`. The broad tier is only
shippable because the calibrated block markers cannot see it.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.asserts.lexicon import (
    AssertVocabulary,
    assert_dialect,
)
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.config import _assertion_names
from repowise.core.analysis.health.walk_cache import HealthWalkCache


def _require(language: str) -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


def _counts(
    source: str, path: str, language: str, extra: frozenset[str] = frozenset()
) -> dict[str, tuple[int, int]]:
    """``{function: (assertion_count, number of assertion blocks)}``."""
    fc = walk_file(path, language, source.encode("utf-8"), extra)
    return {f.name: (f.assertion_count, len(f.assertion_blocks)) for f in fc.functions}


# ---------------------------------------------------------------------------
# Go — the language the single-tier vocabulary left invisible
# ---------------------------------------------------------------------------

GO_TB = """
func TestThing(t *testing.T) {
	if got != want {
		t.Errorf("got %v want %v", got, want)
	}
	if err != nil {
		t.Fatalf("boom: %v", err)
	}
	t.Log("diagnostic, not an assertion")
	t.Run("subtest", func(t *testing.T) {})
	t.Skip("not an assertion either")
}
"""


def test_go_testing_failures_are_assertions() -> None:
    """``t.Errorf`` / ``t.Fatalf`` are how stdlib Go asserts.

    They carry almost all of Go's assertion volume and none of it was visible
    to the ``assert``/``expect`` prefix rule. Figures in LANGUAGE_SUPPORT.md.
    """
    _require("go")
    assert _counts(GO_TB, "pkg/thing_test.go", "go")["TestThing"][0] == 2


def test_go_test_handle_reporting_methods_are_not_assertions() -> None:
    """``t.Log`` / ``t.Run`` / ``t.Skip`` share the receiver and assert nothing.

    The reason the ``t`` receiver carries a method list rather than being taken
    whole, the way ``require`` is.
    """
    _require("go")
    source = """
func TestThing(t *testing.T) {
	t.Log("a")
	t.Logf("b %v", c)
	t.Run("sub", func(t *testing.T) {})
	t.Skip("c")
	t.Helper()
}
"""
    assert _counts(source, "pkg/thing_test.go", "go")["TestThing"][0] == 0


def test_go_testify_require_joins_assert() -> None:
    """``require.NoError`` is ``assert.NoError``'s sibling and was a total miss.

    ``assert.Equal`` already matched on its receiver name; ``require`` did not.
    """
    _require("go")
    source = """
func TestThing(t *testing.T) {
	require.NoError(t, err)
	require.Equal(t, want, got)
	assert.Equal(t, want, got)
}
"""
    assert _counts(source, "pkg/thing_test.go", "go")["TestThing"][0] == 3


# ---------------------------------------------------------------------------
# TypeScript — should.js / chai's BDD chain
# ---------------------------------------------------------------------------


def test_typescript_should_chain_is_an_assertion() -> None:
    """``result.should.equal(x)`` asserts through a receiver, not a callee."""
    _require("typescript")
    source = """
describe("thing", () => {
  it("works", () => {
    result.should.equal(5);
    other.should.containEql("a");
  });
});
"""
    assert _counts(source, "tests/thing.test.ts", "typescript")["it callback"][0] == 2


def test_typescript_should_prefixed_callee_is_not_an_assertion() -> None:
    """``shouldRetry(cfg)`` is the code under test.

    A prefix rule takes this; an exact one does not. The measured reason the
    vocabulary is exact names only.
    """
    _require("typescript")
    source = """
describe("thing", () => {
  it("works", () => {
    shouldRetry(cfg);
    shouldZeroCost(opts);
  });
});
"""
    assert _counts(source, "tests/thing.test.ts", "typescript")["it callback"][0] == 0


# ---------------------------------------------------------------------------
# Object Pascal — DUnit's Check*/Fail family (broad) and DUnitX's Assert.*
# plus the RTL's own Assert() (both narrow, no dialect row needed)
# ---------------------------------------------------------------------------


def test_pascal_dunit_check_family_is_broad_not_narrow() -> None:
    """``CheckEquals`` / ``CheckTrue`` / ``Fail`` count but never join a run.

    None start with ``assert``/``expect``, so they are broad-tier only via
    the Pascal ``AssertDialect`` row — same posture as Go's bare receiver
    list.
    """
    _require("pascal")
    source = (
        "unit UTestFoo;\ninterface\nimplementation\n"
        "procedure TFooTest.TestBar;\nbegin\n"
        "  CheckEquals(5, GetValue);\n"
        "  CheckTrue(IsOk);\n"
        "  Fail('boom');\n"
        "end;\nend.\n"
    )
    count, blocks = _counts(source, "TestFoo.pas", "pascal")["TestBar"]
    assert count == 3
    assert blocks == 0


def test_pascal_dunitx_assert_dot_needs_no_dialect_row() -> None:
    """``Assert.AreEqual`` / ``Assert.IsTrue`` are narrow via the receiver
    identifier alone, and form a run like any other narrow-tier pair."""
    _require("pascal")
    source = (
        "unit UTestFoo;\ninterface\nimplementation\n"
        "procedure TFooTest.TestBar;\nbegin\n"
        "  Assert.AreEqual(5, GetValue);\n"
        "  Assert.IsTrue(IsOk);\n"
        "end;\nend.\n"
    )
    count, blocks = _counts(source, "TestFoo.pas", "pascal")["TestBar"]
    assert count == 2
    assert blocks == 1


def test_pascal_runtime_assert_call_is_narrow() -> None:
    """The RTL's own ``Assert(cond, msg)`` matches the narrow prefix rule
    like any other assert-prefixed callee -- no Pascal-specific handling."""
    _require("pascal")
    source = (
        "unit UTestFoo;\ninterface\nimplementation\n"
        "procedure TFooTest.TestBar;\nbegin\n"
        "  Assert(SomeCheck, 'x');\n"
        "  Assert(OtherCheck, 'y');\n"
        "end;\nend.\n"
    )
    count, blocks = _counts(source, "TestFoo.pas", "pascal")["TestBar"]
    assert count == 2
    assert blocks == 1


def test_pascal_production_code_has_no_false_assertions() -> None:
    """Ordinary calls, including a name merely containing ``check`` as a
    substring (``CheckoutCart``, not an exact broad-tier name), count nothing."""
    _require("pascal")
    source = (
        "unit U;\ninterface\nimplementation\n"
        "procedure Bar;\nbegin\n"
        "  CheckoutCart;\n"
        "  DoNormalWork;\n"
        "end;\nend.\n"
    )
    assert _counts(source, "u.pas", "pascal")["Bar"] == (0, 0)


# ---------------------------------------------------------------------------
# The boundary the phase turns on
# ---------------------------------------------------------------------------


def test_a_dialect_never_changes_the_narrow_blocks() -> None:
    """A broad-only assertion breaks a run exactly as a non-assertion does.

    ``large_assertion_block`` and ``duplicated_assertion_block`` are calibrated
    on these runs. If a vocabulary row could join one, this phase would move
    scored findings, and it must not.
    """
    _require("go")
    source = """
func TestThing(t *testing.T) {
	assert.Equal(t, a, b)
	require.NoError(t, err)
	assert.Equal(t, c, d)
}
"""
    with_dialect = _counts(source, "pkg/thing_test.go", "go")["TestThing"]
    assert with_dialect[0] == 3, "broad tier counts all three"
    assert with_dialect[1] == 0, "the require between them still splits the run"


def test_a_language_with_no_dialect_counts_exactly_as_before() -> None:
    """Python has no row, so nothing about its counting changed."""
    source = """
def test_thing():
    assert a == b
    self.assertEqual(c, d)
    verify(mock)
    result.should.equal(5)
"""
    counts = _counts(source, "tests/test_thing.py", "python")["test_thing"]
    assert counts == (2, 1), "the assert statement and assertEqual, as one run"


# ---------------------------------------------------------------------------
# The repository's own vocabulary
# ---------------------------------------------------------------------------


def test_configured_names_reach_the_broad_tier() -> None:
    """A house assertion helper the conventions miss can be declared."""
    source = """
def test_thing():
    ensureInvariant(client)
    ensureInvariant(client)
"""
    path, lang = "tests/test_thing.py", "python"
    assert _counts(source, path, lang)["test_thing"][0] == 0
    extra = frozenset({"ensureinvariant"})
    assert _counts(source, path, lang, extra)["test_thing"][0] == 2


def test_configured_names_cannot_move_a_block() -> None:
    """The property that makes user config safe to expose at all.

    Two consecutive configured assertions are two broad-tier counts and still
    zero narrow-tier runs, so no scored marker can see them.
    """
    source = """
def test_thing():
    ensureInvariant(client)
    ensureInvariant(client)
"""
    counts = _counts(source, "tests/test_thing.py", "python", frozenset({"ensureinvariant"}))
    assert counts["test_thing"] == (2, 0)


def test_configured_names_are_exact_not_prefixes() -> None:
    """``ensure`` must not sweep in ``ensureConnectionPool(...)``."""
    source = """
def test_thing():
    ensureInvariant(client)
    ensureConnectionPool(client)
"""
    counts = _counts(source, "tests/test_thing.py", "python", frozenset({"ensureinvariant"}))
    assert counts["test_thing"][0] == 1


def test_a_dialect_language_keeps_its_rows_when_names_are_configured() -> None:
    """Extras join a dialect rather than replacing it."""
    dialect = assert_dialect("go", frozenset({"mustmatch"}))
    assert dialect is not None
    assert "require" in dialect.assert_names and "mustmatch" in dialect.assert_names
    assert dialect.receiver_methods["t"], "the Go receiver list survives"


def test_configured_names_are_coerced_to_what_config_can_hold() -> None:
    """One string, a list, and anything else. A typo must not fail a run."""
    assert _assertion_names("MustMatch") == ["mustmatch"]
    assert _assertion_names([" Ensure ", "ok"]) == ["ensure", "ok"]
    assert _assertion_names(["", None, 17]) == []
    assert _assertion_names(None) == []
    assert _assertion_names(17) == []


def test_the_vocabulary_cache_key_ignores_list_order() -> None:
    """Two orderings of one list must share a cached walk."""
    a = AssertVocabulary.from_analyzer_config({"assert_extra_names": ["b", "a"]})
    b = AssertVocabulary.from_analyzer_config({"assert_extra_names": ["a", "b"]})
    assert a.key == b.key == "a,b"
    assert AssertVocabulary.from_analyzer_config({}).key == ""


def test_walk_cache_key_is_unchanged_without_a_vocabulary() -> None:
    """No existing cache entry is invalidated by the parameter existing."""
    assert HealthWalkCache.key("go", "abc") == "go:abc"
    assert HealthWalkCache.key("go", "abc", "") == "go:abc"
    assert HealthWalkCache.key("go", "abc", "ensure") == "go:abc:ensure"
