"""Provider diagnostics distinguish availability from valid configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.doctor_cmd import doctor_command, repo_checks
from repowise.cli.commands.doctor_cmd._checks import providers


def _rows(repo_path: Path) -> dict[str, tuple[bool, str]]:
    return {
        check.name: (check.ok, check.detail) for check in repo_checks._provider_checks(repo_path)
    }


def _patch_provider_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available: bool,
    warnings: list[str] | None = None,
) -> None:
    monkeypatch.setattr(
        "repowise.core.providers.list_providers",
        lambda: ["gemini", "openai"],
    )
    monkeypatch.setattr(
        "repowise.core.providers.llm.registry.provider_available_for_repo",
        lambda _repo_path: available,
    )
    monkeypatch.setattr(
        "repowise.cli.helpers.validate_provider_config",
        lambda **_kw: warnings or [],
    )


def test_keyless_repo_reports_structural_fallback_without_failing_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_provider_state(monkeypatch, available=False)

    rows = _rows(tmp_path)

    assert rows["Providers"] == (True, "Implementations loaded: gemini, openai")
    assert rows["Provider config"] == (True, "No misconfigured provider keys")
    assert rows["LLM provider"] == (
        True,
        "None configured — prose degrades to a structural wiki; set a key or pass --provider",
    )


def test_resolvable_provider_is_reported_for_this_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_provider_state(monkeypatch, available=True)

    assert _rows(tmp_path)["LLM provider"] == (True, "Resolves for this repository")


def test_misconfigured_provider_key_still_fails_its_own_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_provider_state(
        monkeypatch,
        available=False,
        warnings=["openai requires environment variables: OPENAI_API_KEY"],
    )

    assert _rows(tmp_path)["Provider config"] == (
        False,
        "openai requires environment variables: OPENAI_API_KEY",
    )


def test_stale_hosted_session_warns_without_failing_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("repowise.cli.platform.credentials.load", lambda: {"stale": True})

    check = providers._hosted_account_check()

    assert check.level == "warn"
    assert check.ok is True
    assert check.fix == "repowise login"

    monkeypatch.setattr(
        "repowise.cli.commands.doctor_cmd.command._run_repo_checks",
        lambda repo_path, repair, fmt="table", quick=False: (check.ok, [check]),
    )
    result = CliRunner().invoke(
        doctor_command,
        [str(tmp_path), "--no-workspace", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
