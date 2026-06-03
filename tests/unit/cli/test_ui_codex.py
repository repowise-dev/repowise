from __future__ import annotations

from typing import Any

from repowise.cli.ui import provider_selection as ui


def test_detect_provider_status_requires_codex_login(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "repowise.cli.mcp_config.is_codex_cli_installed",
        lambda: True,
    )
    monkeypatch.setattr(
        "repowise.cli.mcp_config.is_codex_logged_in",
        lambda: False,
    )

    assert "codex_cli" not in ui._detect_provider_status()


def test_detect_provider_status_accepts_authenticated_codex(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "repowise.cli.mcp_config.is_codex_cli_installed",
        lambda: True,
    )
    monkeypatch.setattr(
        "repowise.cli.mcp_config.is_codex_logged_in",
        lambda: True,
    )

    assert ui._detect_provider_status()["codex_cli"] == "codex CLI"
