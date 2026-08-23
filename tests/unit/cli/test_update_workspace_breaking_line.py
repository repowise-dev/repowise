"""The one line `repowise update --workspace` prints when contracts broke."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd.workspace import _print_breaking_changes
from repowise.core.workspace.breaking_change import (
    BreakingChange,
    BreakingChangeReport,
    ImpactedConsumer,
    save_breaking_change_report,
)

STAMP = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _save(root: Path, *, generated_at: str | None, severity: str = "breaking") -> None:
    save_breaking_change_report(
        BreakingChangeReport(
            generated_at=generated_at,
            changes=[
                BreakingChange(
                    kind="removed_endpoint",
                    severity=severity,
                    contract_id="code::@acme/types::Order",
                    contract_type="code",
                    provider_repo="api",
                    provider_file="src/types.ts",
                    provider_symbol="Order",
                    provider_service=None,
                    detail="code::@acme/types::Order was removed",
                    impacted_consumers=[
                        ImpactedConsumer(
                            repo="frontend",
                            service=None,
                            node_id="frontend",
                            file="src/api.ts",
                            symbol="@acme/types:Order",
                            match_type="exact",
                            confidence=0.9,
                        )
                    ],
                )
            ],
        ),
        root,
    )


def test_reports_a_report_this_update_wrote(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _save(tmp_path, generated_at=(STAMP + timedelta(seconds=5)).isoformat())
    _print_breaking_changes(tmp_path, STAMP)
    out = capsys.readouterr().out
    assert "1 contract change(s)" in out
    assert "1 breaking, 0 warning" in out
    assert "frontend" in out


def test_silent_on_a_report_from_a_previous_update(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """A stale artifact belongs to an earlier run and must not be attributed here."""
    _save(tmp_path, generated_at=(STAMP - timedelta(hours=1)).isoformat())
    _print_breaking_changes(tmp_path, STAMP)
    assert capsys.readouterr().out == ""


def test_silent_when_detection_never_ran(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _save(tmp_path, generated_at=None)
    _print_breaking_changes(tmp_path, STAMP)
    assert capsys.readouterr().out == ""


def test_silent_without_a_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _print_breaking_changes(tmp_path, STAMP)
    assert capsys.readouterr().out == ""


def test_warning_only_report_still_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """A warning is a change worth naming; the counts say which kind it was."""
    _save(tmp_path, generated_at=(STAMP + timedelta(seconds=5)).isoformat(), severity="warning")
    _print_breaking_changes(tmp_path, STAMP)
    out = capsys.readouterr().out
    assert "1 contract change(s)" in out
    assert "0 breaking, 1 warning" in out
