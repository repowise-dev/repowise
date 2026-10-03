"""Project coverage delta and coverage outside the change: the pure half."""

from __future__ import annotations

from dataclasses import replace

import pytest

from repowise.core.analysis.changed_lines import parse_unified_diff
from repowise.core.analysis.health.coverage import CoverageScope
from repowise.core.analysis.health.coverage import file_coverage as _fc
from repowise.core.analysis.patch_coverage.causes import apply_causes, name_causes
from repowise.core.analysis.patch_coverage.compute import compute_patch_coverage
from repowise.core.analysis.patch_coverage.delta import (
    IndirectCause,
    IndirectChange,
    ProjectDelta,
    ProjectTotals,
    incomparable_reasons,
    indirect_changes,
    project_totals,
)
from repowise.core.analysis.patch_coverage.render import (
    github_annotations,
    project_line,
    render_markdown,
)

# -- totals and the gate -----------------------------------------------------


def test_totals_sum_every_file_after_ignore() -> None:
    coverage = {
        "src/a.py": _fc("src/a.py", [1, 2], [1, 2, 3, 4]),
        "src/b.py": _fc("src/b.py", [1], [1]),
        "gen/c.py": _fc("gen/c.py", [], [1, 2, 3]),
    }
    totals = project_totals(coverage, ignore=["gen/"])

    assert (totals.covered_line_count, totals.coverable_line_count) == (3, 5)
    assert totals.pct == 60.0


def test_totals_take_a_report_summary_over_the_line_sets() -> None:
    # lcov LF/LH summaries are what the stored coverage summary counts too.
    fc = _fc("a.py", [1], [1, 2], total=10, hit=7)
    assert project_totals({"a.py": fc}) == ProjectTotals(7, 10)


def _delta(base: tuple[int, int], head: tuple[int, int], **kwargs) -> ProjectDelta:
    return ProjectDelta(
        base=ProjectTotals(*base), head=ProjectTotals(*head), basis="base_report", **kwargs
    )


def test_delta_is_head_minus_base_unrounded() -> None:
    d = _delta((800, 1000), (797, 1000))
    assert abs(d.delta_pct - -0.3) < 1e-9
    assert d.to_dict()["delta_pct"] == -0.3


def test_gate_without_max_drop_is_not_set() -> None:
    assert _delta((80, 100), (10, 100)).gate == "not_set"


@pytest.mark.parametrize(
    ("base", "head", "max_drop", "gate"),
    [
        ((80, 100), (79, 100), 1.0, "pass"),  # exactly max_drop passes
        ((80, 100), (79, 100), 0.5, "fail"),
        ((80, 100), (90, 100), 0.0, "pass"),
        # 99.9 - 99.6 is 0.30000000000001137 in floats: not a failure.
        ((9990, 10000), (9960, 10000), 0.3, "pass"),
        ((9990, 10000), (9959, 10000), 0.3, "fail"),
    ],
)
def test_gate_passes_at_exactly_max_drop_and_fails_past_it(base, head, max_drop, gate) -> None:
    assert _delta(base, head, max_drop=max_drop).gate == gate


def test_gate_has_no_data_without_both_sides_or_when_incomparable() -> None:
    assert ProjectDelta(None, ProjectTotals(1, 2), "history", max_drop=1).gate == "no_data"
    assert _delta((0, 0), (1, 2), max_drop=1).gate == "no_data"
    assert _delta((80, 100), (10, 100), max_drop=1, incomparable=("x",)).gate == "no_data"


def test_the_project_gate_joins_the_verdict_without_small_change_tolerance() -> None:
    coverage = {"a.py": _fc("a.py", [1], [1])}
    pc = compute_patch_coverage({"a.py": {1}}, coverage, min_coverable_lines=50)
    failing = _delta((80, 100), (70, 100), max_drop=1)
    passing = _delta((80, 100), (80, 100), max_drop=1)

    # Flat and risky unset: the project gate is the verdict, small change or not.
    assert replace(pc, project=failing).gate == "fail"
    assert replace(pc, project=passing).gate == "pass"
    assert replace(pc, project=None).gate == "not_set"
    # A flat verdict stands, and a failing project gate still fails the change.
    flat = replace(pc, threshold=50, min_coverable_lines=None)
    assert replace(flat, project=passing).gate == "pass"
    assert replace(flat, project=failing).gate == "fail"
    assert replace(pc, project=failing).to_dict()["project"]["gate"] == "fail"
    assert pc.to_dict()["project"] is None


# -- comparability -----------------------------------------------------------


def _scope(*formats: str, ignore=(), partial=False) -> CoverageScope:
    return CoverageScope(tuple(sorted(formats)), tuple(sorted(ignore)), partial)


def test_same_scope_is_comparable() -> None:
    assert incomparable_reasons(_scope("lcov", "lcov"), _scope("lcov", "lcov")) == ()


def test_incomparable_reasons_are_plain_sentences() -> None:
    assert incomparable_reasons(_scope("lcov"), _scope("lcov", "lcov")) == (
        "the base read 1 lcov report, the head read 2 lcov reports",
    )
    assert incomparable_reasons(_scope("lcov", ignore=["gen/"]), _scope("lcov")) == (
        "coverage.ignore differs",
    )
    assert incomparable_reasons(_scope("lcov", partial=True), _scope("lcov")) == (
        "the base report mapped fewer than half its files",
    )
    assert incomparable_reasons(None, _scope("lcov")) == (
        "the base ingest predates scope records; pass --base-report, "
        "or re-measure coverage at the base commit",
    )


def test_scope_round_trips_through_its_dict() -> None:
    scope = _scope("lcov", "cobertura", ignore=["b/", "a/"], partial=True)
    assert CoverageScope.from_dict(scope.to_dict()) == scope
    assert CoverageScope.from_dict({"report_formats": "lcov"}) is None
    assert CoverageScope.from_dict(None) is None


def test_resolved_reports_record_one_format_per_report() -> None:
    from repowise.core.analysis.health.coverage import parse_lcov, resolve_reports

    lcov = "SF:a.py\nDA:1,1\nend_of_record\n"
    resolved = resolve_reports([parse_lcov(lcov), parse_lcov(lcov)], {"a.py"}, ignore=["x/"])

    assert resolved.provenance.scope == _scope("lcov", "lcov", ignore=["x/"])


# -- coverage outside the change ---------------------------------------------

# Two lines inserted above line 3 of src/a.py (old 3.. become new 5..), and
# line 7 rewritten.
_DIFF = """\
--- a/src/a.py
+++ b/src/a.py
@@ -2,0 +3,2 @@
+x = 1
+y = 2
@@ -7 +9 @@
-old
+new
"""


def _scan(base, head, diff=_DIFF, renames=None, deleted=()):
    return indirect_changes(base, head, parse_unified_diff(diff), renames or {}, deleted)


def _rows(*args, **kwargs):
    return list(_scan(*args, **kwargs).rows)


def test_lines_move_through_the_diff_before_they_are_compared() -> None:
    base = {"src/a.py": _fc("src/a.py", [1, 3, 4, 7], [1, 3, 4, 7, 8])}
    # At the head, old line 3 is line 5 and old 4 is 6: still covered. Old 8
    # (new 10) is newly covered. Old 7 was rewritten (new 9): the change's own.
    head = {"src/a.py": _fc("src/a.py", [1, 5, 6, 10], [1, 3, 4, 5, 6, 9, 10])}

    (row,) = _rows(base, head)

    assert row.newly_uncovered_ranges == ()
    assert row.newly_covered_line_count == 1
    assert row.status == "changed"


def test_touched_lines_are_left_to_patch_coverage() -> None:
    base = {"src/a.py": _fc("src/a.py", [7], [7])}
    head = {"src/a.py": _fc("src/a.py", [], [9])}
    assert _rows(base, head) == []


def test_a_line_that_lost_coverage_is_named_at_its_head_position() -> None:
    base = {"src/a.py": _fc("src/a.py", [3, 4], [3, 4])}
    head = {"src/a.py": _fc("src/a.py", [], [5, 6])}

    (row,) = _rows(base, head)

    assert row.newly_uncovered_ranges == ((5, 6),)
    assert (row.base_pct, row.head_pct) == (100.0, 0.0)


def test_an_untouched_file_maps_line_for_line() -> None:
    base = {"lib/b.py": _fc("lib/b.py", [1, 2, 3], [1, 2, 3])}
    head = {"lib/b.py": _fc("lib/b.py", [1], [1, 2, 3])}

    (row,) = _rows(base, head)

    assert (row.file_path, row.newly_uncovered_ranges) == ("lib/b.py", ((2, 3),))


def test_a_renamed_file_is_compared_under_its_new_path() -> None:
    base = {"old/b.py": _fc("old/b.py", [1, 2], [1, 2])}
    head = {"new/b.py": _fc("new/b.py", [1], [1, 2])}

    (row,) = _rows(base, head, renames={"old/b.py": "new/b.py"})

    assert (row.file_path, row.newly_uncovered_ranges) == ("new/b.py", ((2, 2),))


def test_files_the_head_report_no_longer_measures() -> None:
    base = {
        "gone.py": _fc("gone.py", [1], [1]),
        "moved.py": _fc("moved.py", [1], [1]),
        "dropped.py": _fc("dropped.py", [1], [1, 2]),
        "empty.py": _fc("empty.py", [], []),
    }
    rows = _rows(base, {}, renames={"moved.py": "elsewhere.py"}, deleted={"gone.py"})

    # Deleted files are the change's own; a renamed one is reported under its
    # new path; a file with no coverable line says nothing.
    assert [(r.file_path, r.status, r.base_pct, r.head_pct) for r in rows] == [
        ("dropped.py", "no_longer_measured", 50.0, None),
        ("elsewhere.py", "no_longer_measured", 100.0, None),
    ]


def test_most_newly_uncovered_lines_first() -> None:
    base = {p: _fc(p, [1, 2, 3], [1, 2, 3]) for p in ("x.py", "y.py", "z.py")}
    head = {
        "x.py": _fc("x.py", [1, 2], [1, 2, 3]),
        "y.py": _fc("y.py", [], [1, 2, 3]),
        "z.py": _fc("z.py", [1, 2, 3], [1, 2, 3]),
    }
    assert [r.file_path for r in _rows(base, head)] == ["y.py", "x.py"]


_SHIFT = """\
--- a/src/a.py
+++ b/src/a.py
@@ -1,0 +2,3 @@
+x = 1
+y = 2
+z = 3
"""


def test_a_base_report_measured_at_the_head_is_left_out_with_a_reason() -> None:
    # The same line data on both sides through a diff that shifts every line:
    # the base report was measured at the head, not at the base commit.
    same = _fc("src/a.py", [2, 3, 6, 9], [2, 3, 4, 5, 6, 9])
    untouched = _fc("lib/b.py", [1, 2], [1, 2, 3])

    scan = _scan(
        {"src/a.py": same, "lib/b.py": untouched},
        {"src/a.py": same, "lib/b.py": untouched},
        diff=_SHIFT,
    )

    assert scan.rows == ()
    assert scan.misaligned == ("src/a.py",)
    assert scan.note("a1b2c3d4e5") == (
        "the base report does not line up with a1b2c3d in 1 file; measure it at the base commit"
    )


def test_a_base_report_that_lines_up_nowhere_keeps_no_rows() -> None:
    base = {"src/a.py": _fc("src/a.py", [1, 2, 3, 4], [1, 2, 3, 4])}
    # Nothing coverable where the base's lines land: the report is from elsewhere.
    head = {"src/a.py": _fc("src/a.py", [], [20, 21, 22, 23])}

    scan = _scan(base, head, diff="")

    assert scan.rows is None
    assert scan.misaligned == ("src/a.py",)


def test_a_few_stray_lines_do_not_make_a_file_misaligned() -> None:
    base = {"lib/b.py": _fc("lib/b.py", list(range(1, 31)), list(range(1, 31)))}
    # Two lines the head no longer counts: under the minimum, the file is kept.
    head = {"lib/b.py": _fc("lib/b.py", list(range(1, 28)), list(range(1, 29)))}

    scan = _scan(base, head, diff="")

    assert scan.misaligned == ()
    assert [r.newly_uncovered_ranges for r in scan.rows] == [((28, 28),)]


# -- causes and rendering ------------------------------------------------------


def test_name_pairing_finds_the_deleted_test_for_a_file() -> None:
    causes = name_causes(
        ["src/auth.py", "src/other.py"],
        {"tests/test_auth.py", "src/cli.py"},
        {"tests/test_auth.py"},
    )
    assert causes == {
        "src/auth.py": (IndirectCause("test_deleted", "tests/test_auth.py", "name"),),
        "src/other.py": (),
    }


def _with_project(rows, **kwargs):
    coverage = {"a.py": _fc("a.py", [1], [1])}
    pc = compute_patch_coverage({"a.py": {1}}, coverage)
    project = _delta((800, 1000), (797, 1000), outside_change=rows, **kwargs)
    return replace(pc, project=project)


def test_markdown_carries_the_project_line_and_the_outside_table() -> None:
    lost = IndirectChange("src/auth.py", ((4, 6),), 0, 90.0, 60.0, "changed")
    rows = apply_causes(
        [lost], {"src/auth.py": (IndirectCause("test_deleted", "tests/test_auth.py", "name"),)}
    )
    md = render_markdown(_with_project(rows, max_drop=0.1, base_commit="a1b2c3d4e5"))

    assert (
        "**Project coverage 79.7%** · down 0.30 points from 80.0% at a1b2c3d · "
        "falls more than the 0.1-point max-drop gate allows"
    ) in md
    assert "### Coverage outside the change" in md
    row = "| `src/auth.py` | 90.0% | 60.0% | 4-6 | deleted test tests/test_auth.py (by name) |"
    assert row in md


def test_markdown_says_why_files_were_left_out() -> None:
    md = render_markdown(_with_project(None, outside_change_note="it does not line up"))
    assert "Files left out: it does not line up." in md
    assert "| File |" not in md


def test_markdown_caps_the_outside_table() -> None:
    rows = tuple(
        IndirectChange(f"f{i:02}.py", ((1, 1),), 0, 100.0, 0.0, "changed") for i in range(12)
    )
    md = render_markdown(_with_project(rows))
    assert "f09.py" in md and "f10.py" not in md
    assert "and 2 more files." in md


def test_project_line_words() -> None:
    same = _with_project(None)

    unchanged = replace(same, project=_delta((80, 100), (80, 100), max_drop=0.5))
    assert project_line(unchanged, markdown=False) == (
        "Project coverage 80.0% · unchanged from 80.0% · within the 0.5-point max-drop gate"
    )
    incomparable = replace(same, project=_delta((80, 100), (70, 100), incomparable=("a", "b")))
    assert project_line(incomparable, markdown=False) == "Project coverage not compared: a; b."
    assert project_line(replace(same, project=None)) == ""


def test_github_warns_per_file_that_lost_coverage_and_errors_on_the_gate() -> None:
    rows = (
        IndirectChange("src/a.py", ((12, 14),), 0, 90.0, 60.0, "changed"),
        IndirectChange("src/b.py", (), 3, 50.0, 80.0, "changed"),
    )
    lines = github_annotations(_with_project(rows, max_drop=0.1))

    errors = [line for line in lines if line.startswith("::error")]
    warnings = [line for line in lines if "Coverage lost outside the change" in line]
    assert len(errors) == 1 and "Project coverage 79.7%" in errors[0]
    assert len(warnings) == 1 and "file=src/a.py,line=12" in warnings[0]


def test_a_file_no_longer_measured_is_a_file_level_warning() -> None:
    rows = (IndirectChange("src/gone.py", (), 0, 80.0, None, "no_longer_measured"),)
    lines = github_annotations(_with_project(rows))

    (warning,) = [line for line in lines if "Coverage lost outside the change" in line]
    assert warning.startswith("::warning file=src/gone.py,title=")
    assert "line=" not in warning
    # Workflow commands escape % as %25.
    assert "Measured at the base (80.0%25), not in the head's coverage report" in warning


def test_ranges_and_outside_warnings_share_one_annotation_budget() -> None:
    # 12 uncovered ranges in the change, 2 files losing coverage outside it.
    covered = []
    coverable = list(range(1, 25, 2))
    pc = compute_patch_coverage({"a.py": set(coverable)}, {"a.py": _fc("a.py", covered, coverable)})
    rows = (
        IndirectChange("x.py", ((1, 1),), 0, 100.0, 0.0, "changed"),
        IndirectChange("y.py", ((2, 2),), 0, 100.0, 0.0, "changed"),
    )
    lines = github_annotations(replace(pc, project=_delta((1, 1), (1, 1), outside_change=rows)))

    ranges = [line for line in lines if "title=Uncovered change::" in line]
    outside = [line for line in lines if "Coverage lost outside the change" in line]
    notices = [line for line in lines if line.startswith("::notice")]
    assert (len(ranges), len(outside)) == (8, 2)
    assert notices == ["::notice::4 more warnings; the first 10 are listed in the job summary"]
