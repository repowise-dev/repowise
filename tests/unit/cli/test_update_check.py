"""Tests for the CLI update-check helper (``repowise.cli.update_check``)."""

from __future__ import annotations

import httpx
import pytest

from repowise.cli import update_check as uc

# --- version comparison ----------------------------------------------------


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("0.15.2", "0.13.0", True),
        ("0.15.2", "0.15.2", False),
        ("0.15.10", "0.15.2", True),
        ("0.13.0", "0.15.2", False),
        ("0.15", "0.15.2", False),  # 0.15.0 < 0.15.2
        ("0.16", "0.15.2", True),
    ],
)
def test_is_newer_version(latest: str, current: str, expected: bool) -> None:
    assert uc.is_newer_version(latest, current) is expected


@pytest.mark.parametrize("latest", ["", "not-a-version", "abc"])
def test_is_newer_version_unparseable_returns_false(latest: str) -> None:
    # Invalid/missing latest yields no update decision instead of crashing.
    assert uc.is_newer_version(latest, "0.15.2") is False


# --- install-method suggestion ---------------------------------------------


def test_suggest_uv_tool_path() -> None:
    cmd, hint = uc.suggest_update_command(
        "/home/user/.local/share/uv/tools/repowise/bin/repowise", "/usr/bin/python"
    )
    assert cmd == "uv tool upgrade repowise"
    assert hint == "uv tool"


def test_suggest_pipx_path() -> None:
    cmd, hint = uc.suggest_update_command(
        "/home/user/.local/pipx/venvs/repowise/bin/repowise", "/usr/bin/python"
    )
    assert cmd == "pipx upgrade repowise"
    assert hint == "pipx"


def test_suggest_pip_fallback() -> None:
    cmd, hint = uc.suggest_update_command("/usr/local/bin/repowise", "/opt/py/bin/python")
    assert cmd == "/opt/py/bin/python -m pip install -U repowise"
    assert hint == "pip"


def test_suggest_pip_fallback_when_executable_unknown() -> None:
    cmd, hint = uc.suggest_update_command(None, "/opt/py/bin/python")
    assert cmd == "/opt/py/bin/python -m pip install -U repowise"
    assert hint == "pip"


# --- get_cli_update_check --------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _patch_no_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the non-editable path so the suggested command is deterministic.
    monkeypatch.setattr(uc, "_editable_checkout", lambda: None)


def test_detects_update_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_no_editable(monkeypatch)
    monkeypatch.setattr("repowise.cli.__version__", "0.13.0", raising=False)
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"info": {"version": "0.15.2"}})
    )

    result = uc.get_cli_update_check(timeout=0.1)

    assert result.current_version == "0.13.0"
    assert result.latest_version == "0.15.2"
    assert result.update_available is True
    assert result.error is None


def test_no_update_when_current_is_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_no_editable(monkeypatch)
    monkeypatch.setattr("repowise.cli.__version__", "0.15.2", raising=False)
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"info": {"version": "0.15.2"}})
    )

    result = uc.get_cli_update_check(timeout=0.1)

    assert result.update_available is False


def test_network_error_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_no_editable(monkeypatch)

    def _boom(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", _boom)

    result = uc.get_cli_update_check(timeout=0.1)

    assert result.latest_version is None
    assert result.update_available is None
    assert result.error is not None


def test_unparsable_latest_is_treated_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_editable(monkeypatch)
    monkeypatch.setattr("repowise.cli.__version__", "0.15.2", raising=False)
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"info": {"version": "garbage"}})
    )

    result = uc.get_cli_update_check(timeout=0.1)

    # An uncomparable PyPI version must not masquerade as "up to date".
    assert result.latest_version is None
    assert result.update_available is None
    assert result.error is not None


def test_records_resolved_and_running_executables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_editable(monkeypatch)
    monkeypatch.setattr(uc.shutil, "which", lambda name: "/usr/local/bin/repowise")
    monkeypatch.setattr(uc.sys, "argv", ["/tmp/venv/bin/repowise", "doctor"])
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"info": {"version": "0.15.2"}})
    )

    result = uc.get_cli_update_check(timeout=0.1)

    assert result.resolved_executable == "/usr/local/bin/repowise"
    assert result.running_executable == "/tmp/venv/bin/repowise"


def test_editable_checkout_suggests_pip_editable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(uc, "_editable_checkout", lambda: tmp_path)
    monkeypatch.setattr(uc.sys, "executable", "/opt/py/bin/python")
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse({"info": {"version": "0.15.2"}})
    )

    result = uc.get_cli_update_check(timeout=0.1)

    assert result.install_hint == "editable"
    assert "git pull" in result.suggested_command
    assert "pip install -e ." in result.suggested_command
