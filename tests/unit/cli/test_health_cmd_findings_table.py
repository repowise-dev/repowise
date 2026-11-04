"""``repowise health`` lists advisory findings apart from the counted ones."""

from __future__ import annotations

import io
from unittest.mock import patch

from rich.console import Console

from repowise.cli.commands.health_cmd import command as health_module
from repowise.core.analysis.health.models import HealthFindingData, Severity


def _finding(marker: str, impact: float, dimension: str) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=marker,
        severity=Severity.MEDIUM,
        file_path="src/app.py",
        function_name="run",
        line_start=1,
        line_end=9,
        details={},
        health_impact=impact,
        dimension=dimension,
    )


def test_header_counts_the_scored_rows_and_advisory_rows_are_marked_last() -> None:
    findings = [
        _finding("brain_method", 0.0, "advisory"),
        _finding("complex_method", 1.2, "defect"),
        _finding("hidden_coupling", 0.0, "advisory"),
        _finding("large_method", 0.4, "defect"),
    ]
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch.object(health_module, "console", console):
        health_module._render_findings_table(findings)
    out = console.file.getvalue()

    assert "2 open findings (+2 advisory, not scored):" in out
    rows = [line for line in out.splitlines() if "src/app.py" in line]
    order = ["complex_method", "large_method", "brain_method", "hidden_coupling"]
    assert all(marker in row for marker, row in zip(order, rows, strict=True))
    assert all("advisory" in r and "-0.00" not in r for r in rows[2:])
    assert "-1.20" in rows[0] and "advisory" not in rows[0]


def test_no_advisory_note_without_advisory_rows() -> None:
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch.object(health_module, "console", console):
        health_module._render_findings_table([_finding("complex_method", 1.2, "defect")])
    out = console.file.getvalue()
    assert "1 open findings:" in out and "advisory" not in out
