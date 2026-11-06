"""Coverage markers fire only on shipping code that has lines to measure."""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.coverage_gap import BIOMARKER as GAP
from repowise.core.analysis.health.biomarkers.coverage_gradient import BIOMARKER as GRADIENT
from repowise.core.analysis.health.biomarkers.untested_complexity import (
    BIOMARKER as COMPLEXITY,
)
from repowise.core.analysis.health.biomarkers.untested_hotspot import BIOMARKER as HOTSPOT
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.coverage import parse_lcov, parse_repowise_json
from repowise.core.analysis.health.coverage.model import coverage_map_entry, file_coverage
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.traverser import FileTraverser

_COVERAGE_MARKERS = {"coverage_gradient", "coverage_gap", "untested_complexity"}

_CODE = "".join(
    f"def f{i}(a, b):\n"
    + "".join(f"    if a == {j}:\n        return b + {j}\n" for j in range(12))
    + "    return 0\n\n\n"
    for i in range(3)
)


def _report(tmp_path, files: dict[str, str], coverage: dict[str, dict]):
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    parsed = [
        parse_file(f, (tmp_path / f.path).read_bytes())
        for f in FileTraverser(tmp_path).traverse()
    ]
    return HealthAnalyzer(nx.DiGraph(), parsed_files=parsed, coverage_map=coverage).analyze()


def _uncovered(path: str, text: str) -> dict:
    lines = range(1, text.count("\n") + 1)
    return coverage_map_entry(file_coverage(path, (), lines), "lcov")


def test_only_production_code_gets_coverage_findings(tmp_path) -> None:
    files = {
        "src/app.py": _CODE,
        "scripts/release.py": _CODE,
        "docs/examples/demo.py": _CODE,
    }
    coverage = {p: _uncovered(p, t) for p, t in files.items()}
    report = _report(tmp_path, files, coverage)

    flagged = {f.file_path for f in report.findings if f.biomarker_type in _COVERAGE_MARKERS}
    assert flagged == {"src/app.py"}
    origins = {m.file_path: m.code_origin for m in report.metrics}
    assert origins["scripts/release.py"] == "tooling"
    assert origins["src/app.py"] == "production"


def test_a_file_with_no_executable_lines_is_not_zero_percent_covered(tmp_path) -> None:
    files = {
        "src/notes.py": "# A module of notes.\n# Nothing here runs.\n",
        "src/limits.py": "MAX = 3\nMIN = 1\n",
    }
    lcov = "".join(f"SF:{p}\nLF:0\nLH:0\nend_of_record\n" for p in files)
    coverage = {fc.file_path: coverage_map_entry(fc, "lcov") for fc in parse_lcov(lcov).files}
    report = _report(tmp_path, files, coverage)

    assert [f for f in report.findings if f.biomarker_type in _COVERAGE_MARKERS] == []
    assert {m.file_path: m.line_coverage_pct for m in report.metrics} == {
        "src/notes.py": None,
        "src/limits.py": None,
    }


def test_a_stated_percentage_without_a_total_is_kept() -> None:
    (fc,) = parse_repowise_json('{"files": [{"path": "a.py", "line_coverage_pct": 40}]}').files
    assert fc.total_coverable_lines == 0
    assert coverage_map_entry(fc, "repowise-json")["line_coverage_pct"] == 40.0


_FN = (FunctionComplexity(name="run", start_line=1, end_line=40, ccn=20, max_nesting=1, cognitive=0, nloc=38),)


@pytest.mark.parametrize(
    "origin", ["tooling", "build", "vendored", "generated", "docs_example", "test"]
)
def test_every_coverage_marker_skips_code_that_does_not_ship(origin: str) -> None:
    ctx = FileContext(
        file_path="src/x.py",
        language="python",
        nloc=200,
        has_test_file=False,
        module=None,
        is_test=False,
        code_origin=origin,
        all_functions=_FN,
        git_meta={"is_hotspot": True},
        dependents_count=12,
        line_coverage_pct=5.0,
        covered_lines={1},
        total_coverable_lines=200,
        coverable_lines=set(range(1, 201)),
    )
    for marker in (GRADIENT, GAP, COMPLEXITY, HOTSPOT):
        assert marker.detect(ctx) == [], marker.name
    ctx.code_origin = "production"
    for marker in (GRADIENT, GAP, COMPLEXITY, HOTSPOT):
        assert marker.detect(ctx), marker.name
