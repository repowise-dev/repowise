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
from repowise.core.analysis.health.coverage.model import CoverageReport, FileCoverage

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


@pytest.mark.parametrize("name", ["sample.apex.json", "sample.apex-codecoverage.json"])
def test_detect_and_dispatch(name: str) -> None:
    assert detect_format(_read(name)) == "apex"
    assert detect_format("﻿" + _read(name)) == "apex"
    assert parse("﻿" + _read(name)).source_format == "apex"


def test_detect_needs_the_cli_markers() -> None:
    assert detect_format('{"result": {"summary": {"outcome": "Passed"}}}') is None
    assert detect_format('[{"name": "x", "lines": {}}]') is None


def test_parse_contexts_file_reads_apex(tmp_path: Path) -> None:
    path = tmp_path / "test-result-7070000000ABCDE.json"
    path.write_text("﻿" + _read("sample.apex.json"), encoding="utf-8")
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
