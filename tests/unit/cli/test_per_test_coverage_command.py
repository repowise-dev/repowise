"""The per-test coverage command the CLI recommends (#3598).

coverage.py has no ``--contexts`` flag, so pasting the old suggestion failed.
Every message now uses one shared, working command, and says it needs
pytest-cov, which repowise does not install.
"""

from __future__ import annotations

import inspect

import pytest
from click.testing import CliRunner

from repowise.cli.commands import coverage_cmd, impacted_tests_cmd
from repowise.cli.main import cli
from repowise.core.analysis.health.coverage.contexts import PER_TEST_COVERAGE_COMMAND


def _flat(text: str) -> str:
    # Rich wraps long lines; compare on single spaces.
    return " ".join(text.split())


def test_the_recommended_command_is_a_real_pytest_cov_invocation() -> None:
    assert PER_TEST_COVERAGE_COMMAND == "pytest --cov --cov-context=test"


@pytest.mark.parametrize("module", [coverage_cmd, impacted_tests_cmd])
def test_no_message_suggests_the_nonexistent_contexts_flag(module) -> None:
    assert "--contexts" not in inspect.getsource(module)


def test_coverage_add_no_report_message_uses_the_shared_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", lambda _p: tmp_path)
    monkeypatch.setattr(coverage_cmd, "ensure_repowise_dir", lambda _p: None)
    monkeypatch.setattr(coverage_cmd, "_discover_context_reports", lambda _p: [])

    result = CliRunner().invoke(cli, ["coverage", "add"])

    assert "No coverage report found" in result.output
    assert PER_TEST_COVERAGE_COMMAND in _flat(result.output)
    # pytest-cov is not a repowise dependency, so the message says it is needed.
    assert f"{PER_TEST_COVERAGE_COMMAND}, which needs pytest-cov" in _flat(result.output)
    assert "--contexts" not in result.output


def test_impacted_tests_no_map_message_uses_the_shared_command(capsys) -> None:
    impacted_tests_cmd._print_map_state({"map_empty": True, "map_current": False})

    out = capsys.readouterr().out
    assert "No test-to-code map ingested" in out
    assert PER_TEST_COVERAGE_COMMAND in _flat(out)
    assert f"{PER_TEST_COVERAGE_COMMAND} (needs pytest-cov)" in _flat(out)
    assert "--contexts" not in out
