"""Tests for the test-quality biomarkers + large_method size hardening.

- ``large_assertion_block`` / ``duplicated_assertion_block`` fire only on
  test files and live in the mild ``test_quality`` category.
- ``large_method`` now requires a minimal CCN floor so a long-but-flat
  body (a big data literal) no longer reads as a complexity smell.
"""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.duplicated_assertion_block import (
    DuplicatedAssertionBlockDetector,
)
from repowise.core.analysis.health.biomarkers.large_assertion_block import (
    LargeAssertionBlockDetector,
)
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity import FunctionComplexity, walk_file
from repowise.core.analysis.health.duplication import ClonePair
from repowise.core.analysis.health.engine import walked_functions
from repowise.core.analysis.health.models import Severity


def _fn(
    name: str,
    *,
    nloc: int = 5,
    ccn: int = 1,
    assertion_blocks: list[tuple[int, int, int]] | None = None,
) -> FunctionComplexity:
    return FunctionComplexity(
        name=name,
        start_line=1,
        end_line=1 + nloc,
        ccn=ccn,
        max_nesting=0,
        cognitive=0,
        nloc=nloc,
        assertion_blocks=assertion_blocks or [],
    )


def _ctx(
    *,
    file_path: str,
    functions: list[FunctionComplexity],
    clones: list[ClonePair] | None = None,
) -> FileContext:
    return FileContext(
        file_path=file_path,
        language="python",
        nloc=200,
        has_test_file=False,
        module=None,
        all_functions=tuple(functions),
        clones=clones or [],
    )


# ---- large_assertion_block -----------------------------------------------


def test_large_assertion_block_fires_on_test_file():
    fn = _fn("test_x", assertion_blocks=[(10, 30, 18)])
    out = LargeAssertionBlockDetector().detect(_ctx(file_path="tests/test_x.py", functions=[fn]))
    assert len(out) == 1
    assert out[0].details["assertion_count"] == 18
    assert out[0].severity == Severity.MEDIUM  # 15 <= count < 30


def test_large_assertion_block_high_severity():
    fn = _fn("test_x", assertion_blocks=[(10, 60, 35)])
    out = LargeAssertionBlockDetector().detect(_ctx(file_path="tests/test_x.py", functions=[fn]))
    assert out[0].severity == Severity.HIGH  # count >= 30


def test_large_assertion_block_ignores_small_runs():
    fn = _fn("test_x", assertion_blocks=[(10, 18, 8)])
    assert (
        LargeAssertionBlockDetector().detect(_ctx(file_path="tests/test_x.py", functions=[fn]))
        == []
    )


def test_large_assertion_block_silent_on_production_file():
    # Same big block, but the file is not a test → never fires.
    fn = _fn("validate", assertion_blocks=[(10, 30, 18)])
    assert (
        LargeAssertionBlockDetector().detect(_ctx(file_path="src/validate.py", functions=[fn]))
        == []
    )


# ---- duplicated_assertion_block ------------------------------------------


def _clone(path: str, a: tuple[int, int], partner: str, b: tuple[int, int]) -> ClonePair:
    return ClonePair(
        file_a=path,
        file_b=partner,
        a_start_line=a[0],
        a_end_line=a[1],
        b_start_line=b[0],
        b_end_line=b[1],
        token_count=80,
    )


def test_duplicated_assertion_block_fires_when_clone_overlaps_block():
    fn = _fn("test_x", assertion_blocks=[(10, 20, 6)])
    clone = _clone("tests/test_x.py", (12, 19), "tests/test_y.py", (5, 12))
    out = DuplicatedAssertionBlockDetector().detect(
        _ctx(file_path="tests/test_x.py", functions=[fn], clones=[clone])
    )
    assert len(out) == 1
    assert out[0].severity == Severity.MEDIUM
    assert out[0].details["partner_file"] == "tests/test_y.py"


def test_duplicated_assertion_block_ignores_clone_outside_block():
    fn = _fn("test_x", assertion_blocks=[(10, 20, 6)])
    clone = _clone("tests/test_x.py", (40, 48), "tests/test_y.py", (5, 12))
    assert (
        DuplicatedAssertionBlockDetector().detect(
            _ctx(file_path="tests/test_x.py", functions=[fn], clones=[clone])
        )
        == []
    )


def test_duplicated_assertion_block_silent_on_production_file():
    fn = _fn("run", assertion_blocks=[(10, 20, 6)])
    clone = _clone("src/run.py", (12, 19), "src/other.py", (5, 12))
    assert (
        DuplicatedAssertionBlockDetector().detect(
            _ctx(file_path="src/run.py", functions=[fn], clones=[clone])
        )
        == []
    )


# ---- large_method size hardening -----------------------------------------


def test_large_method_skips_long_flat_body():
    # 150 lines but zero branching (a big config dict) → not a smell.
    flat = _fn("CONFIG", nloc=150, ccn=1)
    assert LargeMethodDetector().detect(_ctx(file_path="src/x.py", functions=[flat])) == []


def test_large_method_fires_with_real_branching():
    # CCN 3 = genuine branching (two decision points). CCN 2 no longer fires:
    # it's the score a flat ``match`` dispatch table gets from its lone keyword
    # point, and that layout artefact should not read as a large-method smell.
    branchy = _fn("process", nloc=150, ccn=3)
    out = LargeMethodDetector().detect(_ctx(file_path="src/x.py", functions=[branchy]))
    assert len(out) == 1
    assert out[0].details["nloc"] == 150


# ---- the name key the markers used to read through ------------------------


def test_two_callbacks_sharing_a_name_are_both_seen():
    """A spec file's ``it`` callbacks all walk under one name.

    The markers used to read a map keyed by that name, which kept one row
    per distinct name. They read the walked list now, so both are reported.
    """
    asserts = "\n".join(f"\t\texpect(v).toBe({i})" for i in range(18))
    source = (
        "describe('s', () => {\n"
        f"\tit('a', () => {{\n{asserts}\n\t}})\n"
        f"\tit('b', () => {{\n{asserts}\n\t}})\n"
        "})\n"
    ).encode()

    walked = walk_file("thing.spec.ts", "typescript", source).functions
    callbacks = [fn for fn in walked if fn.assertion_blocks]
    assert len(callbacks) == 2
    # The premise: one name, two functions. Without it this test proves nothing.
    assert len({fn.name for fn in callbacks}) == 1

    out = LargeAssertionBlockDetector().detect(
        _ctx(
            file_path="src/__tests__/thing.spec.ts",
            functions=list(walked_functions(walked, "typescript")),
        )
    )
    assert len(out) == 2


def test_a_python_method_shadowed_by_its_namesake_is_seen():
    """Two classes in one module, each with a ``run``.

    The name key kept the first and dropped the second, which is most of what
    it cost Python.
    """
    source = (
        b"class A:\n"
        b"    def run(self):\n"
        b"        assert 1\n"
        b"class B:\n"
        b"    def run(self):\n"
        b"        assert 2\n"
    )
    walked = walk_file("test_m.py", "python", source).functions
    assert [fn.name for fn in walked] == ["run", "run"]

    seen = walked_functions(walked, "python")
    assert [fn.start_line for fn in seen] == [2, 5]


def test_the_walked_list_is_document_order_and_empty_for_sql():
    """The walker returns siblings last-first; markers report top-down."""
    source = b"def a():\n    pass\ndef b():\n    pass\ndef c():\n    pass\n"
    walked = walk_file("m.py", "python", source).functions
    # The premise: unsorted, the walker hands these back reversed.
    assert [fn.start_line for fn in walked] == [5, 3, 1]

    assert [fn.start_line for fn in walked_functions(walked, "python")] == [1, 3, 5]
    # SQL routines are text-counted; they never reach a method biomarker.
    assert walked_functions(walked, "sql") == ()
