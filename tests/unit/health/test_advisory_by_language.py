"""A marker listed as advisory for one language scores nothing there, and only there."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health import HealthAnalyzer, scoring
from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.biomarkers.registry import registered_biomarkers
from repowise.core.analysis.health.gain import removal_gain
from repowise.core.analysis.health.models import Severity, primary_finding
from repowise.core.analysis.health.scoring import (
    ADVISORY_DIMENSION,
    attach_impacts,
    finding_is_advisory,
    score_file,
)
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


@pytest.fixture
def complex_method_advisory_in_python(monkeypatch):
    monkeypatch.setattr(scoring, "_ADVISORY_BY_LANGUAGE", {"complex_method": frozenset({"python"})})


def _result(name: str = "complex_method") -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=name,
        severity=Severity.HIGH,
        function_name="f",
        line_start=1,
        line_end=9,
        details={},
    )


def test_the_table_is_registered_markers_not_already_advisory():
    registered = {b.name for b in registered_biomarkers()}
    for marker, languages in scoring._ADVISORY_BY_LANGUAGE.items():
        assert marker in registered and not scoring.is_advisory(marker)
        assert languages and all(lang == lang.lower() for lang in languages)


@pytest.mark.usefixtures("complex_method_advisory_in_python")
def test_listed_language_scores_nothing_and_stores_advisory():
    results = [_result()]
    scores, deductions = score_file(results, "python")
    assert scores == {"defect": 10.0, "maintainability": 10.0, "performance": 10.0}
    assert deductions == [0.0]
    (finding,) = attach_impacts(results, deductions, "python")
    assert finding.dimension == ADVISORY_DIMENSION
    assert finding_is_advisory(finding)


@pytest.mark.usefixtures("complex_method_advisory_in_python")
@pytest.mark.parametrize("language", ["java", None])
def test_other_languages_still_score(language):
    results = [_result()]
    scores, deductions = score_file(results, language)
    assert scores["defect"] < 10.0 and deductions[0] > 0.0
    (finding,) = attach_impacts(results, deductions, language)
    assert finding.dimension == "defect"
    assert not finding_is_advisory(finding)


@pytest.mark.usefixtures("complex_method_advisory_in_python")
def test_a_stored_advisory_row_neither_leads_nor_frees_points():
    results = [_result(), _result("large_method")]
    _, deductions = score_file(results, "python")
    findings = attach_impacts(results, deductions, "python")
    advisory, sized = findings
    lead = primary_finding(findings)
    assert (lead[0] if isinstance(lead, tuple) else lead) is sized
    assert removal_gain(findings, [(advisory, 1.0)]) == 0.0
    assert removal_gain(findings, [(sized, 1.0)]) > 0.0


_COMPLEX_PY = (
    "def f(a, b):\n    c = 0\n"
    + "".join(f"    if a.p{i}() and b.q{i}():\n        c += {i}\n" for i in range(12))
    + "    return c\n"
)


@pytest.mark.usefixtures("complex_method_advisory_in_python")
def test_the_engine_scores_with_the_files_language(tmp_path: Path):
    (tmp_path / "m.py").write_text(_COMPLEX_PY, encoding="utf-8")
    parser, builder, parsed = ASTParser(), GraphBuilder(tmp_path), []
    for info in FileTraverser(tmp_path).traverse():
        pf = parser.parse_file(info, Path(info.abs_path).read_bytes())
        builder.add_file(pf)
        parsed.append(pf)
    builder.build()
    report = HealthAnalyzer(
        builder.graph(), git_meta_map={}, parsed_files=parsed, repo_root=tmp_path
    ).analyze(None)
    marked = [f for f in report.findings if f.biomarker_type == "complex_method"]
    assert marked and all(f.dimension == ADVISORY_DIMENSION for f in marked)
    assert all(f.health_impact == 0.0 for f in marked)


@pytest.mark.parametrize(
    ("row", "advisory"),
    [
        ({"biomarker_type": "complex_method", "dimension": "advisory"}, True),
        ({"biomarker_type": "complex_method", "dimension": "defect"}, False),
        ({"biomarker_type": "complex_method"}, False),
        ({"biomarker_type": "brain_method", "dimension": "defect"}, True),
    ],
)
def test_a_stored_row_is_advisory_by_its_dimension_or_its_marker(row, advisory):
    """Surfaces read stored rows, which carry no language: the dimension does."""
    assert finding_is_advisory(row) is advisory
