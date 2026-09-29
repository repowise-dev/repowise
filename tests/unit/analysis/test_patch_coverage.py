"""Patch coverage: the pure compute and its renderings."""

from __future__ import annotations

from repowise.core.analysis.changed_lines import line_ranges
from repowise.core.analysis.health.coverage import PathGate, file_coverage
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
    assert lines[-1].startswith("::notice::3 more uncovered changed ranges")


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

    gates, _ = community_gates({"main.py": 1, "lib/x.py": 1, "lib/y.py": 1})

    assert [(g.name, g.paths) for g in gates] == [("root", ("/main.py", "/lib/"))]


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
