"""``repowise health`` offers the same two controls the API and MCP do.

An agent or a script that can ask "is this repo's code getting better" through
one surface and not another gets a different answer depending on where it
asked, which is the drift these two options exist to remove.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands.health_cmd import command as health_module
from repowise.cli.commands.health_cmd.command import health_command
from repowise.cli.main import cli
from repowise.core.analysis.health.counts import COUNTS, DEFAULT_COUNTS
from repowise.core.analysis.health.scope import DEFAULT_SCOPE, SCOPES


def _option(name: str):
    return next(p for p in health_command.params if p.name == name)


def test_both_controls_offer_the_shared_vocabulary() -> None:
    scope, counts = _option("scope"), _option("counts")
    assert tuple(scope.type.choices) == SCOPES
    assert scope.default == DEFAULT_SCOPE
    assert tuple(counts.type.choices) == COUNTS
    assert counts.default == DEFAULT_COUNTS


def test_an_unknown_value_is_refused_rather_than_silently_defaulted() -> None:
    # Falling back to the default would report the calibrated number under a
    # flag that asked for the other one. Assert on click's rejection and not
    # just a non-zero exit: a missing index exits non-zero too.
    result = CliRunner().invoke(cli, ["health", "--counts", "code-shape"])
    assert result.exit_code != 0
    assert "Invalid value for '--counts'" in result.output
    assert "code-shape" in result.output


def test_the_distribution_line_names_every_band() -> None:
    """Three segments on a five-band scale reads as a scale that has three."""
    import re

    from repowise.cli.commands.health_cmd.summary import _render_distribution_line
    from repowise.core.analysis.health.grading import BAND_LABEL, BAND_ORDER

    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch("repowise.cli.commands.health_cmd.summary.console", console):
        _render_distribution_line(
            {
                "total_files": 5,
                "total_nloc": 500,
                "bands": {b: {"files": 1, "nloc": 100, "pct": 20.0} for b in BAND_ORDER},
            }
        )
    out = re.sub(r"\s+", " ", console.file.getvalue())
    for band in BAND_ORDER:
        assert f"{BAND_LABEL[band].lower()} (1 files)" in out


def test_the_trend_says_the_controls_do_not_reach_it() -> None:
    """The trend reads calibrated snapshots, so a flag on it would be a lie."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".repowise").mkdir()
        with (
            patch.object(health_module, "_render_trend") as render,
            patch.object(health_module, "resolve_command_target") as resolve,
        ):
            resolve.return_value = SimpleNamespace(
                is_workspace=False,
                repo_path=Path("."),
                narrow=lambda *a, **k: Path("."),
            )
            result = runner.invoke(cli, ["health", "--trend", "--counts", "code_shape"])
        render.assert_called_once()
    assert "do not apply to it" in result.output


def test_the_trend_stays_quiet_when_no_control_was_passed() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".repowise").mkdir()
        with (
            patch.object(health_module, "_render_trend"),
            patch.object(health_module, "resolve_command_target") as resolve,
        ):
            resolve.return_value = SimpleNamespace(
                is_workspace=False,
                repo_path=Path("."),
                narrow=lambda *a, **k: Path("."),
            )
            result = runner.invoke(cli, ["health", "--trend"])
    assert "do not apply to it" not in result.output


def test_the_report_leads_with_fix_first() -> None:
    """Title, where, why, effort and the verify command, from the shared queue."""
    from repowise.cli.commands.health_cmd.summary import _render_fix_first
    from repowise.core.analysis.health.fix_first import build_fix_first
    from tests.unit.health.fix_first_rows import (
        FINDINGS,
        METRICS,
        PERFORMANCE,
        PLANS,
        REFACTORING,
    )

    queue = build_fix_first(
        metrics=METRICS,
        findings=FINDINGS,
        refactoring=REFACTORING,
        performance=PERFORMANCE,
        plans=PLANS,
        limit=3,
    )
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch("repowise.cli.commands.health_cmd.summary.console", console):
        _render_fix_first((queue, queue.counts(len(queue.items))))
        _render_fix_first(None)
    out = console.file.getvalue()
    assert out.startswith("\nFix first (3 of 3 eligible")
    # The growing, reachable database loop leads; the refactor follows.
    assert " 1. Batch the database calls loops make through load_all " in out
    assert " 2. Start breaking up run (CCN 44, 50 lines): first lift lines 20-35 " in out
    assert "src/core.py:10" in out
    assert "verify: pytest tests/test_core_0.py" in out


def _mostly_unanalysed_output(*args: str) -> str:
    """``repowise health`` over 4 scored files beside 431 it cannot analyse."""
    from repowise.core.analysis.health.models import HealthFileMetricData
    from repowise.core.analysis.health.scoring import compute_kpis

    def row(path: str, score: float | None) -> HealthFileMetricData:
        return HealthFileMetricData(
            file_path=path,
            score=score,
            max_ccn=None if score is None else 1,
            max_nesting=None if score is None else 1,
            nloc=10,
            has_test_file=False,
            defect_score=score,
        )

    metrics = [row(f"s{i}.swift", 10.0) for i in range(4)]
    metrics += [row(f"u{i}.swift", None) for i in range(431)]
    report = SimpleNamespace(metrics=metrics, findings=[], kpis=compute_kpis(metrics, set()))
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".repowise").mkdir()
        with (
            patch.object(health_module, "resolve_command_target") as resolve,
            patch.object(health_module, "_load_stored_report", return_value=(report, set(), {})),
            patch.object(health_module, "_load_fix_first", return_value=None),
            patch.object(health_module, "_render_defect_accuracy_line"),
            patch.object(health_module, "_render_performance_section"),
            patch.object(health_module, "console", console),
        ):
            resolve.return_value = SimpleNamespace(
                is_workspace=False,
                repo_path=Path("."),
                narrow=lambda *a, **k: Path("."),
            )
            result = runner.invoke(cli, ["health", *args])
    assert result.exit_code == 0, result.output
    return console.file.getvalue()


def test_a_mostly_unanalysed_repo_gets_no_band_in_any_scope() -> None:
    """A narrowed recompute keeps the unanalysed files in the share."""
    for args in ((), ("--scope", "production")):
        out = _mostly_unanalysed_output(*args)
        # Never a bare score: coverage sits on the headline itself.
        assert "Code health: 10.0/10 across 4 of 435 code files ·" in out, args
        # No band anywhere on the line, the worst file's included.
        assert "Excellent" not in out, args


def test_the_headline_names_production_code_while_tests_are_in_it() -> None:
    def headline(**kpis) -> str:
        console = Console(file=io.StringIO(), width=400, force_terminal=False)
        with patch.object(health_module, "console", console):
            health_module._render_headline(kpis, False, 0)
        return console.file.getvalue()

    base = {"average_health": 8.0, "production_average": 6.0, "file_count": 2}
    # Every score carries its band: 6.0 is Fair beside a Good headline.
    assert "[Good] · Production code: 6.0/10 [Fair] · Worst:" in headline(
        **base, production_file_count=1
    )
    # Narrowed to production, the two figures are one.
    narrowed = headline(**base, production_file_count=2)
    assert "Production code" not in narrowed and "[Good] · Worst:" in narrowed


def test_the_headline_names_hotspot_health_only_when_there_is_one() -> None:
    def headline(**kpis) -> str:
        console = Console(file=io.StringIO(), width=400, force_terminal=False)
        with patch.object(health_module, "console", console):
            health_module._render_headline(kpis, False, 0)
        return console.file.getvalue()

    out = headline(average_health=8.0, hotspot_health=6.5, worst_performer_score=1.0)
    assert "Hotspot: 6.5/10 [Fair] · Worst: 1.0/10 [At risk]" in out
    assert "Hotspot" not in headline(average_health=8.0, hotspot_health=None)


def test_the_performance_line_leads_with_the_causes_worth_fixing_now() -> None:
    """The stored headline every surface prints, not a raw finding count."""
    from repowise.cli.commands.health_cmd.summary import _render_performance_section
    from repowise.core.analysis.health.perf.coverage import PerfCoverage
    from repowise.core.persistence.crud.analysis.performance import PerformanceHeadline

    coverage = PerfCoverage(
        analyzed_files=10, covered_files=9, covered_nloc=9000, total_nloc=10000
    )
    report = SimpleNamespace(kpis={"performance_average": 9.7})
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch("repowise.cli.commands.health_cmd.summary.console", console):
        _render_performance_section(report, coverage, PerformanceHeadline(4, 415, 926, 9.03))
    out = console.file.getvalue()
    assert (
        "4 causes worth fixing now · 415 open in production code · 926 overall · "
        "9.03/10K covered LOC · avg score 9.7/10" in out
    )
