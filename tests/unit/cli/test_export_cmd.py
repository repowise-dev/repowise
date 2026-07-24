"""Tests for the export CLI command wiring."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands import export_cmd


def test_export_verbose_flag_reaches_configure_cli_logging(tmp_path, monkeypatch):
    """`--verbose/-v` must reach configure_cli_logging via CliRunner (not .callback)."""
    from click.testing import CliRunner
    from repowise.cli.main import cli

    calls: list[bool] = []
    monkeypatch.setattr(
        export_cmd, "configure_cli_logging", lambda *, verbose: calls.append(verbose)
    )
    monkeypatch.setattr(export_cmd, "resolve_repo_path", lambda _p: tmp_path)
    monkeypatch.setattr(export_cmd, "ensure_repowise_dir", lambda _p: tmp_path / ".repowise")
    monkeypatch.setattr(export_cmd, "run_async", lambda coro: coro.close())

    result = CliRunner().invoke(cli, ["export", str(tmp_path), "-v"])
    assert result.exit_code == 0, result.output
    assert calls == [True]

    calls.clear()
    result = CliRunner().invoke(cli, ["export", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls == [False]
