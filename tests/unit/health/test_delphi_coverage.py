"""DelphiCodeCoverage XML (``-xml -xmllines``): per-line hits live in ``<linehits>``."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.coverage import detect_format, parse
from repowise.core.analysis.health.coverage.delphi import parse_delphi_codecoverage

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "coverage" / "delphi_codecoverage.xml"


def _text() -> str:
    return FIXTURE.read_text(encoding="windows-1251")


def test_line_hits_give_covered_and_coverable_lines() -> None:
    report = parse_delphi_codecoverage(_text())
    assert report.source_format == "delphi-codecoverage"
    (calc,) = report.files
    assert calc.file_path == "uCalc.pas"
    assert calc.covered_lines == [19, 20, 24, 25]
    assert calc.coverable_lines == [19, 20, 24, 25, 27, 28, 32, 33]
    assert calc.line_coverage_pct == 50.0
    assert calc.branch_coverage_pct is None


def test_report_is_not_taken_for_cobertura_or_jacoco() -> None:
    # It names ``<packages`` and ``<coverage`` and opens with ``<report``.
    assert detect_format(_text()) == "delphi-codecoverage"
    assert parse(_text()).source_format == "delphi-codecoverage"


def test_same_file_listed_twice_is_merged_hit_wins() -> None:
    text = _text().replace(
        "</linehits>",
        '<file name="uCalc.pas">27=3;99=0</file></linehits>',
    )
    (calc,) = parse_delphi_codecoverage(text).files
    assert 27 in calc.covered_lines
    assert 99 in calc.coverable_lines and 99 not in calc.covered_lines


def test_without_line_hits_the_file_summary_is_used() -> None:
    # No ``-xmllines``: only the per-file "line, %" counter is reported.
    start = _text().index("<linehits>")
    end = _text().index("</linehits>") + len("</linehits>")
    text = _text()[:start] + _text()[end:]
    (calc,) = parse_delphi_codecoverage(text).files
    assert calc.total_coverable_lines == 8
    assert calc.line_coverage_pct == 50.0
    assert calc.covered_lines == []


def test_bad_xml_gives_an_empty_report() -> None:
    assert parse_delphi_codecoverage("<report><data").files == []


def test_bare_unit_name_resolves_to_the_repository_file() -> None:
    from repowise.core.analysis.health.coverage import resolve_reports

    report = parse_delphi_codecoverage(_text())
    res = resolve_reports([report], {"src/uCalc.pas", "src/uOther.pas"})
    assert set(res.coverage_map) == {"src/uCalc.pas"}
    assert res.unmatched == [] and res.ambiguous == []


def test_same_unit_name_in_two_directories_is_ambiguous() -> None:
    from repowise.core.analysis.health.coverage import resolve_reports

    report = parse_delphi_codecoverage(_text())
    res = resolve_reports([report], {"a/uCalc.pas", "b/uCalc.pas"})
    assert res.coverage_map == {}
    assert res.ambiguous
