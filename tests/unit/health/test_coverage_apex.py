"""Salesforce Apex coverage: CLI JSON parsing and class-name to file resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.coverage import (
    build_coverage_map,
    detect_format,
    discover_artifacts,
    parse,
    parse_apex,
    parse_apex_contexts,
    parse_contexts_file,
    resolve_reports,
    resolve_test_reports,
)
from repowise.core.analysis.health.coverage.discovery import (
    _build_suffix_index,
    _match_key,
    _merge_into,
    _resolve_path,
    _salesforce_package_dirs,
)
from repowise.core.analysis.health.coverage.model import (
    CoverageReport,
    FileCoverage,
    file_coverage,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "coverage"
CLASSES = "force-app/main/default/classes"
TRIGGER = "force-app/main/default/triggers/AccountTrigger.trigger"

KEYS = {
    "force-app/main/default/classes/Trigger Recipes/AccountTriggerHandler.cls",
    "force-app/main/default/classes/Trigger Recipes/AccountTriggerHandler_Tests.cls",
    "force-app/main/default/triggers/AccountTrigger.trigger",
    "force-app/main/default/classes/AccountTrigger.cls",
    "force-app/main/default/classes/Dup.cls",
    "unpackaged/classes/Dup.cls",
    "src/classes/Legacy.cls",
    "tools/Makefile",
}


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_cli_result_classes_and_triggers() -> None:
    report = parse_apex(_read("sample.apex.json"))
    assert report.source_format == "apex"
    by_path = {f.file_path: f for f in report.files}
    # An interface with no executable lines is left out, not stored as 0%.
    assert set(by_path) == {"AccountTriggerHandler.cls", "AccountTrigger.trigger"}
    handler = by_path["AccountTriggerHandler.cls"]
    assert handler.coverable_lines == [4, 5, 8, 9, 12, 13]
    assert handler.covered_lines == [4, 5, 8, 12]
    assert handler.line_coverage_pct == pytest.approx(66.67)
    assert handler.branch_coverage_pct is None


def test_codecoverage_file_matches_cli_result() -> None:
    bare = parse_apex(_read("sample.apex-codecoverage.json"))
    full = parse_apex(_read("sample.apex.json"))
    assert [(f.file_path, f.covered_lines) for f in bare.files] == [
        (f.file_path, f.covered_lines) for f in full.files
    ]


def test_unknown_id_prefix_keeps_bare_name_and_bad_input_is_empty() -> None:
    report = parse_apex(
        '[{"name": "Foo", "lines": {"1": 1, "0": 1, "x": 1}}, {"id": "01p", "lines": {}}]'
    )
    assert [(f.file_path, f.coverable_lines) for f in report.files] == [("Foo", [1])]
    assert parse_apex("not json").files == []
    assert parse_apex('{"result": {"tests": []}}').files == []


def test_per_test_records() -> None:
    report = parse_apex_contexts(_read("sample.apex.json"))
    assert report.has_contexts
    # A record with no covered line says nothing about what the test ran.
    assert [(r.test_id, r.file_path, r.covered_lines, r.test_file) for r in report.records] == [
        (
            "AccountTriggerHandler_Tests.testInsertsAccount",
            "AccountTriggerHandler.cls",
            [4, 5, 8],
            "AccountTriggerHandler_Tests.cls",
        ),
        (
            "AccountTriggerHandler_Tests.testInsertsAccount",
            "AccountTrigger.trigger",
            [2, 3],
            "AccountTriggerHandler_Tests.cls",
        ),
    ]
    assert not parse_apex_contexts(_read("sample.apex-codecoverage.json")).has_contexts


@pytest.mark.parametrize(
    "name", ["sample.apex.json", "sample.apex-codecoverage.json", "sample.apex-deploy.json"]
)
def test_detect_and_dispatch(name: str) -> None:
    assert detect_format(_read(name)) == "apex"
    assert detect_format("\ufeff" + _read(name)) == "apex"
    assert parse("\ufeff" + _read(name)).source_format == "apex"


def test_detect_needs_the_cli_markers() -> None:
    assert detect_format('{"result": {"summary": {"outcome": "Passed"}}}') is None
    assert detect_format('[{"name": "x", "lines": {}}]') is None
    assert detect_format('{"result": {"details": {"componentSuccesses": []}}}') is None


def test_deploy_result_percentages_and_extensions() -> None:
    report = parse(_read("sample.apex-deploy.json"))
    assert report.source_format == "apex"
    by_path = {f.file_path: f for f in report.files}
    # The zero-location interface is skipped, not stored as 0%.
    assert set(by_path) == {"AccountService.cls", "AccountTrigger.trigger", "FullyCovered.cls"}
    svc = by_path["AccountService.cls"]
    assert svc.line_coverage_pct == pytest.approx(78.57)
    assert (svc.total_coverable_lines, svc.covered_line_count) == (28, 22)
    assert by_path["AccountTrigger.trigger"].line_coverage_pct == pytest.approx(60.0)
    assert by_path["FullyCovered.cls"].line_coverage_pct == pytest.approx(100.0)


def test_deploy_result_states_counts_only() -> None:
    for fc in parse_apex(_read("sample.apex-deploy.json")).files:
        # No covered-line list in the payload: left empty, never invented.
        assert fc.covered_lines == [] and fc.coverable_lines == []
        assert fc.branch_coverage_pct is None


def test_deploy_single_object_entry() -> None:
    text = (
        '{"result": {"details": {"runTestResult": {"codeCoverage": '
        '{"name": "Solo", "type": "Class", "numLocations": "4", "numLocationsNotCovered": "1"}'
        "}}}}"
    )
    assert detect_format(text) == "apex"
    report = parse(text)
    assert [(f.file_path, f.line_coverage_pct) for f in report.files] == [("Solo.cls", 75.0)]


def test_deploy_bad_counts_are_skipped() -> None:
    text = (
        '{"result": {"details": {"runTestResult": {"codeCoverage": ['
        '{"name": "A", "type": "Class", "numLocations": "x", "numLocationsNotCovered": "1"},'
        '{"name": "B", "type": "Class", "numLocations": "5"},'
        '{"name": "C", "type": "Class", "numLocations": "5", "numLocationsNotCovered": "9"}'
        "]}}}}"
    )
    # C is clamped to 0% rather than going negative.
    assert [(f.file_path, f.line_coverage_pct) for f in parse_apex(text).files] == [("C.cls", 0.0)]


def test_deploy_result_has_no_per_test_records() -> None:
    assert not parse_apex_contexts(_read("sample.apex-deploy.json")).has_contexts


def test_parse_contexts_file_reads_apex(tmp_path: Path) -> None:
    path = tmp_path / "test-result-7070000000ABCDE.json"
    path.write_text("\ufeff" + _read("sample.apex.json"), encoding="utf-8")
    report = parse_contexts_file(path)
    assert report.source_format == "apex"
    assert len(report.records) == 2


def test_names_resolve_to_the_one_file_by_type() -> None:
    res = resolve_reports([parse_apex(_read("sample.apex.json"))], KEYS)
    # The trigger id picks AccountTrigger.trigger over the AccountTrigger.cls class.
    assert set(res.coverage_map) == {
        "force-app/main/default/classes/Trigger Recipes/AccountTriggerHandler.cls",
        "force-app/main/default/triggers/AccountTrigger.trigger",
    }
    assert res.unmatched == [] and res.ambiguous == []


def _report(*paths: str) -> CoverageReport:
    files = [FileCoverage(p, 100.0, None, [1], 1, [1]) for p in paths]
    return CoverageReport(source_format="apex", files=files)


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Apex names are case-insensitive.
        ("accounttriggerhandler.cls", f"{CLASSES}/Trigger Recipes/AccountTriggerHandler.cls"),
        ("LEGACY.cls", "src/classes/Legacy.cls"),
        # The CLI's lcov / Cobertura fallback when it found no source file.
        ("no-map/Legacy", "src/classes/Legacy.cls"),
        ("Legacy", "src/classes/Legacy.cls"),
        # A path the CLI did find resolves as any other report path.
        (TRIGGER, TRIGGER),
        # A bare name with no Apex file of that name keeps the ordinary suffix match.
        ("Makefile", "tools/Makefile"),
    ],
)
def test_resolution_matches(raw: str, expected: str) -> None:
    res = resolve_reports([_report(raw)], KEYS)
    assert list(res.coverage_map) == [expected]


@pytest.mark.parametrize(
    "raw, ambiguous",
    [
        # Two files of one name, or a class and a trigger behind a bare name: never guessed.
        ("Dup.cls", True),
        ("no-map/AccountTrigger", True),
        # No such file; a dotted or namespaced name is not a file name.
        ("Missing.cls", False),
        ("Outer.Inner", False),
        ("ns__Legacy.cls", False),
    ],
)
def test_resolution_refuses(raw: str, ambiguous: bool) -> None:
    res = resolve_reports([_report(raw)], KEYS)
    assert res.coverage_map == {}
    assert (res.ambiguous if ambiguous else res.unmatched) == [raw]


def test_match_key_prefers_first_matching_apex_package() -> None:
    repo_keys = {"preferred/classes/Dup.cls", "other/classes/Dup.cls"}
    assert _match_key(
        "Dup.cls",
        repo_keys,
        _build_suffix_index(repo_keys),
        apex_package_dirs=("preferred", "other"),
    ) == ("preferred/classes/Dup.cls", False)


def test_match_key_keeps_ambiguity_in_first_matching_apex_package() -> None:
    repo_keys = {"preferred/classes/Dup.cls", "preferred/extra/Dup.cls", "later/classes/Dup.cls"}
    assert _match_key(
        "Dup.cls",
        repo_keys,
        _build_suffix_index(repo_keys),
        apex_package_dirs=("preferred", "later"),
    ) == (None, True)


def test_match_key_uses_later_apex_package_when_earlier_packages_have_no_hit() -> None:
    repo_keys = {"later/classes/Dup.cls", "other/classes/Dup.cls"}
    assert _match_key(
        "Dup.cls",
        repo_keys,
        _build_suffix_index(repo_keys),
        apex_package_dirs=("earlier", "later", "other"),
    ) == ("later/classes/Dup.cls", False)


def test_match_key_without_apex_package_dirs_keeps_duplicate_ambiguity() -> None:
    repo_keys = {"preferred/classes/Dup.cls", "other/classes/Dup.cls"}
    assert _match_key("Dup.cls", repo_keys, _build_suffix_index(repo_keys)) == (None, True)


def test_resolve_path_source_roots_apply_apex_package_priority() -> None:
    first = "pkg-one/classes/Dup.cls"
    second = "pkg-two/classes/Dup.cls"
    repo_keys = {first, second}
    # Keep the index order fixed: without package priority, the source-root
    # match would select `first` before the second root is considered.
    suffix_index = {"dup.cls": [first, second]}

    assert _resolve_path(
        "Dup.cls",
        repo_keys,
        suffix_index,
        strip_prefix=None,
        path_prefix=None,
        source_roots=("missing", "/"),
        apex_package_dirs=("pkg-two", "pkg-one"),
    ) == (second, False, False)


def test_resolve_path_falls_back_when_no_source_root_matches() -> None:
    repo_keys = {"src/Foo.cls"}
    assert _resolve_path(
        "Foo.cls",
        repo_keys,
        _build_suffix_index(repo_keys),
        strip_prefix=None,
        path_prefix=None,
        source_roots=("missing/package",),
    ) == ("src/Foo.cls", False, False)


def test_resolve_path_fallback_uses_apex_package_priority_without_source_root_match() -> None:
    first = "pkg-one/classes/Dup.cls"
    preferred = "pkg-two/classes/Dup.cls"
    repo_keys = {first, preferred}

    # The joined path has too little directory overlap to match either file;
    # only the final bare-name fallback can apply the package preference.
    assert _resolve_path(
        "Dup.cls",
        repo_keys,
        {"dup.cls": [first, preferred]},
        strip_prefix=None,
        path_prefix=None,
        source_roots=("missing/package/deeper",),
        apex_package_dirs=("pkg-two", "pkg-one"),
    ) == ("pkg-two/classes/Dup.cls", False, False)


def test_resolve_path_uses_matching_file_under_origin_dir() -> None:
    repo_keys = {"packages/app/classes/Dup.cls", "packages/other/classes/Dup.cls"}
    assert _resolve_path(
        "Dup.cls",
        repo_keys,
        _build_suffix_index(repo_keys),
        strip_prefix=None,
        path_prefix=None,
        origin_dir="packages/app/classes",
    ) == ("packages/app/classes/Dup.cls", False, True)


def test_resolve_path_keeps_multiple_source_root_matches_ambiguous() -> None:
    repo_keys = {"pkg-one/classes/Dup.cls", "pkg-two/classes/Dup.cls"}
    assert _resolve_path(
        "Dup.cls",
        repo_keys,
        _build_suffix_index(repo_keys),
        strip_prefix=None,
        path_prefix=None,
        source_roots=("pkg-one/classes", "pkg-two/classes"),
        apex_package_dirs=("pkg-two", "pkg-one"),
    ) == (None, True, False)


def test_match_key_respects_package_directory_boundaries() -> None:
    repo_keys = {"force-app/classes/Dup.cls", "force-app-extra/classes/Dup.cls"}
    assert _match_key(
        "Dup.cls",
        repo_keys,
        _build_suffix_index(repo_keys),
        apex_package_dirs=("force-app", "force-app-extra"),
    ) == ("force-app/classes/Dup.cls", False)


def test_salesforce_package_dirs_preserves_order_and_filters_duplicate_defaults(
    tmp_path: Path,
) -> None:
    (tmp_path / "sfdx-project.json").write_text(
        '{"packageDirectories":[ '
        '{"path":" /default/ ","default":true},'
        '{"path":"/default/"},'
        '{"path":"later"},'
        '{"path":"later"},'
        '{"path":"  /later/ "},'
        '{"path":"default-two","default":true},'
        'null,{"default":true},{"path":12},{"path":" / "}'
        ']}',
        encoding="utf-8",
    )
    assert _salesforce_package_dirs(tmp_path) == (
        "default",
        "default-two",
        "later",
        "later",
        "later",
    )


def _build_duplicate(tmp_path: Path, manifest: str | None, keys: set[str]):
    if manifest is not None:
        (tmp_path / "sfdx-project.json").write_text(manifest, encoding="utf-8")
    report_path = tmp_path / "apex.json"
    report_path.write_text(
        '[{"id":"01p000000000001AAA","name":"Dup","totalLines":1,'
        '"lines":{"1":1},"totalCovered":1,"coveredPercent":100}]',
        encoding="utf-8",
    )
    return build_coverage_map(tmp_path, [report_path], keys, allow_partial=True)


def test_build_coverage_map_prefers_default_salesforce_package(tmp_path: Path) -> None:
    keys = {"first/classes/Dup.cls", "default/classes/Dup.cls"}
    manifest = '{"packageDirectories":[{"path":"first"},{"path":"default","default":true}]}'
    resolved, errors = _build_duplicate(tmp_path, manifest, keys)
    assert errors == []
    assert list(resolved.coverage_map) == ["default/classes/Dup.cls"]


def test_build_coverage_map_uses_package_declaration_order_without_default(tmp_path: Path) -> None:
    keys = {"second/classes/Dup.cls", "first/classes/Dup.cls"}
    manifest = '{"packageDirectories":[{"path":"first"},{"path":"second"}]}'
    resolved, errors = _build_duplicate(tmp_path, manifest, keys)
    assert errors == []
    assert list(resolved.coverage_map) == ["first/classes/Dup.cls"]


@pytest.mark.parametrize("manifest", [None, "not json", "{}"])
def test_build_coverage_map_keeps_duplicates_ambiguous_without_usable_manifest(
    tmp_path: Path, manifest: str | None
) -> None:
    resolved, errors = _build_duplicate(
        tmp_path, manifest, {"first/classes/Dup.cls", "second/classes/Dup.cls"}
    )
    assert errors == []
    assert resolved.coverage_map == {}
    assert resolved.ambiguous == ["Dup.cls"]


def test_build_coverage_map_keeps_same_package_duplicates_ambiguous(tmp_path: Path) -> None:
    keys = {"preferred/classes/Dup.cls", "preferred/other/Dup.cls", "later/classes/Dup.cls"}
    manifest = '{"packageDirectories":[{"path":"preferred","default":true},{"path":"later"}]}'
    resolved, errors = _build_duplicate(tmp_path, manifest, keys)
    assert errors == []
    assert resolved.coverage_map == {}
    assert resolved.ambiguous == ["Dup.cls"]


def test_package_preference_preserves_unique_and_non_apex_matching(tmp_path: Path) -> None:
    keys = {"preferred/classes/Only.cls", "other/Makefile"}
    manifest = '{"packageDirectories":[{"path":"preferred","default":true}]}'
    (tmp_path / "sfdx-project.json").write_text(manifest, encoding="utf-8")
    report_path = tmp_path / "coverage.info"
    report_path.write_text(
        "SF:Only.cls\nDA:1,1\nend_of_record\nSF:Makefile\nDA:1,1\nend_of_record\n",
        encoding="utf-8",
    )
    resolved, errors = build_coverage_map(tmp_path, [report_path], keys, allow_partial=True)
    assert errors == []
    assert set(resolved.coverage_map) == keys


def test_per_test_records_resolve_source_and_test_file() -> None:
    res = resolve_test_reports(parse_apex_contexts(_read("sample.apex.json")), KEYS)
    test_file = "force-app/main/default/classes/Trigger Recipes/AccountTriggerHandler_Tests.cls"
    assert [(r.file_path, r.test_file) for r in res.records] == [
        ("force-app/main/default/classes/Trigger Recipes/AccountTriggerHandler.cls", test_file),
        ("force-app/main/default/triggers/AccountTrigger.trigger", test_file),
    ]
    assert res.test_files_resolved == 2


def test_discovers_cli_output(tmp_path: Path) -> None:
    for rel in (
        ".sfdx/tools/testresults/apex/test-result-codecoverage.json",
        "tests/apex/test-result-codecoverage.json",
    ):
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_text("[]", encoding="utf-8")
    found = {p.relative_to(tmp_path).as_posix() for p in discover_artifacts(tmp_path)}
    assert found == {
        ".sfdx/tools/testresults/apex/test-result-codecoverage.json",
        "tests/apex/test-result-codecoverage.json",
    }


def test_utf16_report_from_powershell_redirect_is_read(tmp_path: Path) -> None:
    # Windows PowerShell 5.1 writes `sf ... --json > result.json` as UTF-16.
    path = tmp_path / "result.json"
    path.write_bytes(_read("sample.apex.json").encode("utf-16"))
    assert len(parse_contexts_file(path).records) == 2
    resolved, errors = build_coverage_map(tmp_path, [path], KEYS)
    assert errors == []
    assert len(resolved.coverage_map) == 2


def test_test_file_stays_unset_when_the_test_class_does_not_resolve() -> None:
    text = _read("sample.apex.json").replace("AccountTriggerHandler_Tests", "Dup")
    res = resolve_test_reports(parse_apex_contexts(text), KEYS)
    assert [r.test_file for r in res.records] == [None, None]
    assert res.test_files_resolved == 0


def test_merge_two_counts_only_entries_keeps_stated_count() -> None:
    a = file_coverage("AccountService.cls", [], [], total=28, hit=22)
    b = file_coverage("AccountService.cls", [], [], total=28, hit=22)
    _merge_into(a, b)
    assert a.covered_line_count == 22
    assert a.line_coverage_pct == pytest.approx(78.57)


def test_merge_counts_only_with_line_sets_does_not_lose_hits() -> None:
    counts = file_coverage("AccountService.cls", [], [], total=28, hit=22)
    lines = file_coverage("AccountService.cls", [1, 2, 3], list(range(1, 29)))
    _merge_into(lines, counts)
    assert lines.covered_line_count == 22
    assert lines.total_coverable_lines == 28


def test_merge_order_does_not_matter() -> None:
    counts = file_coverage("AccountService.cls", [], [], total=28, hit=22)
    lines = file_coverage("AccountService.cls", [1, 2, 3], list(range(1, 29)))
    _merge_into(counts, lines)
    assert counts.covered_line_count == 22
    assert counts.line_coverage_pct == pytest.approx(78.57)


def test_merge_with_percentage_only_source_still_works() -> None:
    a = file_coverage("A.cls", [1, 2], [1, 2, 3, 4])
    b = file_coverage("A.cls", [], [], total=4)
    # A percentage-only source states no hit count at all.
    b.covered_line_count = None
    _merge_into(a, b)
    assert a.covered_line_count == 2


def test_two_deploy_reports_for_one_class_keep_their_percentage() -> None:
    keys = {"force-app/main/default/classes/AccountService.cls"}
    text = _read("sample.apex-deploy.json")
    res = resolve_reports([parse_apex(text), parse_apex(text)], keys)
    entry = res.coverage_map["force-app/main/default/classes/AccountService.cls"]
    assert entry["line_coverage_pct"] == pytest.approx(78.57)
