"""Tests for coverage artifact discovery + report-path resolution.

The resolver is the make-or-break piece: real reports almost never store
repowise's canonical repo-relative POSIX key, so we must reconcile absolute
/ build-relative paths back to the indexed tree without per-report config.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.coverage import (
    CoverageConfig,
    build_coverage_map,
    discover_artifacts,
    normalize_report_path,
    resolve_reports,
)
from repowise.core.analysis.health.coverage.model import CoverageReport, FileCoverage


def _fc(path: str, pct: float = 50.0, covered: list[int] | None = None) -> FileCoverage:
    covered = covered if covered is not None else [1, 2]
    return FileCoverage(
        file_path=path,
        line_coverage_pct=pct,
        branch_coverage_pct=None,
        covered_lines=covered,
        total_coverable_lines=4,
    )


def _report(files: list[FileCoverage], fmt: str = "lcov") -> CoverageReport:
    return CoverageReport(source_format=fmt, files=files)


# ---------------------------------------------------------------------------
# normalize_report_path
# ---------------------------------------------------------------------------


def test_normalize_strips_drive_and_leading_dot_slash() -> None:
    assert normalize_report_path("C:\\repo\\src\\a.rs") == "repo/src/a.rs"
    assert normalize_report_path("./src/a.ts") == "src/a.ts"
    assert normalize_report_path("/abs/src/a.py") == "abs/src/a.py"


def test_normalize_applies_strip_and_path_prefix() -> None:
    assert normalize_report_path("build/src/a.ts", strip_prefix="build") == "src/a.ts"
    assert normalize_report_path("a.ts", path_prefix="packages/web") == "packages/web/a.ts"


# ---------------------------------------------------------------------------
# resolve_reports — the core matching logic
# ---------------------------------------------------------------------------


def test_exact_match() -> None:
    keys = {"rust/src/tts/voices.rs", "rust/src/lib.rs"}
    res = resolve_reports([_report([_fc("rust/src/tts/voices.rs")])], keys)
    assert res.matched_exact == 1
    assert res.matched_suffix == 0
    assert "rust/src/tts/voices.rs" in res.coverage_map
    assert not res.unmatched


def test_absolute_path_resolves_via_suffix() -> None:
    # cargo-llvm-cov emits absolute build paths; we must map them home.
    keys = {"rust/src/tts/voices.rs", "rust/src/lib.rs"}
    report = _report([_fc("/home/ci/work/myproj/rust/src/tts/voices.rs")])
    res = resolve_reports([report], keys)
    assert res.matched_suffix == 1
    assert "rust/src/tts/voices.rs" in res.coverage_map
    assert not res.unmatched


def test_ambiguous_basename_disambiguated_by_overlap() -> None:
    keys = {"rust/src/tts/mod.rs", "rust/src/transcribe/mod.rs"}
    report = _report([_fc("/abs/rust/src/transcribe/mod.rs")])
    res = resolve_reports([report], keys)
    assert res.matched_suffix == 1
    assert "rust/src/transcribe/mod.rs" in res.coverage_map
    assert "rust/src/tts/mod.rs" not in res.coverage_map


def test_truly_ambiguous_is_refused_not_guessed() -> None:
    # Same basename, identical trailing overlap depth -> we refuse to guess.
    keys = {"a/mod.rs", "b/mod.rs"}
    report = _report([_fc("mod.rs")])
    res = resolve_reports([report], keys)
    assert res.coverage_map == {}
    assert res.ambiguous == ["mod.rs"]


def test_unmatched_reported_not_silently_dropped() -> None:
    keys = {"src/a.ts"}
    report = _report([_fc("src/gone.ts")])
    res = resolve_reports([report], keys)
    assert res.unmatched == ["src/gone.ts"]
    assert res.matched == 0


def test_hit_wins_merge_across_reports() -> None:
    keys = {"src/a.ts"}
    r1 = _report([_fc("src/a.ts", covered=[1, 2])])
    r2 = _report([_fc("src/a.ts", covered=[2, 3])])
    res = resolve_reports([r1, r2], keys)
    entry = res.coverage_map["src/a.ts"]
    assert entry["covered_lines"] == [1, 2, 3]


def test_strip_prefix_upgrades_suffix_to_exact() -> None:
    keys = {"src/a.ts"}
    report = _report([_fc("build/src/a.ts")])
    bare = resolve_reports([report], keys)
    assert bare.matched_suffix == 1  # resolves, but only by suffix
    fixed = resolve_reports([report], keys, strip_prefix="build")
    assert fixed.matched_exact == 1  # now an exact key match


def test_path_prefix_resolves_bare_basename_ambiguity() -> None:
    # A bare basename ties across packages; path_prefix pins the package.
    keys = {"web/a.ts", "api/a.ts"}
    report = _report([_fc("a.ts")])
    bare = resolve_reports([report], keys)
    assert bare.ambiguous == ["a.ts"]
    fixed = resolve_reports([report], keys, path_prefix="web")
    assert fixed.matched_exact == 1
    assert "web/a.ts" in fixed.coverage_map


# ---------------------------------------------------------------------------
# discover_artifacts
# ---------------------------------------------------------------------------


def test_discover_finds_common_locations(tmp_path: Path) -> None:
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text("SF:a\nend_of_record\n")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "clover.xml").write_text("<coverage/>")

    found = discover_artifacts(tmp_path)
    names = {p.name for p in found}
    assert "lcov.info" in names
    # node_modules is pruned.
    assert all("node_modules" not in p.parts for p in found)


def test_discover_recursive_patterns_reach_deep_files(tmp_path: Path) -> None:
    """The ``**`` patterns keep their reach through the pruned-walk rewrite."""
    deep_cob = tmp_path / "pkg" / "reports" / "cobertura.xml"
    deep_cob.parent.mkdir(parents=True)
    deep_cob.write_text("<coverage/>")
    deep_lcov = tmp_path / "coverage" / "unit" / "deep" / "lcov.info"
    deep_lcov.parent.mkdir(parents=True)
    deep_lcov.write_text("SF:a\nend_of_record\n")
    rust = tmp_path / "target" / "llvm-cov" / "html" / "cov.lcov"
    rust.parent.mkdir(parents=True)
    rust.write_text("SF:a\nend_of_record\n")

    found = set(discover_artifacts(tmp_path))
    assert {deep_cob, deep_lcov, rust} <= found


def test_discover_finds_go_and_jacoco_defaults(tmp_path: Path) -> None:
    """A literally spelled ``build/`` is searched even though ``build`` is pruned."""
    go = tmp_path / "coverage.out"
    go.write_text("mode: set\n")
    maven = tmp_path / "target" / "site" / "jacoco" / "jacoco.xml"
    maven.parent.mkdir(parents=True)
    maven.write_text("<report/>")
    gradle = tmp_path / "build" / "reports" / "jacoco" / "test" / "jacocoTestReport.xml"
    gradle.parent.mkdir(parents=True)
    gradle.write_text("<report/>")
    # ``build`` reached only through ``**`` stays pruned.
    nested = tmp_path / "lib" / "build" / "reports" / "jacoco" / "jacocoTestReport.xml"
    nested.parent.mkdir(parents=True)
    nested.write_text("<report/>")
    below = tmp_path / "build" / "reports" / "jacoco" / "node_modules" / "x.xml"
    below.parent.mkdir(parents=True)
    below.write_text("<report/>")
    module = tmp_path / "svc" / "target" / "site" / "jacoco-aggregate" / "jacoco.xml"
    module.parent.mkdir(parents=True)
    module.write_text("<report/>")

    found = set(discover_artifacts(tmp_path))
    assert {go, maven, gradle, module} <= found
    assert nested not in found
    assert below not in found


def test_discover_root_only_patterns_stay_root_only(tmp_path: Path) -> None:
    """Literal patterns must NOT gain match-anywhere semantics: a checked-in
    fixture named ``lcov.info``/``coverage.xml`` deep in the tree is not a
    report for THIS repo."""
    (tmp_path / "lcov.info").write_text("SF:a\nend_of_record\n")
    fixture = tmp_path / "tests" / "fixtures" / "lcov.info"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("SF:b\nend_of_record\n")
    stray_xml = tmp_path / "some" / "dir" / "coverage.xml"
    stray_xml.parent.mkdir(parents=True)
    stray_xml.write_text("<coverage/>")

    found = set(discover_artifacts(tmp_path))
    assert tmp_path / "lcov.info" in found
    assert fixture not in found
    assert stray_xml not in found


def test_discover_prunes_nested_git_repos(tmp_path: Path) -> None:
    """A vendored/sibling checkout's reports belong to a different repo."""
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text("SF:a\nend_of_record\n")
    sibling = tmp_path / "vendored-repo"
    (sibling / ".git").mkdir(parents=True)
    (sibling / "reports").mkdir()
    (sibling / "reports" / "cobertura.xml").write_text("<coverage/>")

    found = discover_artifacts(tmp_path)
    assert all("vendored-repo" not in p.parts for p in found)
    assert tmp_path / "coverage" / "lcov.info" in set(found)


def test_discover_pattern_priority_order_preserved(tmp_path: Path) -> None:
    """Earlier (more canonical) patterns yield earlier results."""
    canonical = tmp_path / "coverage" / "lcov.info"
    canonical.parent.mkdir()
    canonical.write_text("SF:a\nend_of_record\n")
    late = tmp_path / "sub" / "clover.xml"
    late.parent.mkdir()
    late.write_text("<coverage/>")

    found = discover_artifacts(tmp_path)
    assert found.index(canonical) < found.index(late)


def test_discover_user_glob_override_still_works(tmp_path: Path) -> None:
    """CoverageConfig.artifacts globs route through the same expansion."""
    deep = tmp_path / "out" / "cov" / "report.info"
    deep.parent.mkdir(parents=True)
    deep.write_text("SF:a\nend_of_record\n")
    shallow = tmp_path / "reports" / "cov.xml"
    shallow.parent.mkdir()
    shallow.write_text("<coverage/>")

    assert set(discover_artifacts(tmp_path, globs=["out/**/*.info"])) == {deep}
    assert set(discover_artifacts(tmp_path, globs=["reports/*.xml"])) == {shallow}


def test_build_coverage_map_end_to_end(tmp_path: Path) -> None:
    lcov = "SF:/ci/build/src/a.ts\nDA:1,1\nDA:2,0\nend_of_record\n"
    report_path = tmp_path / "coverage" / "lcov.info"
    report_path.parent.mkdir()
    report_path.write_text(lcov)

    keys = {"src/a.ts"}
    resolved, errors = build_coverage_map(tmp_path, [report_path], keys)
    assert not errors
    assert "src/a.ts" in resolved.coverage_map
    assert resolved.coverage_map["src/a.ts"]["line_coverage_pct"] == 50.0
    assert resolved.mapping_partial is False


# ---------------------------------------------------------------------------
# mapping_partial — severe path-mapping loss (issue #1746)
# ---------------------------------------------------------------------------


def test_mapping_partial_false_when_majority_maps() -> None:
    keys = {"src/a.py", "src/b.py"}
    report = _report([_fc("src/a.py"), _fc("src/b.py"), _fc("/abs/other/c.py")])
    res = resolve_reports([report], keys)
    assert res.matched == 2
    assert res.mapping_partial is False


def test_mapping_partial_true_when_majority_unmapped() -> None:
    """Issue #1746: a report that maps 1 of 5 files is a fragment, and the
    ingest must not present its aggregate as repository coverage."""
    keys = {"src/a.py"}
    report = _report(
        [_fc("src/a.py"), _fc("/abs/one/b.py"), _fc("/abs/two/c.py"),
         _fc("/abs/three/d.py"), _fc("/abs/four/e.py")]
    )
    res = resolve_reports([report], keys)
    assert res.matched == 1
    assert len(res.unmatched) == 4
    assert res.mapping_partial is True


def test_mapping_partial_measured_on_report_files_not_matched_keys() -> None:
    """Merging the same key across reports must not shrink the denominator."""
    keys = {"src/a.py"}
    res = resolve_reports(
        [_report([_fc("src/a.py"), _fc("/abs/other/b.py")]),
         _report([_fc("src/a.py")])],
        keys,
    )
    # 3 report files, 2 matched (a.py twice, merged hit-wins) → not partial
    assert res.matched == 2
    assert res.mapping_partial is False


def test_build_coverage_map_flags_partial(tmp_path: Path) -> None:
    lcov = "SF:/ci/build/src/a.ts\nDA:1,1\nend_of_record\nSF:/ci/other/b.ts\nDA:1,1\nend_of_record\nSF:/ci/other/c.ts\nDA:1,1\nend_of_record\n"
    report_path = tmp_path / "coverage" / "lcov.info"
    report_path.parent.mkdir()
    report_path.write_text(lcov)

    keys = {"src/a.ts"}
    resolved, errors = build_coverage_map(tmp_path, [report_path], keys)
    assert not errors
    assert resolved.mapping_partial is True


# ---------------------------------------------------------------------------
# CoverageConfig
# ---------------------------------------------------------------------------


def test_coverage_config_defaults() -> None:
    cfg = CoverageConfig.from_repo_config(None)
    assert cfg.auto_discover is True
    assert cfg.paths == ()


def test_coverage_config_parses_block() -> None:
    cfg = CoverageConfig.from_repo_config(
        {
            "coverage": {
                "auto_discover": False,
                "paths": "coverage/lcov.info",
                "strip_prefix": "build",
                "reingest_on_update": True,
            }
        }
    )
    assert cfg.auto_discover is False
    assert cfg.paths == ("coverage/lcov.info",)
    assert cfg.strip_prefix == "build"
    assert cfg.reingest_on_update is True


# ---------------------------------------------------------------------------
# Directory agreement, report location and Cobertura source roots
# ---------------------------------------------------------------------------


def test_a_lone_basename_needs_a_matching_directory() -> None:
    # Only the basename agrees: another package's utils.py is not this one.
    res = resolve_reports([_report([_fc("other/pkg/utils.py")])], {"src/utils.py"})
    assert res.unmatched == ["other/pkg/utils.py"]
    assert res.coverage_map == {}


def test_a_root_file_under_an_absolute_path_still_matches() -> None:
    # The whole key is the tail of the report path.
    res = resolve_reports([_report([_fc("/home/ci/proj/command.go")])], {"command.go"})
    assert "command.go" in res.coverage_map


def test_relative_paths_resolve_under_the_reports_own_package() -> None:
    # A monorepo package's report names paths relative to that package.
    keys = {"src/index.ts", "packages/web/src/index.ts", "packages/api/src/index.ts"}
    report = CoverageReport(
        source_format="lcov",
        files=[_fc("src/index.ts")],
        origin_dir="packages/web/coverage",
    )
    res = resolve_reports([report], keys)
    assert list(res.coverage_map) == ["packages/web/src/index.ts"]
    assert res.matched_exact == 1


def test_a_tie_prefers_the_reports_own_directory() -> None:
    keys = {"packages/web/src/util.ts", "packages/api/src/util.ts"}
    report = CoverageReport(
        source_format="lcov",
        files=[_fc("/ci/build/src/util.ts")],
        origin_dir="packages/api/coverage",
    )
    res = resolve_reports([report], keys)
    assert list(res.coverage_map) == ["packages/api/src/util.ts"]


def test_cobertura_source_roots_disambiguate_shared_basenames() -> None:
    from repowise.core.analysis.health.coverage import parse_cobertura

    xml = (
        "<coverage><sources><source>/ci/repo/packages/core/src</source></sources>"
        '<packages><package><classes><class filename="pkg/__init__.py">'
        '<lines><line number="1" hits="1"/></lines></class></classes></package>'
        "</packages></coverage>"
    )
    report = parse_cobertura(xml)
    keys = {"packages/core/src/pkg/__init__.py", "packages/cli/src/pkg/__init__.py"}

    assert report.source_roots == ("/ci/repo/packages/core/src",)
    res = resolve_reports([report], keys)
    assert list(res.coverage_map) == ["packages/core/src/pkg/__init__.py"]


def test_build_coverage_map_records_where_the_report_lives(tmp_path: Path) -> None:
    pkg = tmp_path / "packages" / "web"
    (pkg / "coverage").mkdir(parents=True)
    (pkg / "coverage" / "lcov.info").write_text(
        "SF:src/index.ts\nDA:1,1\nend_of_record\n", encoding="utf-8"
    )
    res, errors = build_coverage_map(
        tmp_path,
        [pkg / "coverage" / "lcov.info"],
        {"src/index.ts", "packages/web/src/index.ts"},
    )
    assert not errors
    assert list(res.coverage_map) == ["packages/web/src/index.ts"]


def test_a_dependency_file_never_matches_the_repos_own() -> None:
    keys = {"index.js", "src/index.js"}
    res = resolve_reports([_report([_fc("node_modules/x/index.js")])], keys)
    assert res.coverage_map == {}
    assert res.unmatched == ["node_modules/x/index.js"]


def test_source_roots_that_disagree_are_ambiguous() -> None:
    keys = {"src/foo/x.py", "lib/foo/x.py"}
    report = CoverageReport(
        source_format="cobertura",
        files=[_fc("foo/x.py")],
        source_roots=("/ci/repo/src", "/ci/repo/lib"),
    )
    res = resolve_reports([report], keys)
    assert res.ambiguous == ["foo/x.py"]


def test_source_roots_win_over_the_report_directory() -> None:
    keys = {"packages/api/x.py", "packages/api/src/x.py"}
    report = CoverageReport(
        source_format="cobertura",
        files=[_fc("x.py")],
        source_roots=("/ci/repo/packages/api/src",),
        origin_dir="packages/api",
    )
    res = resolve_reports([report], keys)
    assert list(res.coverage_map) == ["packages/api/src/x.py"]
