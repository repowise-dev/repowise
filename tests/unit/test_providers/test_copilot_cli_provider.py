"""Unit tests for CopilotCliProvider; the CLI subprocess is mocked."""

from __future__ import annotations

import json
from typing import Any

import pytest

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.copilot_cli import CopilotCliProvider


def _jsonl(*events: dict[str, Any]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def _captured_jsonl(text: str = "PONG") -> str:
    return _jsonl(
        {
            "type": "assistant.message",
            "data": {"content": text, "toolRequests": []},
        },
        {
            "type": "session.usage_checkpoint",
            "data": {
                "accountingSnapshot": {
                    "lastCallInputTokens": 3425,
                    "lastCallOutputTokens": 5,
                    "totalPremiumRequestCost": 1,
                    "tokenDetails": {"cache_read": {"tokenCount": 0}},
                }
            },
        },
    )


def test_build_command_uses_jsonl_and_exposes_no_tools(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/copilot")
    provider = CopilotCliProvider(model="gpt-5.5", repo_path=tmp_path)

    command = provider.build_command("system", "auto")

    assert command[0] == "/usr/bin/copilot"
    assert "--output-format" in command
    assert command[command.index("--output-format") + 1] == "json"
    assert "--no-ask-user" in command
    assert "--available-tools=repowise_no_tools" in command
    assert "--disable-builtin-mcps" in command
    assert "--no-custom-instructions" in command
    assert "--allow-all-tools" not in command
    assert "-p" not in command
    assert command[command.index("-C") + 1] == str(tmp_path.resolve())
    assert command[command.index("--model") + 1] == "gpt-5.5"


def test_build_stdin_combines_system_and_user_prompts(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/copilot")
    provider = CopilotCliProvider(repo_path=tmp_path)

    prompt = provider.build_stdin("system rules", "user context")

    assert "system rules" in prompt
    assert "user context" in prompt
    assert "You have no tools available" in prompt


def test_parse_captured_jsonl_and_usage(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/copilot")
    provider = CopilotCliProvider(repo_path=tmp_path)

    result = provider.parse_output(_captured_jsonl("answer"), "")

    assert result.content == "answer"
    assert result.input_tokens == 3425
    assert result.output_tokens == 5
    assert result.cached_tokens == 0
    assert result.usage["source"] == "copilot_cli_jsonl"
    assert result.usage["totalPremiumRequestCost"] == 1


def test_parse_output_marks_missing_usage_as_estimated(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/copilot")
    provider = CopilotCliProvider(repo_path=tmp_path)

    result = provider.parse_output(
        _jsonl({"type": "assistant.message", "data": {"content": "ok"}}),
        "",
    )

    assert result.content == "ok"
    assert result.input_tokens == result.output_tokens == result.cached_tokens == 0
    assert result.usage["estimated"] is True


def test_parse_output_requires_assistant_text(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/copilot")
    provider = CopilotCliProvider(repo_path=tmp_path)

    with pytest.raises(ProviderError, match="no text was found"):
        provider.parse_output(_jsonl({"type": "session.usage_checkpoint", "data": {}}), "")