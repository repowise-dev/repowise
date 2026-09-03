"""Tests for the ``coverage add`` command boundary."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands import coverage_cmd
from repowise.cli.main import cli


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_coverage_add_configures_logging_before_repo_resolution(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, bool | None]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_resolve_coverage_repo(_path: str | None) -> None:
        events.append(("repo", None))
        raise click.ClickException("stop after repo resolution")

    monkeypatch.setattr(coverage_cmd, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", fake_resolve_coverage_repo)

    result = CliRunner().invoke(cli, ["coverage", "add", *extra_args])

    assert result.exit_code == 1
    assert "stop after repo resolution" in result.output
    assert events == [("logging", expected_verbose), ("repo", None)]


def test_coverage_add_help_lists_verbose() -> None:
    result = CliRunner().invoke(cli, ["coverage", "add", "--help"])

    assert result.exit_code == 0
    assert "--verbose" in result.output


def _no_discovered_reports(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Point the command at an empty repo so auto-discovery finds nothing."""
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", lambda _p: tmp_path)
    monkeypatch.setattr(coverage_cmd, "ensure_repowise_dir", lambda _p: None)
    monkeypatch.setattr(coverage_cmd, "_discover_context_reports", lambda _p: [])


def test_coverage_add_exits_non_zero_when_it_discovers_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    A refresh is scripted as `coverage add ... || exit 1`, so a run that stored
    nothing has to be distinguishable from a complete one by its exit status.
    """
    _no_discovered_reports(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli, ["coverage", "add"])

    assert result.exit_code == 1
    assert "No coverage report found" in result.output


@pytest.mark.parametrize(("stored", "expected_exit"), [(True, 0), (False, 1)])
def test_coverage_add_exit_status_follows_whether_anything_was_stored(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stored: bool, expected_exit: int
) -> None:
    """
    Every no-op branch inside the ingest returns False -- no index, no indexed
    files, nothing mapped, or --strict with unmapped report files. This pins
    the wiring that turns that answer into the process exit status.
    """
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", lambda _p: tmp_path)
    monkeypatch.setattr(coverage_cmd, "ensure_repowise_dir", lambda _p: None)

    def fake_run_async(coro):
        coro.close()  # never awaited; the DB is not part of this test
        return stored

    monkeypatch.setattr(coverage_cmd, "run_async", fake_run_async)

    report = tmp_path / "lcov.info"
    report.write_text("TN:", encoding="utf-8")

    result = CliRunner().invoke(cli, ["coverage", "add", str(report)])

    assert result.exit_code == expected_exit


def _coverage_add_source() -> str:
    """Source of the `coverage add` callback (Click wraps it in a Command)."""
    import inspect

    return inspect.getsource(coverage_cmd.coverage_add.callback)


def test_strict_counts_unmapped_before_the_resolved_files_branch() -> None:
    """Total mapping loss on the aggregate leg must still be able to trip --strict.

    The count used to be read inside `if resolved.files:`, so the one run where
    every report file failed to map was also the run that saw zero unmapped
    files. With a per-test map present, `map_records` is non-empty, the
    "nothing mapped" branch is skipped, and a --strict run exited 0 having
    stored no per-file coverage at all.

    This is a structural guard, not a behavioural one: the branch lives in the
    async closure that needs a DB session, so this pins the ordering that makes
    the branch reachable rather than the branch itself.
    """
    source = _coverage_add_source()

    assert source.index("unmapped = len(skipped)") < source.index("if resolved.files:"), (
        "the unmapped count must be computed before the resolved.files branch"
    )


def test_strict_failure_is_reported_before_the_success_guidance() -> None:
    """A failing --strict run must not first advise running `repowise health`."""
    source = _coverage_add_source()

    assert source.index("if strict and unmapped:") < source.index(
        "to fold coverage into the defect "
    )


def test_coverage_add_help_documents_strict() -> None:
    result = CliRunner().invoke(cli, ["coverage", "add", "--help"])

    assert result.exit_code == 0
    assert "--strict" in result.output
