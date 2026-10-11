"""``mock_saturated_test``: the mock-setup count, and the ratio it fires on.

The counting cases are written as real source through the walker rather than as
hand-built ``FunctionComplexity`` rows, because the failures worth catching here
are grammar-shaped: a decorator that lives on the parent node, a receiver read
in the wrong order, an HTTP ``patch`` that is not a mock.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.mock_saturated_test import BIOMARKER
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.mock_walk import file_may_contain_mocks
from repowise.core.analysis.health.models import Severity


def _walk(
    source: str, path: str = "tests/test_sample.py", language: str = "python"
) -> dict[str, tuple[int, int]]:
    """``{function: (mock_setup_count, assertion_count)}`` for one file."""
    fc = walk_file(path, language, source.encode("utf-8"))
    return {f.name: (f.mock_setup_count, f.assertion_count) for f in fc.functions}


def _rows(source: str, path: str, language: str) -> list[tuple[int, int]]:
    """``(mock_setup_count, assertion_count)`` per function, in document order.

    Unlike :func:`_walk`, keeps every row: a file's ``it`` callbacks all share
    one name and a name-keyed view would collapse them.
    """
    fc = walk_file(path, language, source.encode("utf-8"))
    return [(f.mock_setup_count, f.assertion_count) for f in fc.functions]


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
        all_functions=tuple(fc.functions),
    )
    return BIOMARKER.detect(ctx)


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_counts_constructors_config_and_decorators():
    counts = _walk(
        """
from unittest.mock import MagicMock, patch

@patch("pkg.mod.load")
def test_saturated(mock_load):
    client = MagicMock()
    client.get.return_value = MagicMock()
    repo = MagicMock()
    repo.fetch.side_effect = [1, 2]
    mock_load.return_value = repo
    assert repo.fetch() == 1
"""
    )
    # 1 decorator + 5 body statements; the assertion is not counted as setup.
    assert counts["test_saturated"] == (6, 1)


def test_a_statement_matching_twice_counts_once():
    """``x.y.return_value = MagicMock()`` is one act of setup, not two."""
    counts = _walk(
        """
from unittest.mock import MagicMock

def test_one():
    repo.load.return_value = MagicMock()
    assert repo.load()
"""
    )
    assert counts["test_one"] == (1, 1)


def test_an_assertion_on_a_mock_is_not_setup():
    """``mock.assert_called_once()`` reads the double but verifies with it.

    It matches the mock vocabulary on its receiver, so without the
    assertion-first rule it would count in both halves of the ratio.
    """
    counts = _walk(
        """
from unittest.mock import MagicMock

def test_verifies():
    client = MagicMock()
    client.get()
    client.get.assert_called_once()
    client.post.assert_not_called()
"""
    )
    mocks, asserts = counts["test_verifies"]
    assert asserts == 2
    assert mocks == 1  # only ``client = MagicMock()``


def test_http_patch_is_not_a_mock():
    """The context guard. ``client.patch("/url")`` is a PATCH request."""
    counts = _walk(
        """
def test_http(client):
    response = client.patch("/users/1", json={"a": 1})
    other = client.patch("/users/2", json={"b": 2})
    assert response.status_code == 200
"""
    )
    assert counts["test_http"] == (0, 1)


def test_bare_and_module_qualified_patch_are_mocks():
    counts = _walk(
        """
import mock
from unittest.mock import patch

def test_bare():
    with patch("pkg.a") as a:
        assert a is not None

def test_qualified():
    with mock.patch("pkg.b") as b:
        assert b is not None
"""
    )
    assert counts["test_bare"][0] == 1
    assert counts["test_qualified"][0] == 1


def test_monkeypatch_counts_only_its_doubling_methods():
    """Arranging the environment is not installing a test double."""
    counts = _walk(
        """
def test_env(monkeypatch):
    monkeypatch.setenv("A", "1")
    monkeypatch.delenv("B", raising=False)
    monkeypatch.chdir("/tmp")
    monkeypatch.syspath_prepend("/x")
    assert True

def test_double(monkeypatch):
    monkeypatch.setattr("pkg.a", 1)
    monkeypatch.delattr("pkg.b")
    monkeypatch.setitem({}, "k", 3)
    assert True
"""
    )
    assert counts["test_env"] == (0, 1)
    assert counts["test_double"] == (3, 1)


def test_nested_function_setup_does_not_count_against_its_parent():
    """A closure's doubles belong to the closure, not the test around it.

    The walker records no row for a function nested inside a function body, so
    the setup is not attributed anywhere - which is the conservative direction:
    it can only ever suppress a finding, never invent one.
    """
    counts = _walk(
        """
from unittest.mock import MagicMock

def test_outer():
    def helper():
        a = MagicMock()
        b = MagicMock()
        return a, b

    assert helper()
"""
    )
    assert counts["test_outer"] == (0, 1)
    assert "helper" not in counts


def test_setup_inside_a_nested_block_is_counted():
    counts = _walk(
        """
from unittest.mock import MagicMock

def test_loop():
    for _ in range(2):
        client = MagicMock()
        client.get.return_value = 1
    assert client
"""
    )
    assert counts["test_loop"] == (2, 1)


# ---------------------------------------------------------------------------
# The whole-file precheck
# ---------------------------------------------------------------------------


def test_precheck_skips_a_file_with_no_mock_vocabulary():
    assert not file_may_contain_mocks(b"def f(x):\n    return x + 1\n")


@pytest.mark.parametrize(
    "source",
    [b"MagicMock()", b"from unittest import mock", b"a_stub = 1", b"@patch('x')", b"SPY = 2"],
)
def test_precheck_is_case_insensitive_and_catches_the_vocabulary(source):
    assert file_may_contain_mocks(source)


def test_precheck_is_a_pure_function_of_the_bytes():
    """It must not read the path: the walk cache keys on content and language."""
    source = b"from unittest.mock import MagicMock\n"
    assert file_may_contain_mocks(source) is file_may_contain_mocks(bytes(source))


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


_SATURATED = """
from unittest.mock import MagicMock

def test_saturated():
    a = MagicMock()
    b = MagicMock()
    c = MagicMock()
    d = MagicMock()
    e = MagicMock()
    f = MagicMock()
    a.go.return_value = b
    assert a.go() is b
"""


def test_fires_on_a_saturated_test():
    findings = _detect(_SATURATED)
    assert [f.biomarker_type for f in findings] == ["mock_saturated_test"]
    finding = findings[0]
    assert finding.function_name == "test_saturated"
    assert finding.details["mock_setup_count"] == 7
    assert finding.details["assertion_count"] == 1
    assert finding.details["ratio"] == 7.0


def test_silent_on_a_production_file():
    """Only test files. Production code that builds doubles is not a test."""
    assert _detect(_SATURATED, path="src/pkg/module.py") == []


def test_silent_when_assertions_keep_pace():
    findings = _detect(
        """
from unittest.mock import MagicMock

def test_balanced():
    a = MagicMock()
    b = MagicMock()
    c = MagicMock()
    d = MagicMock()
    e = MagicMock()
    f = MagicMock()
    assert a
    assert b
    assert c
    assert d
"""
    )
    assert findings == []


def test_silent_on_a_fixture_with_no_assertions():
    """A mock factory with no assertions is correct code, not a saturated test.

    It is also what ``assertion_free_test`` is for; firing here would report the
    same function twice under two names.
    """
    findings = _detect(
        """
from unittest.mock import MagicMock

def make_fake_repo():
    repo = MagicMock()
    repo.load.return_value = MagicMock()
    repo.save.return_value = None
    repo.drop.return_value = None
    repo.list.return_value = []
    repo.count.return_value = 0
    return repo
"""
    )
    assert findings == []


def test_silent_below_the_absolute_setup_floor():
    """Ratio alone is not enough: a small test is not saturated."""
    findings = _detect(
        """
from unittest.mock import MagicMock

def test_small():
    a = MagicMock()
    b = MagicMock()
    c = MagicMock()
    assert a
"""
    )
    assert findings == []


def test_silent_on_a_non_test_function_in_a_test_file():
    findings = _detect(
        """
from unittest.mock import MagicMock

def build_harness():
    a = MagicMock()
    b = MagicMock()
    c = MagicMock()
    d = MagicMock()
    e = MagicMock()
    f = MagicMock()
    g = MagicMock()
    assert a
"""
    )
    assert findings == []


def test_severity_rises_with_saturation():
    low = _detect(_SATURATED)[0]
    assert low.severity is Severity.LOW
    high = _detect(
        """
from unittest.mock import MagicMock

def test_very_saturated():
    a = MagicMock()
    b = MagicMock()
    c = MagicMock()
    d = MagicMock()
    e = MagicMock()
    f = MagicMock()
    g = MagicMock()
    h = MagicMock()
    assert a
"""
    )[0]
    assert high.severity is Severity.MEDIUM


def test_a_language_with_no_dialect_produces_no_signal():
    """A language absent from ``MOCK_DIALECTS`` stays silent.

    Go is the case that matters: it is absent deliberately, not by omission,
    because its assertion count is best-effort for testify only.
    """
    _require("go")
    source = """
func TestThing(t *testing.T) {
	a := mockThing()
	b := mockThing()
	c := mockThing()
	d := mockThing()
	e := mockThing()
	f := mockThing()
	assert.NotNil(t, a)
}
"""
    # Assert the walk saw the function first, so a missing grammar cannot make
    # this pass by producing no rows at all.
    assert _walk(source, "thing_test.go", "go")["TestThing"] == (0, 1)
    assert _detect(source, path="thing_test.go", language="go") == []


# ---------------------------------------------------------------------------
# TypeScript / JavaScript
# ---------------------------------------------------------------------------


_TS_SATURATED = """
describe("thing", () => {
  it("passes whatever the code does", () => {
    const a = vi.fn();
    const b = vi.fn();
    const c = vi.fn();
    const d = vi.fn();
    const e = vi.fn();
    const f = vi.fn();
    expect(a).toBeDefined();
  });
});
"""


@pytest.mark.parametrize(
    ("language", "path"),
    [("typescript", "tests/thing.test.ts"), ("javascript", "tests/thing.test.js")],
)
def test_a_saturated_it_callback_fires(language: str, path: str) -> None:
    """The dialect reaches both tags, which share one row."""
    _require(language)
    findings = _detect(_TS_SATURATED, path=path, language=language)
    assert len(findings) == 1
    assert findings[0].details["mock_setup_count"] == 6
    assert findings[0].details["assertion_count"] == 1


def test_each_it_callback_is_counted_separately() -> None:
    """A ``describe`` body is not a function entry, so its ``it``s are.

    Both callbacks are named ``"it callback"``; a name-keyed view keeps one.
    """
    _require("typescript")
    rows = _rows(
        """
describe("thing", () => {
  it("first", () => {
    const a = vi.fn();
    expect(a).toBeDefined();
  });
  it("second", () => {
    const b = vi.fn();
    const c = vi.fn();
    expect(b).toBeDefined();
  });
});
""",
        "tests/thing.test.ts",
        "typescript",
    )
    assert sorted(rows) == [(1, 1), (2, 1)]


def test_both_it_callbacks_reach_the_detector() -> None:
    """The collapse this guards against silently hid every test but one."""
    _require("typescript")
    source = """
describe("thing", () => {
  it("one", () => {
    const a = vi.fn();
    const b = vi.fn();
    const c = vi.fn();
    const d = vi.fn();
    const e = vi.fn();
    const f = vi.fn();
    expect(a).toBeDefined();
  });
  it("two", () => {
    const g = vi.fn();
    const h = vi.fn();
    const i = vi.fn();
    const j = vi.fn();
    const k = vi.fn();
    const l = vi.fn();
    expect(g).toBeDefined();
  });
});
"""
    findings = _detect(source, path="tests/thing.test.ts", language="typescript")
    assert len(findings) == 2, "a name-keyed view would report only one"


def test_arranging_the_test_environment_is_not_mock_setup() -> None:
    """``vi.useFakeTimers`` arranges the run; only doubling counts.

    The same split ``monkeypatch.setenv`` forced for Python.
    """
    _require("typescript")
    counts = _rows(
        """
describe("thing", () => {
  it("works", () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    vi.stubEnv("MODE", "test");
    const a = vi.fn();
    expect(a).toBeDefined();
  });
});
""",
        "tests/thing.test.ts",
        "typescript",
    )
    assert counts == [(1, 1)]


def test_java_verify_is_not_an_assertion() -> None:
    """``verify(...)`` must never satisfy the assertion count.

    SonarQube's S2699 treats it as satisfying a test's assertion requirement.
    Ours must not: a test built entirely of mocks and verifies is the thing
    this marker measures, and counting verifies would hide exactly that.
    This is also why Java carries no dialect -- with its real checks excluded,
    an over-mocked Java test reaches the ratio with a vacuous denominator.
    """
    _require("java")
    counts = _walk(
        """
class ThingTest {
  @Test
  void sendsTheMessage() {
    Mockito.verify(repository).save(entity);
    Mockito.verify(sender).send(message);
    verify(listener).onDone();
    assertEquals(1, result);
  }
}
""",
        "src/test/java/ThingTest.java",
        "java",
    )
    assert counts["sendsTheMessage"][1] == 1, "only assertEquals is an assertion"


# ---------------------------------------------------------------------------
# Regressions found in review
# ---------------------------------------------------------------------------


def test_a_bare_local_named_return_value_is_not_mock_setup():
    """A config attribute needs a receiver.

    ``return_value = compute()`` is an ordinary local binding. Counting it
    inflated the numerator on code that had no doubles in it at all.
    """
    counts = _walk(
        """
def test_local():
    return_value = compute()
    side_effect = other()
    assert return_value
    assert side_effect
"""
    )
    assert counts["test_local"] == (0, 2)


def test_an_argument_name_does_not_make_a_call_mock_ish():
    """Only the callee names a call. ``check(mock_repo)`` is not mock setup."""
    counts = _walk(
        """
def test_args(mock_repo):
    check(mock_repo)
    verify(mock_repo)
    assert True
"""
    )
    assert counts["test_args"][0] == 0


def test_blocks_are_unchanged_by_the_assertion_total():
    """The run scan is a calibrated, SCORING input and must not narrow.

    Two assert-ish calls inside one assertion's argument list form a phantom
    run of two. That is what ``duplicated_assertion_block`` was calibrated
    against, so the total had to be scoped without touching the run scan.
    """
    counts = _walk(
        """
class T:
    def test_nested_asserts(self):
        self.assertEqual(assert_ok(a), assert_ok(b))
"""
    )
    fc = walk_file(
        "tests/test_sample.py",
        "python",
        b"class T:\n    def test_nested_asserts(self):\n"
        b"        self.assertEqual(assert_ok(a), assert_ok(b))\n",
    )
    blocks = next(f.assertion_blocks for f in fc.functions if f.name == "test_nested_asserts")
    assert blocks, "the phantom run must survive - duplicated_assertion_block reads it"
    assert counts["test_nested_asserts"][1] == 1, "but the total counts one statement"
