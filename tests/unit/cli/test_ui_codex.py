from __future__ import annotations

import subprocess
from typing import Any

from repowise.cli.ui import provider_selection as ui
from repowise.core.agents.identity import AgentIdentity


def _codex_says(monkeypatch: Any, returncode: int, text: str) -> None:
    """Every agent CLI on PATH; only codex answers its status command."""
    monkeypatch.setattr(AgentIdentity, "which", lambda self: self.executable)

    def run(args: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        if args[0] == "codex":
            return subprocess.CompletedProcess(args, returncode, text, "")
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(subprocess, "run", run)


def test_detect_provider_status_requires_codex_login(monkeypatch: Any) -> None:
    _codex_says(monkeypatch, 1, "Not logged in")
    assert "codex_cli" not in ui._detect_provider_status()


def test_detect_provider_status_accepts_authenticated_codex(monkeypatch: Any) -> None:
    _codex_says(monkeypatch, 0, "Logged in using ChatGPT")
    assert "codex_cli" in ui._detect_provider_status()


def test_codex_through_chatgpt_is_picker_only(monkeypatch: Any) -> None:
    """A lapsed ChatGPT plan stays signed in, so this login is never recommended."""
    from repowise.cli.ui import subscription

    _codex_says(monkeypatch, 0, "Logged in using ChatGPT")
    assert "codex_cli" in ui._detect_provider_status()
    assert "codex_cli" not in subscription.ready_agent_providers()
    auth = subscription.agent_cli_auth("codex_cli")
    assert auth is not None and not auth.offer


def test_codex_with_an_api_key_is_offered(monkeypatch: Any) -> None:
    from repowise.cli.ui import subscription

    _codex_says(monkeypatch, 0, "Logged in using an API key - sk-proj-***")
    assert subscription.ready_agent_providers() == ("codex_cli",)
