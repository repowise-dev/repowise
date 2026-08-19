"""Unit tests for ClaudeCliProvider.

All tests mock the Claude subprocess; no real Claude CLI calls are made.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.claude_cli import (
    ClaudeCliProvider,
    _parse_result,
    _subprocess_env,
)


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        on_communicate: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._on_communicate = on_communicate
        self._transport = transport
        self.stdin_input: bytes | None = None
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_input = input
        if self._on_communicate is not None:
            await self._on_communicate()
        return self._stdout.encode("utf-8"), self._stderr.encode("utf-8")

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


class FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _result_json(
    text: str = "OK",
    *,
    is_error: bool = False,
    subtype: str = "success",
    usage: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": text,
        "session_id": "s1",
    }
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload)


_USAGE = {
    "input_tokens": 120,
    "cache_read_input_tokens": 30,
    "output_tokens": 40,
}


def _which_claude(cmd: str) -> str | None:
    return "claude" if cmd == "claude" else None


def test_provider_name_and_default_model(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    provider = ClaudeCliProvider(repo_path=tmp_path)

    assert provider.provider_name == "claude_cli"
    assert provider.model_name == "claude_cli/default"


def test_custom_model_is_normalized_for_attribution(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    assert ClaudeCliProvider(model="opus", repo_path=tmp_path).model_name == "claude_cli/opus"
    assert (
        ClaudeCliProvider(model="claude_cli/opus", repo_path=tmp_path).model_name
        == "claude_cli/opus"
    )
    assert (
        ClaudeCliProvider(model="claude_cli/default", repo_path=tmp_path).model_name
        == "claude_cli/default"
    )


def test_missing_cli_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    with pytest.raises(ProviderError, match="Claude Code CLI not found"):
        ClaudeCliProvider(repo_path=tmp_path)


def test_available_model_options_lead_with_cli_default(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    options = ClaudeCliProvider(repo_path=tmp_path).available_model_options()

    assert options[0].model == "claude_cli/default"
    assert options[0].recommended is True
    assert {option.model for option in options[1:]} == {
        "claude_cli/sonnet",
        "claude_cli/opus",
        "claude_cli/haiku",
    }


def test_parse_result_reads_verified_payload_shape():
    content, usage, is_error, detail = _parse_result(_result_json("hello", usage=_USAGE))

    assert content == "hello"
    assert usage == _USAGE
    assert is_error is False
    assert detail is None


def test_parse_result_skips_wrapper_noise_lines():
    stdout = "npm warn deprecated something\n" + _result_json("hello", usage=_USAGE) + "\n"

    content, usage, is_error, _ = _parse_result(stdout)

    assert content == "hello"
    assert usage == _USAGE
    assert is_error is False


def test_parse_result_flags_error_payload():
    content, _usage, is_error, detail = _parse_result(
        _result_json("Credit balance too low", is_error=True, subtype="error_during_execution")
    )

    assert is_error is True
    assert detail == "Credit balance too low"
    assert content == "Credit balance too low"


def test_subprocess_env_scrubs_api_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-test")
    monkeypatch.setenv("UNRELATED_VAR", "kept")

    env = _subprocess_env()

    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["UNRELATED_VAR"] == "kept"


async def test_generate_invokes_claude_p_with_stdin(monkeypatch, tmp_path):
    claude_cmd = str(tmp_path / "bin" / "claude.CMD")
    monkeypatch.setattr("shutil.which", lambda cmd: claude_cmd if cmd == "claude" else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-scrubbed")
    captured: dict[str, Any] = {}
    proc = FakeProcess(stdout=_result_json("Hello from Claude", usage=_USAGE))

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    provider = ClaudeCliProvider(model="opus", repo_path=tmp_path)
    result = await provider.generate("system rules", "user context", reasoning="high")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Claude"
    assert proc.stdin_input is not None
    assert proc.stdin_input.decode("utf-8") == "user context"
    args = list(captured["args"])
    assert args[:4] == [claude_cmd, "-p", "--output-format", "json"]
    assert "--no-session-persistence" in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--system-prompt") + 1] == "system rules"
    assert args[args.index("--model") + 1] == "opus"
    assert args[args.index("--effort") + 1] == "high"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())
    assert "ANTHROPIC_API_KEY" not in captured["kwargs"]["env"]


async def test_generate_auto_reasoning_omits_effort(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **_kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess(stdout=_result_json(usage=_USAGE))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user", reasoning="auto")

    assert "--effort" not in captured["args"]


async def test_generate_maps_minimal_reasoning_to_low_effort(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **_kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess(stdout=_result_json(usage=_USAGE))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user", reasoning="minimal")

    args = list(captured["args"])
    assert args[args.index("--effort") + 1] == "low"


async def test_generate_uses_result_usage(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_result_json(usage=_USAGE))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.input_tokens == 120
    assert result.output_tokens == 40
    assert result.cached_tokens == 30
    assert result.usage["source"] == "claude_p"
    assert "estimated" not in result.usage


async def test_generate_marks_missing_usage_as_estimated(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_result_json("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.usage["estimated"] is True


async def test_generate_closes_subprocess_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)
    transport = FakeTransport()

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_result_json(usage=_USAGE), transport=transport)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert transport.closed is True


async def test_generate_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="not logged in"):
        await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_hides_structured_error_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=2, stdout='{"error":{"message":"secret details"}}')

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="claude -p exited with 2") as exc_info:
        await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert "secret details" not in str(exc_info.value)


async def test_generate_raises_on_error_result(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(
            stdout=_result_json(
                "Credit balance too low",
                is_error=True,
                subtype="error_during_execution",
            )
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="Credit balance too low"):
        await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_raises_on_empty_result(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_result_json("", usage=_USAGE))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="empty result"):
        await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_serializes_subprocess_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)
    active = 0
    max_active = 0

    async def on_communicate() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_result_json(usage=_USAGE), on_communicate=on_communicate)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    provider = ClaudeCliProvider(repo_path=tmp_path)

    await asyncio.gather(
        provider.generate("sys", "user 1"),
        provider.generate("sys", "user 2"),
    )

    assert max_active == 1


async def test_generate_times_out_and_kills_claude(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", _which_claude)
    monkeypatch.setattr(
        "repowise.core.providers.llm.claude_cli._EXEC_TIMEOUT_SECONDS",
        0.01,
    )

    async def on_communicate() -> None:
        await asyncio.sleep(1)

    proc = FakeProcess(stdout="", on_communicate=on_communicate)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="timed out"):
        await ClaudeCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert proc.killed
