"""Provider diagnostics distinguish availability from valid configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd import repo_checks
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
    ok, detail = rows["LLM provider"]
    assert ok is True
    assert detail.startswith("None configured: ")
    assert "a provider key or a local model" in detail


def test_keyless_repo_names_a_signed_in_plan(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.index_scope import FreeRoute

    _patch_provider_state(monkeypatch, available=False)
    monkeypatch.setattr(
        "repowise.cli.ui.subscription.upgrade_route",
        lambda _repo: FreeRoute("claude_cli", "Claude Code"),
    )
    detail = _rows(tmp_path)["LLM provider"][1]
    assert "uses your Claude Code sign-in and plan quota" in detail


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("repowise.cli.platform.credentials.load", lambda: {"stale": True})

    check = providers._hosted_account_check()

    assert check.level == "warn"
    assert check.ok is True
    assert check.fix == "repowise login"
