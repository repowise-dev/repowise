"""The per-test coverage command the CLI recommends must be one that works (#3598).

coverage.py's ``run`` has no ``--contexts`` option, so a hint built on it fails
on the first copy-paste and no per-test map is ever recorded.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands import coverage_cmd, impacted_tests_cmd
from repowise.cli.main import cli
from repowise.core.analysis.health.coverage.contexts import PER_TEST_COVERAGE_COMMAND


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_recommended_command_uses_pytest_cov_contexts() -> None:
    assert "--contexts" not in PER_TEST_COVERAGE_COMMAND
    assert "--cov-context=test" in PER_TEST_COVERAGE_COMMAND


def test_coverage_add_no_report_message_uses_the_shared_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", lambda _p: tmp_path)
    monkeypatch.setattr(coverage_cmd, "ensure_repowise_dir", lambda _p: None)
    monkeypatch.setattr(coverage_cmd, "_discover_context_reports", lambda _p: [])

    result = CliRunner().invoke(cli, ["coverage", "add"])

    output = _flat(result.output)
    assert "No coverage report found" in output
    assert PER_TEST_COVERAGE_COMMAND in output
    assert "--contexts" not in output


def test_impacted_tests_empty_map_message_uses_the_shared_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = StringIO()
    monkeypatch.setattr(
        impacted_tests_cmd, "console", Console(file=buffer, width=400, no_color=True)
    )

    impacted_tests_cmd._print_map_state({"map_empty": True, "map_current": False})

    output = _flat(buffer.getvalue())
    assert "No test-to-code map ingested" in output
    assert PER_TEST_COVERAGE_COMMAND in output
    assert "--contexts" not in output


@pytest.mark.parametrize("module", [coverage_cmd, impacted_tests_cmd])
def test_cli_messages_never_name_the_nonexistent_flag(module: object) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "--contexts" not in source
