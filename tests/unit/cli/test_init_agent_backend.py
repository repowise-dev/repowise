"""Init's provider picker defaults to the CLI of the agent set up in the repo."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from repowise.cli.agent_targets import registry
from repowise.cli.commands.init_cmd.command import _interactive_gate
from repowise.cli.ui import provider_selection as ui
from repowise.core.agents.identity import identity_for_target_id


class _Target:
    def __init__(self, present: bool) -> None:
        self.present = present

    def is_present(self, repo_path: Path | None = None) -> bool:
        return self.present

    def detect(self, repo_path: Path | None = None) -> list[Any]:
        return []


def _set_up(monkeypatch: Any, *target_ids: str) -> None:
    monkeypatch.setattr(
        registry, "get_target", lambda target_id: _Target(target_id in target_ids)
    )


def _default_pick(
    monkeypatch: Any, detected: dict[str, str], prefer: tuple[str, ...]
) -> tuple[str, str]:
    """The provider the picker offers as its default, and what it printed."""
    monkeypatch.setattr(ui, "_detect_provider_status", lambda: detected)
    seen: dict[str, str] = {}

    def ask(*_a: object, default: str, **_k: object) -> str:
        seen["default"] = default
        return default

    monkeypatch.setattr(ui.Prompt, "ask", ask)
    buf = io.StringIO()
    ui._interactive_provider_name(Console(file=buf, width=120), None, prefer=prefer)
    return ui._PROVIDER_CHOICES[int(seen["default"]) - 1], buf.getvalue()


def test_agent_set_up_here_lists_its_provider(tmp_path: Path, monkeypatch: Any) -> None:
    _set_up(monkeypatch, "claude-code")
    assert ui.agent_providers_set_up(tmp_path) == ("claude_cli",)


def test_agent_without_indexing_provider_lists_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    identity = identity_for_target_id("cursor")
    assert identity is not None and identity.indexing_provider is None
    _set_up(monkeypatch, "cursor")
    assert ui.agent_providers_set_up(tmp_path) == ()


def test_configured_provider_suppresses_the_preference(tmp_path: Path, monkeypatch: Any) -> None:
    _set_up(monkeypatch, "claude-code")
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text("provider: gemini\n", encoding="utf-8")
    assert ui.agent_providers_set_up(tmp_path) == ()


def test_ready_agent_cli_beats_a_key_as_default(monkeypatch: Any) -> None:
    detected = {"openai": "OPENAI_API_KEY", "claude_cli": "agent CLI"}
    chosen, out = _default_pick(monkeypatch, detected, ("claude_cli",))
    assert chosen == "claude_cli"
    assert "uses your Claude Code login" in out.split("Provider Setup")[1].split("mock is")[1]


def test_agent_cli_not_ready_leaves_default_alone(monkeypatch: Any) -> None:
    detected = {"openai": "OPENAI_API_KEY"}
    assert _default_pick(monkeypatch, detected, ("claude_cli",))[0] == "openai"


def test_no_preference_keeps_first_ready_default(monkeypatch: Any) -> None:
    detected = {"openai": "OPENAI_API_KEY", "claude_cli": "agent CLI"}
    assert _default_pick(monkeypatch, detected, ())[0] == "openai"


@pytest.mark.parametrize("provider_name", ["claude_cli", "gemini"])
def test_explicit_provider_skips_the_picker(provider_name: str) -> None:
    assert not _interactive_gate(
        isatty=True, provider_name=provider_name, index_only=False, yes=False, resume=False
    )


def test_signed_out_claude_is_not_the_default(monkeypatch: Any) -> None:
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda name: name if name == "claude" else None)
    monkeypatch.setattr(
        subprocess, "run", lambda args, **_k: subprocess.CompletedProcess(args, 1, "", "")
    )
    monkeypatch.setattr(ui, "_detect_ollama_status", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    detected = ui._detect_provider_status()
    assert "claude_cli" not in detected
    chosen, out = _default_pick(monkeypatch, detected, ("claude_cli",))
    assert chosen != "claude_cli"
    assert "Default:" not in out


def test_opencode_reason_does_not_claim_a_login(monkeypatch: Any) -> None:
    chosen, out = _default_pick(monkeypatch, {"opencode": "agent CLI"}, ("opencode",))
    reason = out.split("mock is")[1]
    assert chosen == "opencode"
    assert "uses your opencode CLI setup" in reason
    assert "login" not in reason
