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
    parse_lcov,
    parse_repowise_json,
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


# ---------------------------------------------------------------------------
# Zero coverable lines (issue #2193).
#
# ``LF:0`` answers "there is nothing here to measure", which is a different
# fact from ``LH:0 LF:2`` ("two lines, neither executed"). Reporting both as
# 0.0 handed every downstream consumer a hard zero it could not distinguish
# from a genuinely uncovered file, so coverage_gradient and untested_hotspot
# both fired on a type-only module and fix_first ranked it as a real defect.
# ``None`` is the "not applicable" answer, matching branch_coverage_pct in the
# same dataclass.
# ---------------------------------------------------------------------------

_LCOV_ZERO_LF = """TN:
SF:src/types.ts
FNF:0
FNH:0
LF:0
LH:0
BRF:0
BRH:0
end_of_record
TN:
SF:src/real.ts
FNF:2
FNH:0
DA:1,0
DA:2,0
LF:2
LH:0
BRF:0
BRH:0
end_of_record
"""


def test_lcov_zero_coverable_lines_is_not_applicable() -> None:
    """The issue's own reproduction, both records side by side.

    The two must not collapse to the same value: one has nothing to cover.
    """
    paths = {f.file_path: f for f in parse_lcov(_LCOV_ZERO_LF).files}

    types = paths["src/types.ts"]
    real = paths["src/real.ts"]

    assert types.total_coverable_lines == 0
    assert types.line_coverage_pct is None
    assert real.total_coverable_lines == 2
    assert real.line_coverage_pct == 0.0


def test_lcov_zero_lf_without_da_lines_is_not_applicable() -> None:
    """``LF`` is optional; a record with no ``DA`` lines is the same fact.

    Derived totals come from the ``DA`` set, so this is the path a report
    without an explicit ``LF`` takes to the same answer.
    """
    report = parse_lcov("SF:src/only_types.ts\nFNF:0\nFNH:0\nend_of_record\n")

    assert report.files[0].total_coverable_lines == 0
    assert report.files[0].line_coverage_pct is None


def test_cobertura_zero_coverable_lines_is_not_applicable() -> None:
    xml = """<coverage>
      <packages><package name="p"><classes>
        <class filename="src/types.ts" line-rate="0" branch-rate="0">
          <lines/>
        </class>
        <class filename="src/real.ts" line-rate="0" branch-rate="0">
          <lines>
            <line number="1" hits="0"/>
            <line number="2" hits="0"/>
          </lines>
        </class>
      </classes></package></packages>
    </coverage>"""
    paths = {f.file_path: f for f in parse_cobertura(xml).files}

    assert paths["src/types.ts"].total_coverable_lines == 0
    assert paths["src/types.ts"].line_coverage_pct is None
    assert paths["src/real.ts"].line_coverage_pct == 0.0


def test_clover_zero_coverable_lines_is_not_applicable() -> None:
    xml = """<coverage>
      <project><package name="p">
        <file path="src/types.ts"><metrics statements="0" coveredstatements="0"/></file>
        <file path="src/real.ts">
          <line num="1" count="0" type="stmt"/>
          <line num="2" count="0" type="stmt"/>
        </file>
      </package></project>
    </coverage>"""
    paths = {f.file_path: f for f in parse_clover(xml).files}

    assert paths["src/types.ts"].total_coverable_lines == 0
    assert paths["src/types.ts"].line_coverage_pct is None
    assert paths["src/real.ts"].line_coverage_pct == 0.0


def test_repowise_json_zero_coverable_lines_is_not_applicable() -> None:
    """An entry that pins down ``total_coverable_lines: 0`` is not 0% covered."""
    text = json.dumps(
        {
            "files": {
                "src/types.ts": {"line_coverage_pct": 0.0, "total_coverable_lines": 0},
                "src/real.ts": {"line_coverage_pct": 0.0, "total_coverable_lines": 2},
            }
        }
    )
    paths = {f.file_path: f for f in parse_repowise_json(text).files}

    assert paths["src/types.ts"].line_coverage_pct is None
    assert paths["src/real.ts"].line_coverage_pct == 0.0


def test_repowise_json_keeps_a_genuine_zero_for_a_covered_set() -> None:
    """A file with hits and a real total keeps its percentage, however low."""
    text = json.dumps({"files": {"src/a.py": {"covered_lines": [1], "total_coverable_lines": 20}}})
    fc = parse_repowise_json(text).files[0]

    assert fc.line_coverage_pct == 5.0


def test_lcov_zero_lines_is_not_100_percent_covered() -> None:
    """The rejected alternative: vacuous 100% inflates anything that averages.

    A file with no code counted as fully covered is a quietly wrong number
    nobody files a bug about, which is why the maintainer scoped this to None.
    An explicit LF:0/LH:0 record must not read as either 100% or 0%.
    """
    pct = parse_lcov("SF:src/types.ts\nLF:0\nLH:0\nend_of_record\n").files[0].line_coverage_pct

    assert pct is None


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


@pytest.mark.parametrize(
    "name, expected",
    [
        ("sample.lcov", "lcov"),
        ("sample.cobertura.xml", "cobertura"),
        ("sample.clover.xml", "clover"),
    ],
)
def test_detect_format(name: str, expected: str) -> None:
    assert detect_format(_read(name)) == expected


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
