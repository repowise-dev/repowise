"""Tests for the export CLI command wiring."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands import export_cmd


def test_export_command_forwards_verbose_to_configure_cli_logging(monkeypatch, tmp_path: Path) -> None:
    """`--verbose` must reach configure_cli_logging before any pipeline work."""
    seen: dict[str, object] = {}

    def fake_configure(*, verbose: bool = False) -> None:
        seen["verbose"] = verbose

    def fake_resolve_repo_path(_path: str | None) -> Path:
        return tmp_path

    def fake_ensure_repowise_dir(_repo_path: Path) -> Path:
        return tmp_path / ".repowise"

    def fake_run_async(_coro) -> None:
        _coro.close()

    monkeypatch.setattr(export_cmd, "configure_cli_logging", fake_configure)
    monkeypatch.setattr(export_cmd, "resolve_repo_path", fake_resolve_repo_path)
    monkeypatch.setattr(export_cmd, "ensure_repowise_dir", fake_ensure_repowise_dir)
    monkeypatch.setattr(export_cmd, "run_async", fake_run_async)

    export_cmd.export_command.callback(
        path=None, fmt="markdown", output_dir=None, full_export=False, verbose=True
    )
    assert seen.get("verbose") is True

    seen.clear()
    export_cmd.export_command.callback(
        path=None, fmt="markdown", output_dir=None, full_export=False, verbose=False
    )
    assert seen.get("verbose") is False
