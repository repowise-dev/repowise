"""Tests for the advisory CLI version row in ``repowise doctor``."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from repowise.cli.commands import doctor_cmd
from repowise.cli.update_check import UpdateCheck


def _capture(monkeypatch: pytest.MonkeyPatch, check: UpdateCheck) -> str:
    buf = io.StringIO()
    monkeypatch.setattr(doctor_cmd, "console", Console(file=buf, width=200))
    monkeypatch.setattr("repowise.cli.update_check.get_cli_update_check", lambda *a, **k: check)
    doctor_cmd._print_cli_version_status()
    return buf.getvalue()


def _make(**overrides) -> UpdateCheck:
    base = dict(
        current_version="0.13.0",
        latest_version="0.15.2",
        resolved_executable="/usr/local/bin/repowise",
        running_executable="/tmp/venv/bin/repowise",
        python="/usr/bin/python",
        update_available=True,
        suggested_command="/usr/bin/python -m pip install -U repowise",
        install_hint="pip",
        error=None,
    )
    base.update(overrides)
    return UpdateCheck(**base)


def test_update_available_shows_warn_and_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _capture(monkeypatch, _make())
    assert "CLI version" in out
    assert "WARN" in out
    assert "current 0.13.0" in out
    assert "latest 0.15.2" in out
    assert "/usr/local/bin/repowise" in out  # resolved path
    assert "/tmp/venv/bin/repowise" in out  # full running command (may differ)
    assert "pip install -U repowise" in out
    assert "Restart" in out


def test_up_to_date_shows_ok_no_command(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _capture(
        monkeypatch,
        _make(current_version="0.15.2", latest_version="0.15.2", update_available=False),
    )
    assert "OK" in out
    assert "WARN" not in out
    assert "(latest)" in out
    assert "Restart" not in out


def test_latest_unknown_stays_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _capture(
        monkeypatch,
        _make(latest_version=None, update_available=None, error="no network"),
    )
    assert "OK" in out
    assert "WARN" not in out
    assert "could not check latest version" in out
    assert "Restart" not in out


def test_never_raises_when_check_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    buf = io.StringIO()
    monkeypatch.setattr(doctor_cmd, "console", Console(file=buf, width=200))

    def _boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("repowise.cli.update_check.get_cli_update_check", _boom)
    # Should swallow the error and print nothing rather than crash doctor.
    doctor_cmd._print_cli_version_status()
    assert buf.getvalue() == ""
