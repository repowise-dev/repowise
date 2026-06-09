"""Unit tests for OpenCodeProvider.

All tests mock the opencode subprocess; no real opencode CLI calls are made.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.opencode import (
    OpenCodeProvider,
    _parse_models_output,
    _validate_model_name,
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


def _jsonl(*events: dict[str, Any], noise: bool = False) -> str:
    lines = [json.dumps(event) for event in events]
    if noise:
        lines.insert(1, "warning: this is not JSON")
    return "\n".join(lines) + "\n"


def _success_jsonl(text: str = "OK") -> str:
    return _jsonl(
        {"type": "step_start", "sessionID": "s1"},
        {"type": "text", "part": {"text": text}},
        {
            "type": "step_finish",
            "part": {
                "tokens": {
                    "total": 100,
                    "input": 80,
                    "output": 10,
                    "reasoning": 10,
                    "cache": {"write": 0, "read": 20},
                },
            },
        },
        noise=True,
    )


# ---------------------------------------------------------------------------
# Model name validation
# ---------------------------------------------------------------------------


def test_validate_model_name_accepts_valid_names():
    for name in ["big-pickle", "deepseek-v4-pro", "provider/model", "abc.123_x/y-z"]:
        _validate_model_name(name)


def test_validate_model_name_rejects_shell_metacharacters():
    for name in ["bad; ls", "name$(whoami)", "name|cat", "`evil`"]:
        with pytest.raises(ProviderError, match="Invalid model name"):
            _validate_model_name(name)


def test_validate_model_name_rejects_empty():
    with pytest.raises(ProviderError, match="Invalid model name"):
        _validate_model_name("")


def test_validate_model_name_rejects_leading_space():
    with pytest.raises(ProviderError, match="Invalid model name"):
        _validate_model_name(" model")


def test_parse_models_output_extracts_provider_model_pairs():
    output = """
deepseek/deepseek-v4-pro
openai/gpt-5
anthropic/claude-sonnet-4-6
"""
    models = _parse_models_output(output)
    assert models == [
        "deepseek/deepseek-v4-pro",
        "openai/gpt-5",
        "anthropic/claude-sonnet-4-6",
    ]


def test_parse_models_output_ignores_invalid_lines():
    output = """
deepseek/deepseek-v4-pro
some random text
openai/gpt-5

another invalid line
"""
    models = _parse_models_output(output)
    assert models == ["deepseek/deepseek-v4-pro", "openai/gpt-5"]


def test_parse_models_output_empty():
    assert _parse_models_output("") == []
    assert _parse_models_output("   \n  \n") == []


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_name_and_default_model(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    provider = OpenCodeProvider(repo_path=tmp_path)

    assert provider.provider_name == "opencode"
    assert provider.model_name == "opencode/default"


def test_custom_model_is_normalized(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    assert (
        OpenCodeProvider(
            model="opencode/deepseek/deepseek-v4-pro", repo_path=tmp_path
        ).model_name
        == "opencode/deepseek/deepseek-v4-pro"
    )
    assert (
        OpenCodeProvider(model="opencode/default", repo_path=tmp_path).model_name
        == "opencode/default"
    )


def test_invalid_model_name_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    with pytest.raises(ProviderError, match="Invalid model name"):
        OpenCodeProvider(model="bad;name", repo_path=tmp_path)


def test_missing_cli_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    with pytest.raises(ProviderError, match="OpenCode CLI is not installed"):
        OpenCodeProvider(repo_path=tmp_path)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def test_generate_invokes_opencode_with_stdin(monkeypatch, tmp_path):
    opencode_cmd = str(tmp_path / "bin" / "opencode")
    monkeypatch.setattr("shutil.which", lambda _cmd: opencode_cmd)
    captured: dict[str, Any] = {}
    proc = FakeProcess(stdout=_success_jsonl("Hello from OpenCode"))

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    provider = OpenCodeProvider(
        model="opencode/deepseek/deepseek-v4-pro", repo_path=tmp_path
    )
    result = await provider.generate("system rules", "user context")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from OpenCode"
    assert proc.stdin_input is not None
    prompt = proc.stdin_input.decode("utf-8")
    assert "system rules" in prompt
    assert "user context" in prompt
    args = list(captured["args"])
    assert args[:5] == [opencode_cmd, "run", "--format", "json", "--dir"]
    assert "--dangerously-skip-permissions" not in args
    assert args[args.index("--dir") + 1] == str(tmp_path.resolve())
    assert args[args.index("--model") + 1] == "deepseek/deepseek-v4-pro"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    env = captured["kwargs"].get("env", {})
    assert "OPENCODE_CONFIG_CONTENT" in env
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"]["edit"] == "deny"
    assert config["permission"]["bash"] == "deny"


async def test_generate_default_model_omits_model_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess(stdout=_success_jsonl("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")

    args = list(captured["args"])
    assert "--model" not in args


async def test_generate_parses_jsonl_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.content == "OK"
    assert result.input_tokens == 80
    assert result.output_tokens == 10
    assert result.cached_tokens == 20
    assert result.usage["source"] == "opencode_run"
    assert "estimated" not in result.usage


async def test_generate_handles_jsonl_noise(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("Documentation here"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.content == "Documentation here"


async def test_generate_marks_missing_usage_as_estimated(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(
            stdout=_jsonl(
                {"type": "step_start"},
                {"type": "text", "part": {"text": "OK"}},
            )
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.usage["estimated"] is True


async def test_generate_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=1, stdout="", stderr="not authenticated")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="not authenticated"):
        await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_raises_when_jsonl_has_no_text(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_jsonl({"type": "step_finish", "part": {"tokens": {}}}))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="no text was found"):
        await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_serializes_subprocess_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")
    active = 0
    max_active = 0

    async def on_communicate() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("OK"), on_communicate=on_communicate)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    provider = OpenCodeProvider(repo_path=tmp_path)

    await asyncio.gather(
        provider.generate("sys", "user 1"),
        provider.generate("sys", "user 2"),
    )

    assert max_active == 1


async def test_generate_times_out_and_kills_process(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")
    monkeypatch.setattr(
        "repowise.core.providers.llm.opencode._EXEC_TIMEOUT_SECONDS",
        0.01,
    )

    async def on_communicate() -> None:
        await asyncio.sleep(1)

    proc = FakeProcess(stdout="", on_communicate=on_communicate)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="timed out"):
        await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")

    assert proc.killed


async def test_generate_closes_subprocess_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")
    transport = FakeTransport()

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("OK"), transport=transport)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")

    assert transport.closed is True


async def test_generate_accepts_cache_hints(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await OpenCodeProvider(repo_path=tmp_path).generate(
        "sys",
        "user",
        cache_hints=(),
    )

    assert result.content == "OK"


async def test_generate_handles_error_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(
            returncode=1,
            stdout=_jsonl(
                {
                    "type": "error",
                    "error": {"name": "APIError", "data": {"message": "Model is disabled"}},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="Model is disabled"):
        await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_handles_file_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        raise FileNotFoundError("No such file")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="curl -fsSL"):
        await OpenCodeProvider(repo_path=tmp_path).generate("sys", "user")


async def test_available_model_options_with_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")
    monkeypatch.setattr(
        "repowise.core.providers.llm.opencode._load_opencode_model_catalog",
        lambda _cmd: ["deepseek/deepseek-v4-pro", "openai/gpt-5"],
    )

    options = OpenCodeProvider(repo_path=tmp_path).available_model_options()

    assert len(options) == 3
    assert options[0].model == "opencode/default"
    assert options[0].recommended is True
    assert options[0].source == "local"
    assert options[1].model == "opencode/deepseek/deepseek-v4-pro"
    assert options[1].label == "deepseek/deepseek-v4-pro"
    assert options[1].source == "local"
    assert options[2].model == "opencode/openai/gpt-5"
    assert options[2].label == "openai/gpt-5"


async def test_available_model_options_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "opencode")
    monkeypatch.setattr(
        "repowise.core.providers.llm.opencode._load_opencode_model_catalog",
        lambda _cmd: None,
    )

    options = OpenCodeProvider(repo_path=tmp_path).available_model_options()

    assert len(options) == 1
    assert options[0].model == "opencode/default"
    assert options[0].recommended is True
    assert options[0].source == "fallback"
