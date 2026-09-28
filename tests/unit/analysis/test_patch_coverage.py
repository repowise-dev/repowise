"""Patch coverage: the pure compute and its renderings."""

from __future__ import annotations

from repowise.core.analysis.health.coverage import file_coverage
from repowise.core.analysis.patch_coverage import (
    PatchScope,
    compute_patch_coverage,
    github_annotations,
    headline,
    line_ranges,
    render_markdown,
)


def _cov(path: str, covered: list[int], coverable: list[int]):
    return {path: file_coverage(path, covered, coverable)}


def test_counts_only_changed_executable_lines() -> None:
    # Lines 1-6 changed; 2 and 5 are comments (not coverable); 3 is unexecuted.
    coverage = _cov("src/a.py", covered=[1, 4, 6, 9], coverable=[1, 3, 4, 6, 9])
    pc = compute_patch_coverage({"src/a.py": {1, 2, 3, 4, 5, 6}}, coverage)

    (f,) = pc.files
    assert f.status == "measured"
    assert (f.covered_lines, f.coverable_lines, f.changed_lines) == (3, 4, 6)
    assert f.uncovered_ranges == ((3, 3),)
    assert pc.pct == 75.0


def test_file_absent_from_report_is_not_in_report_and_not_counted() -> None:
    coverage = _cov("src/a.py", covered=[1], coverable=[1])
    pc = compute_patch_coverage({"src/a.py": {1}, "src/new.py": {1, 2}}, coverage)

    assert [f.status for f in pc.files] == ["measured", "not_in_report"]
    assert pc.pct == 100.0  # the unknown file is surfaced, not scored as 0%


def test_tests_and_unmeasured_kinds_are_out_of_scope() -> None:
    coverage = _cov("src/a.py", covered=[1], coverable=[1])
    pc = compute_patch_coverage(
        {"src/a.py": {1}, "tests/test_a.py": {3}, "README.md": {1}}, coverage
    )

    assert [f.path for f in pc.files] == ["src/a.py"]
    assert pc.out_of_scope == 2


def test_report_without_line_data_is_never_zero_percent() -> None:
    fc = file_coverage("src/a.py", [1], [])
    pc = compute_patch_coverage({"src/a.py": {1, 2}}, {"src/a.py": fc})

    assert pc.files[0].status == "no_line_data"
    assert pc.pct is None
    assert pc.gate == "not_set"


def test_gate() -> None:
    coverage = _cov("a.py", covered=[1], coverable=[1, 2])
    changed = {"a.py": {1, 2}}
    assert compute_patch_coverage(changed, coverage, threshold=50).gate == "pass"
    assert compute_patch_coverage(changed, coverage, threshold=60).gate == "fail"
    # Only comments changed: nothing to judge, so the gate does not fail.
    assert compute_patch_coverage({"a.py": {9}}, coverage, threshold=90).gate == "no_data"


def test_line_ranges() -> None:
    assert line_ranges([5, 1, 2, 3, 7, 8]) == ((1, 3), (5, 5), (7, 8))
    assert line_ranges([]) == ()


def test_to_dict_shape() -> None:
    coverage = _cov("a.py", covered=[1], coverable=[1, 2])
    d = compute_patch_coverage(
        {"a.py": {1, 2}}, coverage, threshold=80, scope=PatchScope(label="main...HEAD")
    ).to_dict()

    assert d["patch_coverage_pct"] == 50.0
    assert d["gate"] == "fail"
    assert d["file_counts"]["measured"] == 1
    assert d["files"][0]["uncovered_ranges"] == [[2, 2]]
    assert d["scope"]["label"] == "main...HEAD"


def test_markdown_passing_change_is_quiet() -> None:
    coverage = _cov("a.py", covered=[1, 2], coverable=[1, 2])
    md = render_markdown(
        compute_patch_coverage(
            {"a.py": {1, 2}},
            coverage,
            threshold=80,
            scope=PatchScope(label="main...HEAD", report_formats=("lcov",)),
        )
    )

    assert md.splitlines() == [
        "**Patch coverage 100.0%** (2 of 2 changed executable lines covered) "
        "· meets the 80.0% gate",
        "",
        "`main...HEAD` · lcov",
    ]


def test_markdown_lists_gaps_and_files_missing_from_report() -> None:
    coverage = _cov("a.py", covered=[1], coverable=[1, 2, 3, 5])
    md = render_markdown(
        compute_patch_coverage({"a.py": {1, 2, 3, 5}, "b.py": {1}}, coverage, threshold=90)
    )

    assert "below the 90.0% gate" in md
    assert "| `a.py` | 2-3, 5 | 1 of 4 |" in md
    assert "1 changed file is not in the coverage report" in md
    assert "`b.py`" in md
    assert "0%" not in md.split("<details>")[1]


def test_headline_without_data_explains_why() -> None:
    fc = file_coverage("a.py", [1], [])
    text = headline(compute_patch_coverage({"a.py": {1}}, {"a.py": fc}))
    assert "does not say which lines are executable" in text


def test_github_annotations_escape_and_span() -> None:
    coverage = _cov("dir,x/a:b.py", covered=[], coverable=[4, 5, 9])
    lines = github_annotations(compute_patch_coverage({"dir,x/a:b.py": {4, 5, 9}}, coverage))

    assert lines == [
        "::warning file=dir%2Cx/a%3Ab.py,line=4,endLine=5,title=Uncovered change::"
        "Changed lines 4-5 not covered by tests",
        "::warning file=dir%2Cx/a%3Ab.py,line=9,endLine=9,title=Uncovered change::"
        "Changed line 9 not covered by tests",
    ]
