from __future__ import annotations

from typing import Any

from repowise.cli.ui import provider_selection as ui
from repowise.core.agents.identity import AgentIdentity


def test_detect_provider_status_requires_codex_login(monkeypatch: Any) -> None:
    monkeypatch.setattr(AgentIdentity, "is_installed", lambda self: True)
    monkeypatch.setattr(AgentIdentity, "is_logged_in", lambda self: self.slug != "codex")

    assert "codex_cli" not in ui._detect_provider_status()


def test_detect_provider_status_accepts_authenticated_codex(monkeypatch: Any) -> None:
    monkeypatch.setattr(AgentIdentity, "is_installed", lambda self: True)
    monkeypatch.setattr(AgentIdentity, "is_logged_in", lambda self: True)

    assert "codex_cli" in ui._detect_provider_status()
