"""Parser tests against handcrafted real-world-shaped fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.health.coverage import (
    detect_format,
    is_test_file,
    paired_test_file,
    parse,
    parse_clover,
    parse_cobertura,
    parse_go_coverprofile,
    parse_jacoco,
    parse_lcov,
    parse_repowise_json,
    resolve_reports,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "coverage"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_lcov_parses_files_and_branches() -> None:
    report = parse_lcov(_read("sample.lcov"))
    assert report.source_format == "lcov"
    paths = {f.file_path: f for f in report.files}
    assert set(paths) == {"src/foo.py", "src/bar.py", "src/empty.py"}

    foo = paths["src/foo.py"]
    assert foo.total_coverable_lines == 4
    assert foo.line_coverage_pct == 75.0
    assert foo.branch_coverage_pct == 50.0
    assert foo.covered_lines == [1, 3, 4]

    bar = paths["src/bar.py"]
    assert bar.line_coverage_pct == 100.0
    assert bar.branch_coverage_pct is None

    empty = paths["src/empty.py"]
    assert empty.line_coverage_pct == 0.0


def test_lcov_tolerates_missing_end_of_record() -> None:
    text = "SF:src/a.py\nDA:1,1\nDA:2,0\n"
    report = parse_lcov(text)
    assert len(report.files) == 1
    assert report.files[0].line_coverage_pct == 50.0


def test_cobertura_parses_lines_and_branches() -> None:
    report = parse_cobertura(_read("sample.cobertura.xml"))
    assert report.source_format == "cobertura"
    paths = {f.file_path: f for f in report.files}
    assert set(paths) == {"src/foo.py", "src/bar.py"}
    foo = paths["src/foo.py"]
    assert foo.line_coverage_pct == 75.0
    assert foo.branch_coverage_pct == 50.0
    bar = paths["src/bar.py"]
    assert bar.line_coverage_pct == 100.0
    assert bar.branch_coverage_pct is None


def test_cobertura_returns_empty_on_bad_xml() -> None:
    report = parse_cobertura("not xml at all")
    assert report.files == []


def test_clover_parses_cond_lines() -> None:
    report = parse_clover(_read("sample.clover.xml"))
    assert report.source_format == "clover"
    paths = {f.file_path: f for f in report.files}
    y = paths["src/y.ts"]
    assert y.line_coverage_pct == 75.0
    assert y.branch_coverage_pct == 50.0
    z = paths["src/z.ts"]
    assert z.line_coverage_pct == 100.0
    assert z.branch_coverage_pct is None


def test_repowise_json_dict_form() -> None:
    text = json.dumps(
        {
            "format": "repowise-coverage-v1",
            "commit_sha": "deadbeef",
            "files": {
                "src/foo.py": {
                    "line_coverage_pct": 75.0,
                    "branch_coverage_pct": 50.0,
                    "covered_lines": [1, 3, 4],
                    "total_coverable_lines": 4,
                },
                "src/bar.py": {"line_coverage_pct": 100.0},
            },
        }
    )
    report = parse_repowise_json(text)
    assert report.source_format == "repowise-json"
    assert report.commit_sha == "deadbeef"
    paths = {f.file_path: f for f in report.files}
    assert set(paths) == {"src/foo.py", "src/bar.py"}
    foo = paths["src/foo.py"]
    assert foo.line_coverage_pct == 75.0
    assert foo.branch_coverage_pct == 50.0
    assert foo.covered_lines == [1, 3, 4]
    assert foo.total_coverable_lines == 4
    assert paths["src/bar.py"].branch_coverage_pct is None


def test_repowise_json_list_form_and_path_normalization() -> None:
    text = json.dumps(
        {
            "files": [
                {"file_path": "src\\win\\a.py", "line_coverage_pct": 40.0},
                {"path": "src/b.py", "covered_lines": [1, 2], "total_coverable_lines": 8},
            ]
        }
    )
    report = parse_repowise_json(text)
    paths = {f.file_path: f for f in report.files}
    assert "src/win/a.py" in paths  # backslashes normalized to POSIX
    b = paths["src/b.py"]
    assert b.line_coverage_pct == 25.0  # derived from 2/8
    assert b.total_coverable_lines == 8


def test_repowise_json_derives_total_from_pct_and_covered() -> None:
    text = json.dumps({"files": {"x.py": {"line_coverage_pct": 50.0, "covered_lines": [1, 2, 3]}}})
    fc = parse_repowise_json(text).files[0]
    assert fc.total_coverable_lines == 6  # 3 covered at 50% => 6 coverable


def test_repowise_json_skips_unanchored_entries() -> None:
    # An entry with no pct, no covered lines, no total cannot anchor coverage —
    # it is absent, not zero, so it must be dropped.
    text = json.dumps({"files": {"x.py": {"branch_coverage_pct": 10.0}, "y.py": {}}})
    assert parse_repowise_json(text).files == []


def test_repowise_json_bad_input_is_empty() -> None:
    assert parse_repowise_json("not json").files == []
    assert parse_repowise_json("[1,2,3]").files == []


def test_go_coverprofile_expands_blocks_to_lines() -> None:
    report = parse_go_coverprofile(_read("sample.coverprofile"))
    assert report.source_format == "go-coverprofile"
    paths = {f.file_path: f for f in report.files}
    assert set(paths) == {"example.com/mod/pkg/calc.go", "example.com/mod/util/str.go"}

    calc = paths["example.com/mod/pkg/calc.go"]
    # The zero-statement block adds no line 11; the ``14.1`` end adds no line 14.
    assert calc.coverable_lines == [3, 4, 5, 7, 8, 9, 10, 12, 13]
    # 7-8 is hit in the second profile section; line 8 is hit-wins over 8-10.
    assert calc.covered_lines == [3, 4, 5, 7, 8]
    assert calc.total_coverable_lines == 9
    assert calc.line_coverage_pct == 55.56
    assert calc.branch_coverage_pct is None

    # A later zero count never un-covers a block.
    assert paths["example.com/mod/util/str.go"].line_coverage_pct == 100.0


def test_go_coverprofile_skips_malformed_lines() -> None:
    text = (
        "mode: count\n"
        "garbage\n"
        "a.go:1.1,2.2 x 1\n"
        "a.go:nope 1 1\n"
        "a.go:3.1,2.1 1 1\n"
        ":1.1,1.9 1 1\n"
        "dir with space/b:c.go:4.2,4.9 1 3\n"
    )
    report = parse_go_coverprofile(text)
    assert [(f.file_path, f.covered_lines) for f in report.files] == [
        ("dir with space/b:c.go", [4])
    ]
    assert parse_go_coverprofile("").files == []
    assert parse_go_coverprofile("mode: set\n").files == []


def test_jacoco_parses_lines_branches_and_merges_groups() -> None:
    report = parse_jacoco(_read("sample.jacoco.xml"))
    assert report.source_format == "jacoco"
    paths = {f.file_path: f for f in report.files}
    # Api.java has no line data, so it is unmeasured rather than 0%.
    assert set(paths) == {"com/foo/Bar.java", "Main.java"}

    bar = paths["com/foo/Bar.java"]
    assert bar.coverable_lines == [3, 5, 7, 9]
    # Line 7 is missed in one group and covered in the other: hit wins.
    assert bar.covered_lines == [3, 5, 7]
    assert bar.line_coverage_pct == 75.0
    # Line 5: 1/2, line 7: max over both groups 1/2.
    assert bar.branch_coverage_pct == 50.0

    main = paths["Main.java"]
    assert main.coverable_lines == [1, 2]
    assert main.covered_lines == [2]
    assert main.branch_coverage_pct is None


def test_jacoco_returns_empty_on_bad_xml() -> None:
    assert parse_jacoco("<report><package").files == []


@pytest.mark.parametrize(
    "name, expected",
    [
        ("sample.lcov", "lcov"),
        ("sample.cobertura.xml", "cobertura"),
        ("sample.clover.xml", "clover"),
        ("sample.coverprofile", "go-coverprofile"),
        ("sample.jacoco.xml", "jacoco"),
    ],
)
def test_detect_format(name: str, expected: str) -> None:
    assert detect_format(_read(name)) == expected
    # Some Windows tools prepend a UTF-8 BOM.
    assert detect_format("﻿" + _read(name)) == expected


def test_detect_jacoco_without_doctype() -> None:
    assert detect_format('<?xml version="1.0"?>\n<report name="x"><group/></report>') == "jacoco"


def test_bom_prefixed_reports_parse() -> None:
    assert parse("﻿" + _read("sample.coverprofile")).source_format == "go-coverprofile"
    assert len(parse("﻿" + _read("sample.jacoco.xml")).files) == 2


def test_go_and_jacoco_paths_resolve_to_repo_keys() -> None:
    keys = {"pkg/f.go", "src/main/java/com/foo/Bar.java", "src/main/java/com/other/Bar.java"}
    go = parse_go_coverprofile("mode: set\nexample.com/mod/pkg/f.go:1.1,2.9 1 1\n")
    jacoco = parse_jacoco(
        '<report name="r"><package name="com/foo"><sourcefile name="Bar.java">'
        '<line nr="1" mi="0" ci="1"/></sourcefile></package></report>'
    )
    res = resolve_reports([go, jacoco], keys)
    assert set(res.coverage_map) == {"pkg/f.go", "src/main/java/com/foo/Bar.java"}
    assert res.unmatched == [] and res.ambiguous == []


def test_detect_and_dispatch_repowise_json() -> None:
    text = json.dumps(
        {"format": "repowise-coverage-v1", "files": {"a.py": {"line_coverage_pct": 1.0}}}
    )
    assert detect_format(text) == "repowise-json"
    # recognizable by key even without the format tag
    text2 = json.dumps({"files": {"a.py": {"line_coverage_pct": 1.0}}})
    assert detect_format(text2) == "repowise-json"
    assert parse(text).source_format == "repowise-json"
    # JSON that isn't a coverage report sniffs to None
    assert detect_format(json.dumps({"hello": "world"})) is None


def test_detect_format_unknown() -> None:
    assert detect_format("hello world") is None
    assert detect_format("") is None


def test_parse_dispatches_via_detect() -> None:
    assert parse(_read("sample.lcov")).source_format == "lcov"
    assert parse(_read("sample.cobertura.xml")).source_format == "cobertura"
    assert parse(_read("sample.clover.xml")).source_format == "clover"
    assert parse(_read("sample.coverprofile")).source_format == "go-coverprofile"
    assert parse(_read("sample.jacoco.xml")).source_format == "jacoco"


def test_parse_with_explicit_format_override() -> None:
    # Force lcov parser on weird content — should yield empty rather than crash.
    report = parse("hello world", format="lcov")
    assert report.files == []


@pytest.mark.parametrize(
    "path, expected",
    [
        ("src/foo.py", False),
        ("src/test_foo.py", True),
        ("src/foo_test.py", True),
        ("src/foo.test.ts", True),
        ("src/foo.test.tsx", True),
        ("src/foo.spec.js", True),
        ("src/foo_test.go", True),
        ("tests/integration/x.py", True),
        ("packages/core/x.py", False),
        ("__tests__/something.js", True),
    ],
)
def test_is_test_file(path: str, expected: bool) -> None:
    assert is_test_file(path) is expected


def test_is_test_file_uses_source_imports() -> None:
    src = "import pytest\n\ndef test_x():\n    pass\n"
    assert is_test_file("weird/path/name.py", src) is True


def test_paired_test_file_finds_partner() -> None:
    all_paths = {"src/foo.py", "tests/test_foo.py", "src/bar.py"}
    assert paired_test_file("src/foo.py", all_paths) == "tests/test_foo.py"
    assert paired_test_file("src/bar.py", all_paths) is None
