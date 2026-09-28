"""Patch coverage: the pure compute and its renderings."""

from __future__ import annotations

from repowise.core.analysis.changed_lines import line_ranges
from repowise.core.analysis.health.coverage import file_coverage
from repowise.core.analysis.patch_coverage import (
    PatchScope,
    compute_patch_coverage,
    fmt_pct,
    github_annotations,
    headline,
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
    assert (f.covered_line_count, f.coverable_line_count, f.changed_line_count) == (3, 4, 6)
    assert f.uncovered_ranges == ((3, 3),)
    assert pc.patch_coverage_pct == 75.0


def test_file_absent_from_report_is_not_in_report_and_not_counted() -> None:
    coverage = _cov("src/a.py", covered=[1], coverable=[1])
    pc = compute_patch_coverage({"src/a.py": {1}, "src/new.py": {1, 2}}, coverage)

    assert [f.status for f in pc.files] == ["measured", "not_in_report"]
    assert pc.patch_coverage_pct == 100.0  # the unknown file is surfaced, not scored as 0%


def test_tests_and_unmeasured_kinds_are_out_of_scope() -> None:
    coverage = _cov("src/a.py", covered=[1], coverable=[1])
    pc = compute_patch_coverage(
        {"src/a.py": {1}, "tests/test_a.py": {3}, "README.md": {1}}, coverage
    )

    assert [f.file_path for f in pc.files] == ["src/a.py"]
    assert pc.out_of_scope_count == 2


def test_report_without_line_data_is_never_zero_percent() -> None:
    fc = file_coverage("src/a.py", [1], [])
    pc = compute_patch_coverage({"src/a.py": {1, 2}}, {"src/a.py": fc})

    assert pc.files[0].status == "no_line_data"
    assert pc.patch_coverage_pct is None
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
    assert d["files"][0]["file_path"] == "a.py"
    assert d["covered_line_count"] == 1
    assert d["scope"]["label"] == "main...HEAD"


def test_markdown_passing_change_is_quiet() -> None:
    coverage = _cov("a.py", covered=[1, 2], coverable=[1, 2])
    md = render_markdown(
        compute_patch_coverage(
            {"a.py": {1, 2}},
            coverage,
            threshold=80,
            scope=PatchScope(label="main...HEAD", source_formats=("lcov",)),
        )
    )

    assert md.splitlines() == [
        "**Patch coverage 100.0%** (2 of 2 changed executable lines covered) "
        "· meets the 80.0% gate",
        "",
        "`main...HEAD` · lcov · 1 of 1 changed files measured",
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


def test_gate_compares_unrounded_and_display_never_rounds_up() -> None:
    # 79.9975% rounds to 80.0 but must still fail an 80% gate, and say so.
    coverable = list(range(1, 40001))
    coverage = _cov("a.py", covered=coverable[:31999], coverable=coverable)
    pc = compute_patch_coverage({"a.py": set(coverable)}, coverage, threshold=80)

    assert pc.gate == "fail"
    assert "79.9%" in headline(pc)
    assert fmt_pct(79.96) == "79.9%"
    assert fmt_pct(None) == "n/a"


def test_no_data_headline_names_the_reason_and_the_skipped_gate() -> None:
    coverage = _cov("a.py", covered=[1], coverable=[1])
    empty = headline(compute_patch_coverage({}, coverage, threshold=80))
    assert "no changed lines" in empty
    assert "80.0% gate was not applied" in empty
    docs_only = headline(compute_patch_coverage({"README.md": {1}}, coverage))
    assert "Only tests or files the report does not measure changed" in docs_only


def test_annotations_mark_largest_ranges_first_and_count_the_rest() -> None:
    coverable = list(range(1, 100))
    # Twelve single uncovered lines plus one 5-line run.
    uncovered = {*range(1, 24, 2), 50, 51, 52, 53, 54}
    covered = [n for n in coverable if n not in uncovered]
    pc = compute_patch_coverage(
        {"a.py": set(coverable)}, _cov("a.py", covered, coverable), threshold=99
    )
    lines = github_annotations(pc)

    warnings = [line for line in lines if line.startswith("::warning")]
    assert len(warnings) == 10
    assert "line=50,endLine=54" in warnings[0]
    assert "::notice::3 more uncovered changed ranges" in lines[10]
    assert lines[-1].startswith("::error::Patch coverage")


def test_markdown_lists_files_without_line_data() -> None:
    fc = file_coverage("b.py", [1], [])
    md = render_markdown(
        compute_patch_coverage(
            {"a.py": {1}, "b.py": {1}}, {**_cov("a.py", [1], [1]), "b.py": fc}
        )
    )
    assert "1 changed file is in a report without line data" in md


def test_exact_threshold_passes_and_reads_exactly() -> None:
    # 57 / 100 * 100 is 56.99999999999999 in floating point.
    coverable = list(range(1, 101))
    pc = compute_patch_coverage(
        {"a.py": set(coverable)}, _cov("a.py", coverable[:57], coverable), threshold=57
    )
    assert pc.gate == "pass"
    assert "57.0%" in headline(pc)
    assert pc.to_dict()["patch_coverage_pct"] == 57.0


def test_serialized_percentage_is_floored_not_rounded() -> None:
    coverable = list(range(1, 40001))
    pc = compute_patch_coverage(
        {"a.py": set(coverable)}, _cov("a.py", coverable[:31999], coverable), threshold=80
    )
    assert pc.to_dict()["patch_coverage_pct"] == 79.99
