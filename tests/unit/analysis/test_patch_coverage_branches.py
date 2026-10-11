"""Branch coverage on changed lines: per-line branch data from each parser to the gate."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from repowise.core.analysis.health.coverage import (
    file_coverage,
    parse_clover,
    parse_cobertura,
    parse_go_coverprofile,
    parse_jacoco,
    parse_lcov,
    resolve_reports,
)
from repowise.core.analysis.patch_coverage import (
    ProjectDelta,
    ProjectTotals,
    attention_rows,
    branch_line,
    compute_patch_coverage,
    github_annotations,
    headline,
    render_markdown,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "coverage"


def _files(report) -> dict:
    return {f.file_path: f for f in report.files}


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_lcov_groups_brda_by_line_and_never_evaluated_is_not_taken() -> None:
    text = (
        "SF:a.py\nDA:3,1\nDA:5,0\nDA:7,1\n"
        "BRDA:3,0,0,2\nBRDA:3,0,1,0\n"  # one of two taken
        "BRDA:5,0,0,-\nBRDA:5,0,1,-\n"  # the line never ran
        "BRDA:7,0,0,1\nBRDA:7,0,1,4\n"  # both taken
        "end_of_record\n"
    )
    (a,) = parse_lcov(text).files

    assert a.branch_lines == {3: (1, 2), 5: (0, 2), 7: (2, 2)}
    assert a.branch_coverage_pct == 50.0  # file-level figure unchanged


def test_cobertura_reads_condition_coverage_per_line() -> None:
    foo = _files(parse_cobertura(_read("sample.cobertura.xml")))["src/foo.py"]
    assert foo.branch_lines == {3: (1, 2)}


def test_cobertura_takes_the_max_of_a_line_repeated_across_classes() -> None:
    line = '<line number="3" hits="1" branch="true" condition-coverage="{}"/>'
    text = (
        "<coverage><packages><package><classes>"
        f'<class filename="a.py"><lines>{line.format("50% (1/2)")}</lines></class>'
        f'<class filename="a.py"><lines>{line.format("100% (2/2)")}</lines></class>'
        "</classes></package></packages></coverage>"
    )
    (a,) = parse_cobertura(text).files

    assert a.branch_lines == {3: (2, 2)}
    assert a.branch_coverage_pct == 100.0  # never summed to 3 of 4


def test_jacoco_counts_covered_and_missed_branches_and_merges_groups() -> None:
    bar = _files(parse_jacoco(_read("sample.jacoco.xml")))["com/foo/Bar.java"]
    # Line 7: 0 of 2 in one group, 1 of 2 in the other: the max.
    assert bar.branch_lines == {5: (1, 2), 7: (1, 2)}


def test_clover_evaluation_counts_give_two_branches_a_line() -> None:
    # truecount / falsecount are how often the condition was true / false.
    y = _files(parse_clover(_read("sample.clover.xml")))["src/y.ts"]
    assert y.branch_lines == {3: (1, 2)}
    assert y.branch_coverage_pct == 50.0


def test_clover_branch_counts_read_covered_and_not_covered() -> None:
    # A report marked by its fixed project name and root version: truecount is
    # branches covered, falsecount branches not covered.
    (util,) = parse_clover(_read("sample.clover-counts.xml")).files

    assert util.branch_lines == {2: (1, 4), 3: (2, 2)}
    assert util.branch_coverage_pct == 50.0


def test_the_project_name_alone_does_not_mean_branch_counts() -> None:
    text = _read("sample.clover-counts.xml").replace(' clover="3.2.0"', "")
    (util,) = parse_clover(text).files

    assert util.branch_lines == {2: (2, 2), 3: (1, 2)}


def test_formats_without_branch_data_carry_none() -> None:
    (go,) = parse_go_coverprofile("mode: set\nm/a.go:1.1,3.2 2 1\n").files
    assert go.branch_lines == {}
    bar = _files(parse_cobertura(_read("sample.cobertura.xml")))["src/bar.py"]
    assert bar.branch_lines == {}


def test_merge_takes_the_most_taken_and_the_most_seen_per_line() -> None:
    first = parse_lcov("SF:a.py\nDA:1,1\nDA:2,1\nBRDA:1,0,0,1\nBRDA:1,0,1,0\nend_of_record\n")
    second = parse_lcov(
        "SF:a.py\nDA:1,1\nDA:2,1\nBRDA:1,0,0,0\nBRDA:1,0,1,1\n"
        "BRDA:2,0,0,1\nBRDA:2,0,1,0\nBRDA:2,0,2,0\nend_of_record\n"
    )

    (merged,) = resolve_reports([first, second], {"a.py"}).files

    assert merged.branch_lines == {1: (1, 2), 2: (1, 3)}


def test_merge_keeps_a_line_only_one_report_has_branch_data_for() -> None:
    with_branches = parse_lcov("SF:a.py\nDA:1,1\nBRDA:1,0,0,1\nBRDA:1,0,1,0\nend_of_record\n")
    lines_only = parse_lcov("SF:a.py\nDA:1,1\nDA:2,1\nend_of_record\n")

    for order in ([with_branches, lines_only], [lines_only, with_branches]):
        (merged,) = resolve_reports(order, {"a.py"}).files
        assert merged.branch_lines == {1: (1, 2)}


def _branchy(path: str = "src/a.py"):
    # Lines 1-6 executable; 6 never ran. 2 is partly taken, 4 fully taken,
    # 5 ran without taking a branch, 6 has branches but never ran.
    return {
        path: file_coverage(
            path,
            [1, 2, 3, 4, 5],
            [1, 2, 3, 4, 5, 6],
            branch_lines={2: (1, 2), 4: (2, 2), 5: (0, 2), 6: (0, 2)},
        )
    }


def test_partly_taken_lines_ran_and_the_share_covers_every_changed_line() -> None:
    pc = compute_patch_coverage({"src/a.py": {2, 3, 4}}, _branchy())

    (f,) = pc.files
    assert (f.branch_taken, f.branch_total) == (3, 4)
    assert f.partial_ranges == ((2, 2),)
    assert pc.patch_coverage_pct == 100.0  # the line figure is untouched
    assert pc.branch_pct == 75.0
    assert pc.partial_line_count == 1
    assert pc.scope.branch_data == "per_line"
    assert pc.to_dict()["branches"] == {
        "branch_taken": 3,
        "branch_total": 4,
        "branch_coverage_pct": 75.0,
        "partial_line_count": 1,
        "threshold": None,
        "gate": "not_set",
    }


def test_a_line_that_ran_with_no_branch_taken_is_partly_taken_one_that_never_ran_is_not() -> None:
    pc = compute_patch_coverage({"src/a.py": {5, 6}}, _branchy())

    (f,) = pc.files
    assert f.partial_ranges == ((5, 5),)
    assert f.uncovered_ranges == ((6, 6),)
    # Both lower the share, and each is shown as one or the other.
    assert (f.branch_taken, f.branch_total) == (0, 4)


def test_no_changed_line_branching_is_nothing_to_judge() -> None:
    pc = compute_patch_coverage({"src/a.py": {1, 3}}, _branchy())

    assert pc.branch_pct is None
    assert pc.to_dict()["branches"] is None
    assert branch_line(pc) == ""

    gated = replace(pc, branch_threshold=80)
    assert gated.branch_gate == "no_data"
    assert gated.gate == "no_data"  # not applied, never a failure
    assert gated.to_dict()["branches"]["branch_coverage_pct"] is None
    assert branch_line(gated) == (
        "Branches on changed lines: no changed executable line has branches. "
        "The 80.0% branch gate was not applied."
    )


def test_coverage_without_per_line_branch_data_says_so() -> None:
    cov = {"src/a.py": file_coverage("src/a.py", [1], [1])}
    gated = replace(compute_patch_coverage({"src/a.py": {1}}, cov), branch_threshold=80)

    assert gated.scope.branch_data == "none"
    assert branch_line(gated).startswith(
        "Branches on changed lines: not measured (the coverage report has no per-line "
        "branch data)"
    )
    stored = replace(gated, scope=replace(gated.scope, branch_data="stored_before"))
    assert "re-run `repowise coverage add`" in branch_line(stored)


def test_an_unapplied_branch_gate_is_a_github_notice() -> None:
    cov = {"src/a.py": file_coverage("src/a.py", [1], [1])}
    gated = replace(compute_patch_coverage({"src/a.py": {1}}, cov), branch_threshold=80)

    notices = [line for line in github_annotations(gated) if line.startswith("::notice")]
    assert notices == [
        "::notice::Branches on changed lines: not measured (the coverage report has no "
        "per-line branch data). The 80.0%25 branch gate was not applied."  # % escaped
    ]


def test_the_branch_gate_fails_the_change_beside_a_passing_line_gate() -> None:
    pc = compute_patch_coverage({"src/a.py": {2, 3, 4}}, _branchy(), threshold=80)

    assert replace(pc, branch_threshold=70).gate == "pass"
    failed = replace(pc, branch_threshold=80)
    assert (failed.flat_gate, failed.branch_gate, failed.gate) == ("pass", "fail", "fail")
    assert "below the 80.0% branch gate" in branch_line(failed, markdown=False)
    # The headline stays the line figure: the two are never blended.
    assert headline(failed).startswith("**Patch coverage 100.0%**")


def test_an_unapplied_branch_gate_gives_way_to_the_project_verdict() -> None:
    pc = compute_patch_coverage({"src/a.py": {1, 3}}, _branchy())
    project = ProjectDelta(ProjectTotals(80, 100), ProjectTotals(80, 100), "history", max_drop=1)

    assert replace(pc, branch_threshold=80, project=project).gate == "pass"


def test_the_small_change_tolerance_applies_to_the_branch_gate() -> None:
    pc = compute_patch_coverage(
        {"src/a.py": {2, 3, 4}}, _branchy(), min_coverable_lines=5
    )
    small = replace(pc, branch_threshold=80)

    assert small.branch_gate == "too_small"
    assert small.gate == "too_small"


def test_partly_taken_files_follow_uncovered_ones_and_get_a_column() -> None:
    cov = {**_branchy("src/a.py"), "src/b.py": file_coverage("src/b.py", [1], [1, 2])}
    pc = compute_patch_coverage({"src/a.py": {2, 3}, "src/b.py": {1, 2}}, cov)

    # a.py sorts first by name, but an uncovered line outranks a partly taken branch.
    assert [f.file_path for f in attention_rows(pc)] == ["src/b.py", "src/a.py"]
    md = render_markdown(pc)
    assert "**Branches on changed lines 50.0%** (1 of 2 taken; 1 line partly taken)" in md
    assert "| File | Uncovered changed lines | Partly taken | Covered |" in md
    assert "| `src/a.py` |  | 2 | 2 of 2 |" in md


def test_no_partly_taken_column_without_a_partly_taken_line() -> None:
    md = render_markdown(compute_patch_coverage({"src/a.py": {4, 6}}, _branchy()))

    assert "Branches on changed lines 50.0%" in md
    assert "Partly taken" not in md


def test_github_warns_per_partly_taken_range_after_the_uncovered_ones() -> None:
    cov = {
        "src/a.py": file_coverage(
            "src/a.py", [1, 2, 4], [1, 2, 3, 4], branch_lines={2: (1, 2), 4: (1, 3)}
        )
    }
    pc = replace(
        compute_patch_coverage({"src/a.py": {1, 2, 3, 4}}, cov), branch_threshold=90
    )

    lines = github_annotations(pc)

    assert lines[0].startswith("::error::Branches on changed lines 40.0%")
    uncovered, *partial = lines[1:]
    assert "title=Uncovered change" in uncovered
    assert [line.split("::", 2)[1] for line in partial] == [
        "warning file=src/a.py,line=2,endLine=2,title=Partly taken branch",
        "warning file=src/a.py,line=4,endLine=4,title=Partly taken branch",
    ]
    assert partial[0].endswith("Changed line 2 ran, but not every branch was taken by tests")
