"""``repowise health`` reads every stored finding the score counted, and lists
only what a default surface lists."""

from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands.health_cmd import command as health_module
from repowise.cli.main import cli
from repowise.core.analysis.health.models import HealthFileMetricData


def _finding(kind: str, impact: float) -> SimpleNamespace:
    return SimpleNamespace(
        biomarker_type=kind,
        severity="high",
        file_path="src/a.py",
        function_name="run",
        line_start=1,
        line_end=9,
        health_impact=impact,
        details_json=None,
        details={},
        reason=f"{kind} reason",
        dimension="defect",
        status="open",
    )


def _run(monkeypatch, *args: str) -> tuple[str, str]:
    metric = HealthFileMetricData(
        file_path="src/a.py", score=7.0, max_ccn=12, max_nesting=2, nloc=40,
        has_test_file=False, defect_score=7.0,
    )
    stored = [_finding("complex_method", 2.0), _finding("dry_violation", 1.0)]

    @asynccontextmanager
    async def _session(_path):
        yield object(), "repo-1"

    async def _findings(_session, _repo_id, **kwargs):
        if kwargs.get("include_withheld"):
            return stored
        return [f for f in stored if f.biomarker_type != "dry_violation"]

    async def _empty(*_a, **_k):
        return {}

    async def _metrics(*_a, **_k):
        return [metric]

    monkeypatch.setattr("repowise.cli.helpers.repo_index_session", _session)
    monkeypatch.setattr("repowise.core.persistence.crud.get_health_findings", _findings)
    monkeypatch.setattr("repowise.core.persistence.crud.get_health_metrics", _metrics)
    monkeypatch.setattr("repowise.core.persistence.crud.get_hotspot_file_paths", _empty)
    monkeypatch.setattr("repowise.core.persistence.crud.get_file_language_map", _empty)
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".repowise").mkdir()
        with (
            patch.object(health_module, "resolve_command_target") as resolve,
            patch.object(health_module, "_load_fix_first", return_value=None),
            patch.object(health_module, "_load_queue_counts", return_value=None),
            patch.object(health_module, "_load_short_history", return_value=set()),
            patch.object(health_module, "console", console),
        ):
            resolve.return_value = SimpleNamespace(
                is_workspace=False, repo_path=Path("."), notice=lambda *a, **k: None
            )
            result = runner.invoke(cli, ["health", *args])
    assert result.exit_code == 0, result.output
    return result.stdout, console.file.getvalue()


def test_json_keeps_withheld_findings_so_it_explains_its_score(monkeypatch) -> None:
    stdout, _ = _run(monkeypatch, "--format", "json")
    payload = json.loads(stdout[stdout.index("{"):])
    by_kind = {f["biomarker_type"]: f for f in payload["findings"]}
    assert set(by_kind) == {"complex_method", "dry_violation"}
    assert by_kind["dry_violation"]["withheld"] is True
    assert "withheld" not in by_kind["complex_method"]


def test_table_lists_only_served_findings(monkeypatch) -> None:
    _, out = _run(monkeypatch)
    assert "complex_method" in out
    assert "dry_violation" not in out
