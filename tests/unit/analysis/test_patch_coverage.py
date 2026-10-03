"""Patch coverage: the pure compute and its renderings."""

from __future__ import annotations

from dataclasses import replace

from repowise.core.analysis.changed_lines import line_ranges
from repowise.core.analysis.health.coverage import PathGate, file_coverage
from repowise.core.analysis.patch_coverage import (
    FileRisk,
    GitFixHistory,
    IndexFacts,
    PatchScope,
    assess_risks,
    attach_hints,
    attach_risk,
    attention_rows,
    build_hints,
    compute_patch_coverage,
    fmt_pct,
    github_annotations,
    headline,
    hint_phrase,
    render_markdown,
    risky_line,
)
from repowise.core.analysis.patch_coverage.hints import SymbolSpan, TestHint, translate_spans
from repowise.core.analysis.test_reachability import ReachedBy


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


def test_small_change_tolerance() -> None:
    # 1 of 2 changed executable lines covered: 50%.
    coverage = _cov("a.py", covered=[1], coverable=[1, 2])
    changed = {"a.py": {1, 2}}

    def gate(threshold, min_lines):
        return compute_patch_coverage(
            changed, coverage, threshold=threshold, min_coverable_lines=min_lines
        ).gate

    assert gate(80, 5) == "too_small"
    # At the minimum the change is big enough to judge.
    assert gate(80, 2) == "fail"
    assert gate(50, 2) == "pass"
    # A pass below the minimum stays a pass; no threshold or no data wins first.
    assert gate(50, 5) == "pass"
    assert gate(None, 5) == "not_set"
    assert compute_patch_coverage(
        {"a.py": {9}}, coverage, threshold=80, min_coverable_lines=5
    ).gate == "no_data"


def test_too_small_headline_says_the_gate_was_not_applied() -> None:
    coverage = _cov("a.py", covered=[1], coverable=[1, 2])
    pc = compute_patch_coverage({"a.py": {1, 2}}, coverage, threshold=80, min_coverable_lines=5)

    text = headline(pc, markdown=False)
    assert text == (
        "Patch coverage 50.0% (1 of 2 changed executable lines covered) · below the 80.0% "
        "gate, not applied: fewer than 5 changed executable lines (min_coverable_lines)"
    )
    assert pc.to_dict()["min_coverable_lines"] == 5
    lines = github_annotations(pc)
    assert not any(line.startswith("::error") for line in lines)
    # The exemption is visible, not silent.
    assert lines[0].startswith("::notice::Patch coverage 50.0")
    assert "min_coverable_lines" in lines[0]


def test_ignored_changed_files_are_dropped_and_counted() -> None:
    coverage = _cov("src/a.py", covered=[1], coverable=[1])
    pc = compute_patch_coverage(
        {"src/a.py": {1}, "src/gen/api_pb2.py": {1, 2}, "src/b.py": set()},
        coverage,
        ignore=["**/gen/"],
    )

    assert [f.file_path for f in pc.files] == ["src/a.py"]
    assert pc.scope.ignored_file_count == 1
    assert pc.to_dict()["scope"]["ignored_file_count"] == 1
    assert "1 ignored by coverage.ignore" in render_markdown(pc)
    only_ignored = compute_patch_coverage({"src/gen/x.py": {1}}, coverage, ignore=["src/gen"])
    assert "Every changed file is ignored by coverage.ignore" in headline(only_ignored)


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
    assert lines[0].startswith("::error::Patch coverage")
    assert lines[-1] == "::notice::3 more warnings; the first 10 are listed in the job summary"


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


# ---------------------------------------------------------------------------
# Risk: which files are risky, the order rows read in, the risky-file gate
# ---------------------------------------------------------------------------


def _git_history(pressure: dict[str, float]) -> GitFixHistory:
    # As read_git_fix_history builds it: the files with any fix history.
    return GitFixHistory(pressure, tuple(sorted(v for v in pressure.values() if v > 0)))


def test_git_only_risk_is_the_top_quartile_of_files_with_fix_history() -> None:
    # Many files fixed once or twice, one fixed a lot: only that one is risky,
    # however many never-fixed files the repository also has.
    pressure = {f"f{i}.py": 1.0 + i / 10 for i in range(8)} | {"hot.py": 6.0}
    risks = assess_risks(["hot.py", "f0.py", "f7.py", "cold.py"], _git_history(pressure), {})

    assert risks["hot.py"].risky
    assert risks["hot.py"].reasons == ("top quartile of files with bug-fix history",)
    # f7 ranks 7 of 8 others (0.875): top quartile. f0 has a fix but ranks last.
    assert risks["f7.py"].risky
    assert not risks["f0.py"].risky
    # Zero pressure is never risky.
    assert not risks["cold.py"].risky
    assert {r.basis for r in risks.values()} == {"git"}
    assert risks["cold.py"].dependents is None and risks["cold.py"].hotspot is None


def test_one_file_with_fix_history_ranks_nothing() -> None:
    risks = assess_risks(["a.py"], _git_history({"a.py": 3.0}), {})

    assert not risks["a.py"].risky


def test_history_ref_reads_before_the_change() -> None:
    from repowise.core.analysis.change_risk.service import history_ref

    assert history_ref(".", "abc123") == "abc123^"
    assert history_ref(".", None) == "HEAD^"
    assert history_ref(".", None, working_tree=True) == "HEAD"
    assert history_ref(".", "main...HEAD", working_tree=True) == "HEAD"


def test_index_rows_use_the_index_flags_and_a_new_file_falls_back_to_git() -> None:
    git = _git_history({"hot.py": 9.0, "a.py": 1.0, "new.py": 0.0})
    index = {
        "hot.py": IndexFacts(hotspot=False, bug_magnet=False, dependents=3),
        "flagged.py": IndexFacts(hotspot=True, bug_magnet=True, dependents=14),
    }
    risks = assess_risks(["hot.py", "flagged.py", "new.py"], git, index)

    # With index data the index decides, even against a high git pressure.
    assert not risks["hot.py"].risky and risks["hot.py"].basis == "git_and_index"
    assert risks["flagged.py"].reasons == ("hotspot", "bug magnet")
    assert risks["new.py"].basis == "git"
    # Git unreadable: index rows keep their flags, the rest are unavailable.
    no_git = assess_risks(["flagged.py", "new.py"], None, index)
    assert no_git["flagged.py"].basis == "index" and no_git["flagged.py"].risky
    assert no_git["new.py"] == FileRisk()


def _risk(risky: bool, pressure: float = 0.0, dependents: int | None = None) -> FileRisk:
    return FileRisk(
        fix_pressure=pressure,
        dependents=dependents,
        basis="git",
        risky=risky,
        reasons=("hotspot",) if risky else (),
    )


def _three_files():
    coverage = {
        **_cov("big.py", covered=[], coverable=[1, 2, 3, 4, 5]),
        **_cov("fixed.py", covered=[], coverable=[1, 2]),
        **_cov("risky.py", covered=[1], coverable=[1, 2]),
    }
    changed = {"big.py": {1, 2, 3, 4, 5}, "fixed.py": {1, 2}, "risky.py": {1, 2}}
    risks = {
        "big.py": _risk(False),
        "fixed.py": _risk(False, pressure=2.0),
        "risky.py": _risk(True, pressure=1.0, dependents=4),
    }
    return compute_patch_coverage(changed, coverage), risks


def test_rows_and_annotations_read_riskiest_first() -> None:
    pc, risks = _three_files()
    pc = attach_risk(pc, risks)

    assert [f.file_path for f in attention_rows(pc)] == ["risky.py", "fixed.py", "big.py"]
    warnings = [line for line in github_annotations(pc) if line.startswith("::warning")]
    assert "file=risky.py" in warnings[0]
    assert "title=Uncovered change in a risky file" in warnings[0]
    assert "(hotspot, bug-fix weight 1.0, 4 dependents)" in warnings[0]
    assert "title=Uncovered change::" in warnings[1]


def test_markdown_names_each_files_risk_in_words() -> None:
    pc, risks = _three_files()
    md = render_markdown(attach_risk(pc, risks))

    assert "| File | Risk | Uncovered changed lines | Covered |" in md
    assert "| `risky.py` | hotspot, bug-fix weight 1.0, 4 dependents | 2 | 1 of 2 |" in md
    assert "| `big.py` | none known | 1-5 | 0 of 5 |" in md
    assert "Risk is from git bug-fix history alone" in md


def test_unreadable_risk_reads_unknown_and_the_basis_says_so() -> None:
    pc, risks = _three_files()
    md = render_markdown(attach_risk(pc, {**risks, "big.py": FileRisk()}))

    assert "| `big.py` | unknown | 1-5 | 0 of 5 |" in md
    assert "risk could not be read for 1 file" in md


def test_the_risky_gate_fails_on_risky_files_alone() -> None:
    pc, risks = _three_files()
    pc = replace(pc, threshold=10)

    strict = attach_risk(pc, risks, risky_threshold=80)
    assert strict.flat_gate == "pass"  # 1 of 9 lines is 11.1%, above 10%
    assert strict.risky_gate == "fail"  # 1 of 2 risky lines is 50%
    assert strict.gate == "fail"
    assert strict.to_dict()["risky"] == {
        "file_count": 1,
        "covered_line_count": 1,
        "coverable_line_count": 2,
        "patch_coverage_pct": 50.0,
        "threshold": 80,
        "gate": "fail",
    }
    assert "meets the 10.0% gate" in headline(strict)
    assert "below the 80.0% risky-file gate" in risky_line(strict)
    errors = [line for line in github_annotations(strict) if line.startswith("::error")]
    assert len(errors) == 1 and "risky-file gate" in errors[0]

    assert attach_risk(pc, risks, risky_threshold=50).gate == "pass"
    # Only the risky gate set: its verdict is the verdict.
    assert attach_risk(replace(pc, threshold=None), risks, risky_threshold=50).gate == "pass"


def test_no_risky_file_is_no_data_not_a_failure() -> None:
    pc, risks = _three_files()
    calm = {path: _risk(False) for path in risks}

    gated = attach_risk(replace(pc, threshold=10), calm, risky_threshold=90)
    assert gated.risky_gate == "no_data"
    assert gated.gate == "pass"
    assert "risky-file gate was not applied" in risky_line(gated)


def test_the_risky_gate_joins_path_gates_and_the_small_change_tolerance() -> None:
    pc, risks = _three_files()  # 9 changed executable lines, 2 of them risky
    passing_gate = [PathGate("all", ("*.py",), 10)]
    gated = compute_patch_coverage(
        {f.file_path: set(range(1, f.coverable_line_count + 1)) for f in pc.files},
        {
            **_cov("big.py", covered=[], coverable=[1, 2, 3, 4, 5]),
            **_cov("fixed.py", covered=[], coverable=[1, 2]),
            **_cov("risky.py", covered=[1], coverable=[1, 2]),
        },
        gates=passing_gate,
    )

    risky_fail = attach_risk(gated, risks, risky_threshold=80)
    assert [g.gate for g in risky_fail.path_gates] == ["pass"]
    assert (risky_fail.flat_gate, risky_fail.risky_gate, risky_fail.gate) == (
        "not_set",
        "fail",
        "fail",
    )
    # Under the whole change's tolerance the risky gate is exempt, as path gates are.
    small = attach_risk(replace(gated, min_coverable_lines=20), risks, risky_threshold=80)
    assert (small.risky_gate, small.gate) == ("too_small", "too_small")
    assert "risky-file gate, not applied" in risky_line(small)
    notices = [line for line in github_annotations(small) if line.startswith("::notice")]
    assert any("Risky files" in line for line in notices)


def test_without_risk_the_flat_gate_and_shape_are_unchanged() -> None:
    pc, _ = _three_files()
    d = pc.to_dict()

    assert d["risky"] is None
    assert all(f["risk"] is None for f in d["files"])
    assert "| File | Uncovered changed lines | Covered |" in render_markdown(pc)


def test_serialized_percentage_is_floored_not_rounded() -> None:
    coverable = list(range(1, 40001))
    pc = compute_patch_coverage(
        {"a.py": set(coverable)}, _cov("a.py", coverable[:31999], coverable), threshold=80
    )
    assert pc.to_dict()["patch_coverage_pct"] == 79.99


# -- path-scoped gates -------------------------------------------------------


def _two_packages():
    """``api`` is 1 of 4 covered, ``web`` 4 of 4: 5 of 8 (62.5%) over the change."""
    coverage = {
        **_cov("api/a.py", covered=[1], coverable=[1, 2, 3, 4]),
        **_cov("web/b.py", covered=[1, 2, 3, 4], coverable=[1, 2, 3, 4]),
    }
    return {"api/a.py": {1, 2, 3, 4}, "web/b.py": {1, 2, 3, 4}}, coverage


def test_path_gate_judges_only_its_files() -> None:
    changed, coverage = _two_packages()
    pc = compute_patch_coverage(
        changed,
        coverage,
        threshold=50,
        gates=[PathGate("api", ("/api/",), 80), PathGate("web", ("web/**",), 80)],
    )

    api, web = pc.path_gates
    assert (api.measured_file_count, api.covered_line_count, api.coverable_line_count) == (1, 1, 4)
    assert (api.patch_coverage_pct, api.gate) == (25.0, "fail")
    assert (web.patch_coverage_pct, web.gate) == (100.0, "pass")
    # The whole change meets its own gate; the failing path gate fails it.
    assert pc.flat_gate == "pass"
    assert pc.gate == "fail"
    assert pc.to_dict()["gate"] == "fail"


def test_informational_path_gate_never_fails_the_change() -> None:
    changed, coverage = _two_packages()
    pc = compute_patch_coverage(
        changed, coverage, gates=[PathGate("api", ("/api/",), 80, informational=True)]
    )

    assert pc.path_gates[0].gate == "fail"
    assert pc.gate == "not_set"
    lines = github_annotations(pc)
    assert not any(line.startswith("::error") for line in lines)
    # Below its threshold is still said, as a notice.
    assert lines[0] == (
        "::notice::Informational: path-scoped gate api 25.0%25 (1 of 4 changed executable "
        "lines), below its 80.0%25 gate."
    )
    assert not headline(pc).startswith("Fails")


def test_a_file_can_count_in_several_path_gates() -> None:
    changed, coverage = _two_packages()
    pc = compute_patch_coverage(
        changed,
        coverage,
        gates=[PathGate("all", ("*.py",), 50), PathGate("api", ("api/",), 20)],
    )

    everything, api = pc.path_gates
    assert everything.measured_file_count == 2
    assert (everything.covered_line_count, everything.coverable_line_count) == (5, 8)
    assert api.measured_file_count == 1
    # Passing path gates fail nothing, and set no whole-change verdict either.
    assert pc.gate == "not_set"


def test_path_gate_without_files_or_threshold() -> None:
    changed, coverage = _two_packages()
    pc = compute_patch_coverage(
        changed,
        coverage,
        threshold=10,
        gates=[PathGate("docs", ("/docs/",), 80), PathGate("api", ("/api/",))],
    )

    docs, api = pc.path_gates
    assert docs.measured_file_count == 0
    assert docs.patch_coverage_pct is None
    assert docs.gate == "no_data"
    assert docs.to_dict()["patch_coverage_pct"] is None
    assert api.gate == "not_set"
    assert pc.gate == "pass"


def test_path_gate_counts_its_unmeasured_files() -> None:
    coverage = _cov("src/a.py", covered=[1], coverable=[1])
    pc = compute_patch_coverage(
        {"src/a.py": {1}, "new/x.py": {1}, "new/y.py": {2}},
        coverage,
        gates=[PathGate("new", ("/new/",), 80)],
    )

    (gate,) = pc.path_gates
    assert (gate.measured_file_count, gate.unmeasured_file_count, gate.gate) == (0, 2, "no_data")
    assert "| `new` | no measured changed lines (2 changed files not measured) | n/a | 80.0% |" in (
        render_markdown(pc)
    )


def test_small_change_tolerance_is_the_whole_changes() -> None:
    changed, coverage = _two_packages()
    gates = [PathGate("api", ("/api/",), 80)]

    # A 4-line slice of an 8-line change is not a small change.
    big = compute_patch_coverage(changed, coverage, min_coverable_lines=5, gates=gates)
    assert big.path_gates[0].gate == "fail"
    assert big.gate == "fail"
    # The whole change under the minimum exempts every gate.
    small = compute_patch_coverage(changed, coverage, min_coverable_lines=10, gates=gates)
    assert small.path_gates[0].gate == "too_small"
    assert small.gate == "not_set"


def test_unjudged_path_gates_read_no_data_but_keep_their_counts() -> None:
    changed, coverage = _two_packages()
    pc = compute_patch_coverage(
        changed, coverage, gates=[PathGate("api", ("/api/",), 80)], judge_gates=False
    )

    (gate,) = pc.path_gates
    assert (gate.gate, gate.coverable_line_count) == ("no_data", 4)
    assert pc.gate == "not_set"
    assert "| `api` | not judged | 1 of 4 (25.0%) | 80.0% |" in render_markdown(pc)


def test_path_gate_pct_floors_like_the_whole_change() -> None:
    coverage = _cov("a.py", covered=[1, 2], coverable=[1, 2, 3])
    pc = compute_patch_coverage(
        {"a.py": {1, 2, 3}}, coverage, gates=[PathGate("a", ("a.py",), 66.67)]
    )

    (gate,) = pc.path_gates
    # 66.666...% is below 66.67% unrounded, and serialises floored.
    assert gate.gate == "fail"
    assert gate.to_dict()["patch_coverage_pct"] == 66.66


def test_path_gate_to_dict_shape() -> None:
    changed, coverage = _two_packages()
    pc = compute_patch_coverage(changed, coverage, gates=[PathGate("api", ("/api/",), 80)])

    assert pc.to_dict()["path_gates"] == [
        {
            "name": "api",
            "paths": ["/api/"],
            "threshold": 80,
            "informational": False,
            "measured_file_count": 1,
            "unmeasured_file_count": 0,
            "covered_line_count": 1,
            "coverable_line_count": 4,
            "patch_coverage_pct": 25.0,
            "gate": "fail",
        }
    ]
    assert pc.to_dict()["scope"]["config_errors"] == []
    assert compute_patch_coverage(changed, coverage).to_dict()["path_gates"] == []


def test_failing_path_gate_renders_in_headline_markdown_and_annotations() -> None:
    changed, coverage = _two_packages()
    pc = compute_patch_coverage(
        changed,
        coverage,
        threshold=50,
        gates=[
            PathGate("api", ("/api/",), 80),
            PathGate("web", ("/web/",), 80),
            PathGate("docs", ("/docs/",), 80, informational=True),
        ],
    )

    # The failure leads, with its counts; the whole-change verdict follows.
    assert headline(pc, markdown=False) == (
        "Fails: path-scoped gate api 25.0% (1 of 4 changed executable lines), below its "
        "80.0% gate. Patch coverage 62.5% (5 of 8 changed executable lines covered) "
        "· meets the 50.0% gate"
    )
    md = render_markdown(pc)
    assert "| Path-scoped gate | Verdict | Covered changed lines | Threshold |" in md
    assert "| `api` | fails | 1 of 4 (25.0%) | 80.0% |" in md
    assert "| `web` | passes | 4 of 4 (100.0%) | 80.0% |" in md
    assert "| `docs` | no measured changed lines (informational) | n/a | 80.0% |" in md
    errors = [line for line in github_annotations(pc) if line.startswith("::error")]
    assert errors == [
        "::error::Fails: path-scoped gate api 25.0%25 (1 of 4 changed executable lines), "
        "below its 80.0%25 gate."
    ]


def test_path_gate_table_is_capped_with_failing_gates_first() -> None:
    changed, coverage = _two_packages()
    gates = [PathGate(f"g{i}", ("/api/",)) for i in range(12)]
    gates.append(PathGate("late", ("/api/",), 80))
    md = render_markdown(compute_patch_coverage(changed, coverage, gates=gates))

    # The failing gate sits past the cap in config, but leads the table.
    rows = [line for line in md.splitlines() if line.startswith("| `")]
    assert rows[0].startswith("| `late` | fails")
    assert "| `g8` |" in md
    assert "| `g9` |" not in md
    assert "and 3 more path-scoped gates." in md


def test_no_path_gate_table_without_gates() -> None:
    changed, coverage = _two_packages()
    assert "Path-scoped" not in render_markdown(compute_patch_coverage(changed, coverage))


# -- suggested gates ---------------------------------------------------------


def test_community_gates_name_by_common_directory_and_skip_tiny_ones() -> None:
    from repowise.core.analysis.patch_coverage.suggest import COMMUNITY_LIMIT, community_gates

    communities = {
        "svc/api/a.py": 1,
        "svc/api/deep/b.py": 1,
        "svc/web/c.py": 1,
        "svc/web/d.py": 1,
        "svc/api/test_a.py": 1,  # a test: not a source file
        "main.py": 2,
        "lib/x.py": 2,
        "lib/y.py": 2,
        "tiny/one.py": 3,
    }
    # Enough three-file communities to overflow the limit.
    for i in range(COMMUNITY_LIMIT):
        communities.update({f"pkg{i:02}/{n}.py": 100 + i for n in "abc"})

    gates, cut = community_gates(communities)

    by_name = {g.name: g.paths for g in gates}
    assert len(gates) == COMMUNITY_LIMIT
    assert cut == 2
    # Nested directories collapse to the outermost; the name is the common one.
    assert by_name["svc"] == ("/svc/api/", "/svc/web/")
    assert "tiny" not in by_name


def test_community_gates_keep_repository_root_files() -> None:
    from repowise.core.analysis.patch_coverage.suggest import community_gates

    communities = {"main.py": 1, "lib/x.py": 1, "lib/y.py": 1}
    gates, _ = community_gates(communities, {"main.py": 900, "lib/x.py": 100})

    # Rooted at the repository root: named after its largest file.
    assert [(g.name, g.paths) for g in gates] == [("main", ("/main.py", "/lib/"))]


def test_community_gates_on_a_flat_layout_claim_each_directory_once() -> None:
    from repowise.core.analysis.patch_coverage.suggest import (
        GateSource,
        community_gates,
        unique_names,
    )

    # A flat Go module: loose root files, one shared doc/ directory.
    communities = {
        "command.go": 1,
        "help.go": 1,
        "run.go": 1,
        "doc/man.go": 1,
        "doc/md.go": 1,
        "args.go": 2,
        "shell.go": 2,
        "complete.go": 2,
        "doc/rest.go": 2,
        "cobra.go": 3,
        "doc/util.go": 3,
        "doc/yaml.go": 3,
    }
    sizes = {"command.go": 5000, "complete.go": 3000, "cobra.go": 800}

    gates, cut = community_gates(communities, sizes)

    assert [(g.name, g.paths) for g in gates] == [
        ("command", ("/command.go", "/help.go", "/run.go", "/doc/")),
        # doc/ is the first gate's; three loose files are enough on their own.
        ("complete", ("/args.go", "/complete.go", "/shell.go")),
    ]
    # The third keeps one loose file once doc/ is claimed: too few, dropped.
    assert cut == 0
    source = GateSource("graph", "the index", gates)
    unique_names([source])
    assert len({g.name for g in source.gates}) == len(source.gates)


def test_layout_gates_name_clashing_packages_by_their_root() -> None:
    from repowise.core.analysis.patch_coverage.suggest import layout_gates

    gates = layout_gates(["apps/web/a.ts", "packages/web/b.ts", "packages/core/c.py", "x.py"])

    assert [(g.name, g.paths) for g in gates] == [
        ("apps-web", ("/apps/web/",)),
        ("core", ("/packages/core/",)),
        ("packages-web", ("/packages/web/",)),
    ]


def test_suggested_yaml_pastes_below_the_coverage_line() -> None:
    import yaml

    from repowise.core.analysis.patch_coverage.suggest import (
        GateSource,
        SuggestedGate,
        render_yaml,
    )

    empty = render_yaml([GateSource("codeowners", "no CODEOWNERS file")])
    assert yaml.safe_load("coverage:\n" + empty) == {"coverage": {"gates": []}}
    assert "    # From CODEOWNERS: no CODEOWNERS file" in empty
    text = render_yaml([GateSource("layout", "git ls-files", [SuggestedGate("api", ("/api/",))])])
    assert "directly below your `coverage:` line" in text
    assert yaml.safe_load("coverage:\n  fail_under: 80\n" + text) == {
        "coverage": {"fail_under": 80, "gates": [{"name": "api", "paths": ["/api/"]}]}
    }


# -- CODEOWNERS ----------------------------------------------------------------


def _owners(text: str) -> dict[str, tuple[str, ...]]:
    from repowise.core.analysis.patch_coverage.suggest import codeowners_gates

    return {g.name: g.paths for g in codeowners_gates(text)}


def test_codeowners_later_narrower_pattern_is_excluded() -> None:
    assert _owners("/pkg/ @a\n/pkg/web/ @b\n") == {"a": ("/pkg/", "!/pkg/web/"), "b": ("/pkg/web/",)}


def test_codeowners_later_broader_pattern_overrides_and_drops_the_gate() -> None:
    # /pkg/ @b wins every file /pkg/api/ matched, so @a has no gate left.
    assert _owners("/pkg/api/ @a\n/pkg/ @b\n") == {"b": ("/pkg/",)}
    # Only partly overridden: the exclusion stays.
    assert _owners("/pkg/api/ @a\n/lib/ @a\n/pkg/ @b\n") == {
        "a": ("/pkg/api/", "/lib/", "!/pkg/"),
        "b": ("/pkg/",),
    }


def test_codeowners_ownerless_line_excludes_without_a_gate() -> None:
    assert _owners("/pkg/ @a\n/pkg/vendor/\n") == {"a": ("/pkg/", "!/pkg/vendor/")}


def test_codeowners_trailing_catch_all_drops_earlier_gates() -> None:
    assert _owners("/pkg/ @a\n/lib/ @b\n* @c\n/docs/ @d\n") == {"d": ("/docs/",)}
    # A leading catch-all overrides nothing after it.
    assert _owners("* @c\n/pkg/ @a\n") == {"a": ("/pkg/",)}


def test_codeowners_escaped_spaces_tab_comments_and_emails() -> None:
    text = "/docs/my\\ file.md\t@a # the doc\n/src/\tdev@example.com\t# tab comment\n"
    assert _owners(text) == {"a": ("/docs/my\\ file.md",), "dev": ("/src/",)}


def test_codeowners_gitlab_section_default_owners() -> None:
    text = (
        "[Docs] @org/docs\n/docs/\n/guides/ @org/writers\n"
        "^[Backend][2] @org/api\n/api/\n[Plain]\n/misc/\n"
    )
    assert _owners(text) == {
        "org-docs": ("/docs/",),
        "org-writers": ("/guides/",),
        "org-api": ("/api/",),
    }


def test_may_overlap_is_symmetric_and_errs_towards_yes() -> None:
    from repowise.core.analysis.patch_coverage.suggest import _may_overlap

    assert _may_overlap("/pkg/", "/pkg/web/")
    assert _may_overlap("/pkg/web/", "/pkg/")
    assert _may_overlap("/pkg/", "*.md")  # floats: any depth
    assert _may_overlap("docs/", "/pkg/")  # floating owned pattern
    assert _may_overlap("/pkg/*/src/", "/pkg/web/")
    assert not _may_overlap("/pkg/", "/lib/")
    assert not _may_overlap("/src/a.py", "/src/b.py")
    assert not _may_overlap("!/pkg/", "/pkg/web/")
# ---------------------------------------------------------------------------
# hints: where to add a test for each uncovered range
# ---------------------------------------------------------------------------


def _gappy(ranges_lines: set[int], path: str = "src/auth.py"):
    """One measured file whose changed lines *ranges_lines* are all uncovered."""
    pc = compute_patch_coverage(
        {path: ranges_lines}, _cov(path, covered=[], coverable=sorted(ranges_lines))
    )
    return pc, pc.files[0]


_SPANS = [
    SymbolSpan("src/auth.py::Auth", "Auth", 1, 40),
    SymbolSpan("src/auth.py::Auth.login", "Auth.login", 10, 20),
    SymbolSpan("src/auth.py::Auth.logout", "Auth.logout", 22, 30),
]


def _reached(tests: list[str], via: str = "call-graph", total: int | None = None) -> ReachedBy:
    return ReachedBy(tests, via, len(tests) if total is None else total)


def test_a_hint_names_the_innermost_symbol_and_nothing_outside_one() -> None:
    _pc, f = _gappy({12, 35, 50})
    hints = build_hints(f, _SPANS, [], {})

    assert [h.symbol for h in hints] == ["Auth.login", "Auth", None]
    assert all(h.basis == "none" and h.tests == () and h.total == 0 for h in hints)


def test_per_test_coverage_beats_the_graph() -> None:
    _pc, f = _gappy({12})
    rows = [
        {"test_id": "tests/test_auth.py::test_ok", "test_file": "tests/test_auth.py",
         "covered_lines": [11, 13, 14]},
        # Lines of another symbol only: not evidence for login.
        {"test_id": "tests/test_out.py::t", "test_file": "tests/test_out.py",
         "covered_lines": [25]},
        {"test_id": "tests/test_more.py::t", "test_file": None, "covered_lines": [15]},
    ]
    reached = {"src/auth.py::Auth.login": _reached(["tests/test_graph.py"])}
    (hint,) = build_hints(f, _SPANS, rows, reached)

    assert hint.basis == "per_test"
    # Most lines of the symbol first; a row without a file is named by its id.
    assert hint.tests == ("tests/test_auth.py", "tests/test_more.py")
    assert hint.total == 2


def test_outside_a_symbol_per_test_uses_a_window_around_the_range() -> None:
    _pc, f = _gappy({50})
    near = [{"test_id": "t::a", "test_file": "tests/test_near.py", "covered_lines": [53]}]
    far = [{"test_id": "t::b", "test_file": "tests/test_far.py", "covered_lines": [60]}]

    assert build_hints(f, _SPANS, near, {})[0].tests == ("tests/test_near.py",)
    assert build_hints(f, _SPANS, far, {})[0].basis == "none"


def test_the_call_graph_answers_for_the_symbol_then_imports_for_the_file() -> None:
    _pc, f = _gappy({12, 24})
    reached = {
        "src/auth.py::Auth.login": _reached(["tests/test_login.py"]),
        "src/auth.py": _reached(["tests/test_imports.py"], via="import-graph"),
    }
    login, logout = build_hints(f, _SPANS, [], reached)

    assert (login.basis, login.tests) == ("call_graph", ("tests/test_login.py",))
    # No test reaches logout, so the file's importers answer.
    assert (logout.basis, logout.tests) == ("import_graph", ("tests/test_imports.py",))


def test_tests_are_capped_at_three_and_total_stays_honest() -> None:
    _pc, f = _gappy({12})
    many = [f"tests/test_{i}.py" for i in range(5)]
    (hint,) = build_hints(f, _SPANS, [], {"src/auth.py::Auth.login": _reached(many, total=7)})

    assert hint.tests == tuple(many[:3])
    assert hint.total == 7


def test_hints_ride_on_the_row_and_every_rendering() -> None:
    pc, f = _gappy({12, 13})
    hints = build_hints(f, _SPANS, [], {"src/auth.py::Auth.login": _reached(["tests/test_auth.py"])})
    pc = attach_hints(pc, {f.file_path: hints})

    (row,) = pc.to_dict()["files"]
    assert row["hints"] == [
        {"range": [12, 13], "symbol": "Auth.login", "tests": ["tests/test_auth.py"],
         "basis": "call_graph", "total": 1}
    ]
    assert hint_phrase(hints[0]) == "extend tests/test_auth.py (inferred: calls reach `Auth.login`)"
    markdown = render_markdown(pc)
    assert "| File | Uncovered changed lines | Covered | Extend |" in markdown
    assert "extend tests/test_auth.py (inferred: calls reach `Auth.login`)" in markdown
    (annotation,) = github_annotations(pc)
    assert "Extend tests/test_auth.py (inferred: calls reach `Auth.login`)" in annotation


def test_each_basis_says_measured_or_inferred() -> None:
    def phrase(basis, symbol="login", tests=("t.py",)):
        return hint_phrase(TestHint((1, 1), symbol, tests, basis, len(tests)))

    assert phrase("per_test") == "extend t.py (measured: runs other lines of `login`)"
    assert phrase("per_test", symbol=None) == "extend t.py (measured: runs nearby lines)"
    assert phrase("import_graph") == "extend t.py (inferred: imports this file)"
    assert phrase("none", tests=()) == "no test reaches this; add one"


def test_a_tier_the_hints_do_not_know_is_no_evidence() -> None:
    _pc, f = _gappy({12})
    reached = {"src/auth.py": _reached(["tests/test_auth.py"], via="name-match")}
    (hint,) = build_hints(f, _SPANS, [], reached)
    assert (hint.basis, hint.tests, hint.total) == ("none", (), 0)


def test_spans_move_with_the_code_since_the_index() -> None:
    # Five lines inserted at the top shift every symbol down by five.
    shifted = translate_spans(_SPANS, [(0, 0, 1, 5)])
    assert [(s.start_line, s.end_line) for s in shifted] == [(6, 45), (15, 25), (27, 35)]

    # A new top-level function inserted above Auth (new lines 1-8): its range
    # lies in no span, so it has no symbol; the old method keeps its own.
    moved = translate_spans(_SPANS, [(0, 0, 1, 8)])
    _pc, f = _gappy({3, 19})
    new, old = build_hints(f, moved, [], {})
    assert new.symbol is None
    assert old.symbol == "Auth.login"

    # A symbol the diff deleted outright is dropped.
    assert translate_spans([SymbolSpan("a::gone", "gone", 5, 6)], [(5, 2, 4, 0)]) == []


def test_without_an_index_hints_are_null_and_nothing_changes() -> None:
    pc, _f = _gappy({12})
    assert pc.to_dict()["files"][0]["hints"] is None
    assert "Extend" not in render_markdown(pc)
    # An index that found nothing is an empty list, not null.
    empty = attach_hints(pc, {})
    assert empty.to_dict()["files"][0]["hints"] == []
    assert "Extend" not in github_annotations(empty)[0]
    # An index that found no test says so in words.
    none = attach_hints(pc, {"src/auth.py": (TestHint((12, 12), None, (), "none", 0),)})
    assert "no test reaches this; add one" in render_markdown(none)
    assert "No test reaches this; add one" in github_annotations(none)[0]
