"""``serve`` re-checks release currency while it runs, announcing each once."""

from __future__ import annotations

import pytest

from repowise.cli.commands import serve_cmd
from repowise.cli.update_check import UpdateCheck


def _check(latest: str | None, current: str = "0.48.0") -> UpdateCheck:
    available = None if latest is None else latest != current
    return UpdateCheck(
        current_version=current,
        latest_version=latest,
        resolved_executable=None,
        running_executable="repowise",
        python="python",
        update_available=available,
        suggested_command="pip install -U repowise",
        install_hint="pip",
    )


def test_tick_announces_a_newer_release_once(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "repowise.cli.update_check.get_cli_update_check_cached", lambda: _check("9.9.9")
    )
    announced: set[str] = set()
    assert serve_cmd._update_advisory_tick(announced) is True
    assert "9.9.9" in capsys.readouterr().out
    assert serve_cmd._update_advisory_tick(announced) is False
    assert capsys.readouterr().out == ""


def test_tick_is_quiet_when_current_or_unknown(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    for latest in ("0.48.0", None):
        monkeypatch.setattr(
            "repowise.cli.update_check.get_cli_update_check_cached", lambda latest=latest: _check(latest)
        )
        assert serve_cmd._update_advisory_tick(set()) is False
    assert capsys.readouterr().out == ""


def test_tick_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("network")

    monkeypatch.setattr("repowise.cli.update_check.get_cli_update_check_cached", _boom)
    assert serve_cmd._update_advisory_tick(set()) is False
