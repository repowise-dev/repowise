"""Phase 2 integration test: LCOV ingest → analyzer → coverage biomarkers.

Runs the full HealthAnalyzer over the existing ``sample_repo`` fixture
with a handcrafted LCOV report. Asserts:

- The coverage parser produces FileCoverage rows.
- Coverage flows onto HealthFileMetric.line_coverage_pct.
- The coverage_gap biomarker fires for a deliberately-low-coverage file.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.coverage import parse as parse_coverage
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build_parsed_files(repo_path: Path) -> tuple[list, GraphBuilder]:
    traverser = FileTraverser(repo_path)
    file_infos = list(traverser.traverse())
    parser = ASTParser()
    graph_builder = GraphBuilder()
    parsed_files = []
    for fi in file_infos:
        try:
            source = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            graph_builder.add_file(parsed)
            parsed_files.append(parsed)
        except Exception:
            continue
    graph_builder.build()
    return parsed_files, graph_builder


def _python_file_paths(parsed_files: list) -> list[str]:
    return [pf.file_info.path for pf in parsed_files if pf.file_info.language == "python"]


def test_coverage_ingest_flows_through_analyzer(sample_repo_path: Path) -> None:
    parsed_files, graph_builder = _build_parsed_files(sample_repo_path)
    py_paths = _python_file_paths(parsed_files)
    assert py_paths, "sample_repo should have Python files"

    # Pick two known files: give one full coverage, the other very low
    # coverage with a lot of uncovered lines so coverage_gap fires.
    high_cov_path = next(p for p in py_paths if p.endswith("calculator.py"))
    low_cov_path = next(p for p in py_paths if p.endswith("models.py"))

    lcov = f"""TN:
SF:{high_cov_path}
DA:1,1
DA:2,1
DA:3,1
DA:4,1
LF:4
LH:4
end_of_record
SF:{low_cov_path}
{chr(10).join(f'DA:{i},0' for i in range(1, 81))}
DA:81,1
DA:82,1
LF:82
LH:2
end_of_record
"""
    report_cov = parse_coverage(lcov)
    assert report_cov.source_format == "lcov"
    cov_paths = {f.file_path for f in report_cov.files}
    assert high_cov_path in cov_paths
    assert low_cov_path in cov_paths

    coverage_map = {
        fc.file_path: {
            "line_coverage_pct": fc.line_coverage_pct,
            "branch_coverage_pct": fc.branch_coverage_pct,
            "covered_lines": list(fc.covered_lines),
            "total_coverable_lines": fc.total_coverable_lines,
        }
        for fc in report_cov.files
    }

    analyzer = HealthAnalyzer(
        graph_builder.graph(),
        git_meta_map={},
        parsed_files=parsed_files,
        coverage_map=coverage_map,
    )
    report = analyzer.analyze()

    metrics_by_path = {m.file_path: m for m in report.metrics}
    assert metrics_by_path[high_cov_path].line_coverage_pct == 100.0
    low_metric = metrics_by_path[low_cov_path]
    assert low_metric.line_coverage_pct is not None
    assert low_metric.line_coverage_pct < 5.0

    # coverage_gap should fire for the low-coverage file.
    gap_findings = [
        f
        for f in report.findings
        if f.biomarker_type == "coverage_gap" and f.file_path == low_cov_path
    ]
    assert gap_findings, "expected coverage_gap biomarker for the under-tested file"
    assert gap_findings[0].details["line_coverage_pct"] < 5.0
    assert gap_findings[0].details["uncovered_lines"] >= 25
