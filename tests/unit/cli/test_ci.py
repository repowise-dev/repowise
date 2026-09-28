"""The CLI side of CI gates: exit codes, output channels, the step summary."""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from repowise.cli.ci import (
    EXIT_CANNOT_EVALUATE,
    append_step_summary,
    cannot_evaluate,
)


def _invoke(fmt: str):
    @click.command()
    def cmd() -> None:
        cannot_evaluate(fmt, "no_report", "No report: 5%")

    return CliRunner().invoke(cmd)


def test_cannot_evaluate_json_is_one_document() -> None:
    result = _invoke("json")
    assert result.exit_code == EXIT_CANNOT_EVALUATE
    assert json.loads(result.stdout) == {"error": "no_report", "message": "No report: 5%"}


def test_cannot_evaluate_github_emits_an_error_command() -> None:
    result = _invoke("github")
    assert result.exit_code == EXIT_CANNOT_EVALUATE
    assert "::error::No report: 5%25" in result.stdout


@pytest.mark.parametrize("fmt", ["table", "markdown"])
def test_cannot_evaluate_other_formats_exit_2(fmt: str) -> None:
    assert _invoke(fmt).exit_code == EXIT_CANNOT_EVALUATE


def test_append_step_summary(tmp_path) -> None:
    target = tmp_path / "summary.md"
    assert append_step_summary("# a", env={"GITHUB_STEP_SUMMARY": str(target)}) is True
    assert append_step_summary("b\n\n", env={"GITHUB_STEP_SUMMARY": str(target)}) is True
    assert target.read_text(encoding="utf-8") == "# a\nb\n"
    assert append_step_summary("x", env={}) is False
    missing = tmp_path / "no" / "dir.md"
    assert append_step_summary("x", env={"GITHUB_STEP_SUMMARY": str(missing)}) is False
