"""Tests for coverage artifact discovery + report-path resolution.

The resolver is the make-or-break piece: real reports almost never store
repowise's canonical repo-relative POSIX key, so we must reconcile absolute
/ build-relative paths back to the indexed tree without per-report config.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.coverage import (
    CoverageConfig,
    PathGate,
    build_coverage_map,
    configured_coverage,
    discover_artifacts,
    expand_report_patterns,
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


def test_coverage_config_paths_take_globs_and_per_report_prefixes(tmp_path: Path) -> None:
    for rel in ("web/coverage/lcov.info", "api/coverage/lcov.info", "root.lcov"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    cfg = CoverageConfig.from_repo_config(
        {
            "coverage": {
                "paths": [
                    {"path": "*/coverage/lcov.info", "path_prefix": "pkg"},
                    "root.lcov",
                    "missing.lcov",
                    {"path_prefix": "no-path"},
                ],
                "ignore": ["gen/", "**/*_pb2.py"],
                "min_coverable_lines": 5,
            }
        }
    )

    assert cfg.paths == ("*/coverage/lcov.info", "root.lcov", "missing.lcov")
    assert cfg.ignore == ("gen/", "**/*_pb2.py")
    assert cfg.min_coverable_lines == 5
    assert cfg.reports(tmp_path) == {
        tmp_path / "api/coverage/lcov.info": "pkg",
        tmp_path / "web/coverage/lcov.info": "pkg",
        tmp_path / "root.lcov": None,
    }


def test_coverage_config_rejects_a_bad_min_coverable_lines() -> None:
    for bad in (-1, "5", 2.5, True):
        cfg = CoverageConfig.from_repo_config({"coverage": {"min_coverable_lines": bad}})
        assert cfg.min_coverable_lines is None


def test_coverage_config_parses_path_gates() -> None:
    cfg = CoverageConfig.from_repo_config(
        {
            "coverage": {
                "gates": [
                    {"name": "api", "paths": ["packages/api/", "!**/gen/"], "fail_under": 80},
                    {"name": "docs", "paths": "docs/", "informational": True},
                ]
            }
        }
    )

    assert cfg.gates == (
        PathGate("api", ("packages/api/", "!**/gen/"), 80.0),
        PathGate("docs", ("docs/",), None, informational=True),
    )
    assert cfg.gate_errors == ()
    assert CoverageConfig().gates == ()


def test_coverage_config_names_each_invalid_path_gate() -> None:
    cfg = CoverageConfig.from_repo_config(
        {
            "coverage": {
                "gates": [
                    {"name": "ok", "paths": ["a/"]},
                    {"name": "ok", "paths": ["b/"]},
                    {"name": "nopaths", "paths": []},
                    {"name": "high", "paths": ["c/"], "fail_under": 120},
                    {"name": "typo", "paths": ["d/"], "fail-under": 80},
                    {"name": "docs", "paths": ["e/"], "informational": "yes"},
                    {"paths": ["f/"]},
                    {"name": "none", "paths": ["!g/", "# note"]},
                    "g/",
                ]
            }
        }
    )

    assert [g.name for g in cfg.gates] == ["ok"]
    assert cfg.gate_errors == (
        "coverage.gates[1] ('ok'): duplicate name; each gate needs its own.",
        "coverage.gates[2] ('nopaths'): paths must be a non-empty list of globs.",
        "coverage.gates[3] ('high'): fail_under must be a number from 0 to 100, got 120.",
        "coverage.gates[4] ('typo'): unknown key fail-under; expected name, paths, "
        "fail_under, informational.",
        "coverage.gates[5] ('docs'): informational must be true or false, got 'yes'.",
        "coverage.gates[6]: name must be a non-empty string.",
        "coverage.gates[7] ('none'): paths must include a glob that is not a comment or "
        "a ! exclusion.",
        "coverage.gates[8]: must be a mapping with name and paths.",
    )
    bad_block = CoverageConfig.from_repo_config({"coverage": {"gates": {"api": "x/"}}})
    assert bad_block.gate_errors == (
        "coverage.gates must be a list of {name, paths} entries.",
    )


def test_configured_coverage_reads_the_repo_config(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "coverage:\n  ignore: [gen/]\n  gates:\n    - {name: api, paths: [api/], fail_under: 70}\n"
    )

    cfg = configured_coverage(tmp_path)
    assert cfg.ignore == ("gen/",)
    assert cfg.gates == (PathGate("api", ("api/",), 70.0),)
    assert configured_coverage(tmp_path / "missing").gates == ()


def test_expand_report_patterns_sorts_and_dedupes(tmp_path: Path) -> None:
    for rel in ("b/lcov.info", "a/deep/lcov.info"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")

    found = expand_report_patterns(["**/lcov.info", "b/lcov.info", "none/*.info"], tmp_path)
    assert found == [tmp_path / "a/deep/lcov.info", tmp_path / "b/lcov.info"]


def test_expand_report_patterns_never_walks_dependency_trees(tmp_path: Path) -> None:
    for rel in ("web/coverage/lcov.info", "node_modules/x/coverage/lcov.info"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")

    assert expand_report_patterns(["**/lcov.info"], tmp_path) == [
        tmp_path / "web/coverage/lcov.info"
    ]
    # Leading literal directories, absolute ones too, root the walk.
    absolute = (tmp_path / "web").as_posix() + "/**/lcov.info"
    assert expand_report_patterns([absolute], Path()) == [tmp_path / "web/coverage/lcov.info"]


def test_an_existing_path_is_taken_literally_before_globbing(tmp_path: Path) -> None:
    odd = tmp_path / "cov[1].info"
    odd.write_text("")
    assert expand_report_patterns(["cov[1].info"], tmp_path) == [odd]


# ---------------------------------------------------------------------------
# Per-report prefix, coverage.ignore, Go modules
# ---------------------------------------------------------------------------


def test_a_reports_own_prefix_wins_over_the_global_one() -> None:
    keys = {"web/src/a.ts", "api/src/a.ts"}
    web = CoverageReport(source_format="lcov", files=[_fc("src/a.ts")], path_prefix="web")
    res = resolve_reports([web, _report([_fc("src/a.ts")])], keys, path_prefix="api")
    assert sorted(res.coverage_map) == ["api/src/a.ts", "web/src/a.ts"]


def test_a_per_report_prefix_skips_the_report_directory_step() -> None:
    # Under its own directory the report would resolve to pkg/src/a.ts.
    keys = {"pkg/src/a.ts", "lib/src/a.ts"}
    report = CoverageReport(
        source_format="lcov", files=[_fc("src/a.ts")], origin_dir="pkg", path_prefix="lib"
    )
    assert list(resolve_reports([report], keys).coverage_map) == ["lib/src/a.ts"]


def test_build_coverage_map_applies_report_prefixes(tmp_path: Path) -> None:
    report_path = tmp_path / "artifacts" / "web.info"
    report_path.parent.mkdir()
    report_path.write_text("SF:src/a.ts\nDA:1,1\nend_of_record\n")

    res, errors = build_coverage_map(
        tmp_path, [report_path], {"web/src/a.ts"}, report_prefixes={report_path: "web"}
    )
    assert not errors
    assert list(res.coverage_map) == ["web/src/a.ts"]


def test_ignored_report_entries_are_dropped_and_counted_apart() -> None:
    keys = {"src/a.py", "src/gen/b_pb2.py", "src/gen/c_pb2.py"}
    report = _report(
        [_fc("src/a.py"), _fc("src/gen/b_pb2.py"), _fc("src/gen/c_pb2.py"), _fc("/x/zz.py")]
    )
    res = resolve_reports([report], keys, ignore=["*_pb2.py"])

    assert list(res.coverage_map) == ["src/a.py"]
    assert res.ignored == 2
    assert res.unmatched == ["/x/zz.py"]
    # 1 of the 2 entries that were not ignored mapped: not a fragment.
    assert res.mapping_partial is False
    assert res.total == 2


def test_ignore_also_drops_entries_that_resolve_to_nothing() -> None:
    # Generated code that is not in the repository at all.
    report = _report([_fc("src/a.py"), _fc("/ci/build/gen/api_pb2.py"), _fc("/ci/gen/b.py")])
    res = resolve_reports([report], {"src/a.py"}, ignore=["gen/"])

    assert res.ignored == 2
    assert res.unmatched == []
    assert res.mapping_partial is False


def test_ignore_drops_per_test_records() -> None:
    from repowise.core.analysis.health.coverage import resolve_test_reports
    from repowise.core.analysis.health.coverage.model import (
        ContextCoverageReport,
        TestCoverage,
    )

    report = ContextCoverageReport(
        source_format="coverage.py",
        has_contexts=True,
        records=[
            TestCoverage("t::a", "src/a.py", [1]),
            TestCoverage("t::b", "src/gen/b.py", [1]),
        ],
    )
    res = resolve_test_reports(report, {"src/a.py", "src/gen/b.py"}, ignore=["gen/"])
    assert [r.file_path for r in res.records] == ["src/a.py"]
    assert res.ignored == 1


def _go_repo(tmp_path: Path) -> Path:
    backend = tmp_path / "backend"
    (backend / "pkg").mkdir(parents=True)
    (backend / "go.mod").write_text('module "example.com/m" // the service\n\ngo 1.22\n')
    (backend / "main.go").write_text("package main\n")
    (backend / "pkg" / "x.go").write_text("package pkg\n")
    (tmp_path / "main.go").write_text("package other\n")
    profile = tmp_path / "coverage.out"
    profile.write_text(
        "mode: set\n"
        "example.com/m/main.go:3.13,5.2 1 1\n"
        "example.com/m/pkg/x.go:3.13,5.2 1 0\n"
    )
    return profile


def test_go_module_paths_map_to_the_module_directory(tmp_path: Path) -> None:
    profile = _go_repo(tmp_path)
    # Indexed keys hold source files only: go.mod is found on disk.
    keys = {"backend/main.go", "backend/pkg/x.go", "main.go"}

    res, errors = build_coverage_map(tmp_path, [profile], keys)
    assert not errors
    assert sorted(res.coverage_map) == ["backend/main.go", "backend/pkg/x.go"]
    assert res.matched_exact == 2
    # An explicit prefix, per report or global, says where the paths live.
    prefixed, _ = build_coverage_map(
        tmp_path, [profile], keys, report_prefixes={profile: "elsewhere"}
    )
    assert "backend/main.go" not in prefixed.coverage_map
    global_prefix, _ = build_coverage_map(tmp_path, [profile], keys, path_prefix="elsewhere")
    assert "backend/main.go" not in global_prefix.coverage_map


def test_a_duplicate_go_module_path_resolves_to_the_shallowest(tmp_path: Path) -> None:
    from repowise.core.analysis.health.coverage.discovery import _go_modules

    _go_repo(tmp_path)
    for copy in ("examples/deep/copy", "backend/testdata/fixture"):
        (tmp_path / copy).mkdir(parents=True)
        (tmp_path / copy / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "go.mod").write_text("module example.com/m/tools\n")

    assert _go_modules(tmp_path) == [("example.com/m/tools", "tools"), ("example.com/m", "backend")]


def test_a_short_go_module_never_rewrites_other_formats(tmp_path: Path) -> None:
    (tmp_path / "web" / "src").mkdir(parents=True)
    (tmp_path / "web" / "go.mod").write_text("module web\n")
    lcov = tmp_path / "lcov.info"
    lcov.write_text("SF:web/src/x.ts\nDA:1,1\nend_of_record\n")

    res, _ = build_coverage_map(tmp_path, [lcov], {"web/src/x.ts", "web/web/src/x.ts"})
    assert list(res.coverage_map) == ["web/src/x.ts"]


def test_the_longest_go_module_wins() -> None:
    from repowise.core.analysis.health.coverage.discovery import _in_go_module

    modules = [("example.com/m/tools", "tools"), ("example.com/m", "")]
    assert _in_go_module("example.com/m/tools/gen.go", modules) == "tools/gen.go"
    assert _in_go_module("example.com/m/main.go", modules) == "main.go"
    assert _in_go_module("example.com/mx/main.go", modules) is None


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
