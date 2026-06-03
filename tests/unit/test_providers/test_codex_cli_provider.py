"""Unit tests for CodexCliProvider.

All tests mock the Codex subprocess; no real Codex CLI calls are made.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.codex_cli import (
    CodexCliProvider,
    CodexModelReasoning,
    _extract_codex_model_catalog,
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
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 120,
                "cached_input_tokens": 30,
                "output_tokens": 40,
                "reasoning_output_tokens": 10,
            },
        },
        noise=True,
    )


def _reasoning_catalog(
    *efforts: str,
    slug: str = "gpt-5.5",
    default: str = "medium",
) -> dict[str, CodexModelReasoning]:
    return {
        slug: CodexModelReasoning(
            slug=slug,
            default_effort=default,
            supported_efforts=efforts,
        )
    }


def test_extract_codex_model_catalog_keeps_only_reasoning_capabilities():
    catalog = _extract_codex_model_catalog(
        {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "fast"},
                        {"effort": "high", "description": "deeper"},
                    ],
                    "base_instructions": "large prompt metadata should stay ignored",
                },
                {"slug": "no-reasoning", "supported_reasoning_levels": []},
            ]
        }
    )

    assert catalog == {
        "gpt-5.5": CodexModelReasoning(
            slug="gpt-5.5",
            default_effort="medium",
            supported_efforts=("low", "high"),
        )
    }


def test_provider_name_and_default_model(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    provider = CodexCliProvider(repo_path=tmp_path)

    assert provider.provider_name == "codex_cli"
    assert provider.model_name == "codex_cli/default"


def test_custom_model_is_normalized_for_attribution(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    assert CodexCliProvider(model="gpt-5.5", repo_path=tmp_path).model_name == "codex_cli/gpt-5.5"
    assert (
        CodexCliProvider(model="codex_cli/gpt-5.5", repo_path=tmp_path).model_name
        == "codex_cli/gpt-5.5"
    )
    assert (
        CodexCliProvider(model="codex_cli/default", repo_path=tmp_path).model_name
        == "codex_cli/default"
    )


def test_supported_reasoning_modes_reflect_codex_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.codex_cli._load_codex_model_catalog",
        lambda _cmd: _reasoning_catalog("none", "low", "medium", "high", "xhigh"),
    )

    assert CodexCliProvider(
        model="gpt-5.5",
        repo_path=tmp_path,
    ).supported_reasoning_modes() == (
        "auto",
        "off",
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    )


def test_available_model_options_reflect_codex_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.codex_cli._load_codex_model_catalog",
        lambda _cmd: _reasoning_catalog(
            "none",
            "low",
            "medium",
            "high",
            slug="gpt-5.5",
            default="medium",
        ),
    )

    options = CodexCliProvider(repo_path=tmp_path).available_model_options()

    assert options[0].model == "codex_cli/default"
    assert options[0].recommended is True
    model_option = next(option for option in options if option.model == "codex_cli/gpt-5.5")
    assert model_option.label == "gpt-5.5"
    assert model_option.source == "local"
    assert model_option.reasoning_modes == (
        "auto",
        "off",
        "none",
        "minimal",
        "low",
        "medium",
        "high",
    )


def test_missing_cli_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    with pytest.raises(ProviderError, match="Codex CLI not found"):
        CodexCliProvider(repo_path=tmp_path)


async def test_generate_invokes_codex_exec_with_stdin(monkeypatch, tmp_path):
    codex_cmd = str(tmp_path / "bin" / "codex.CMD")
    monkeypatch.setattr("shutil.which", lambda cmd: codex_cmd if cmd == "codex" else None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.codex_cli._load_codex_model_catalog",
        lambda _cmd: _reasoning_catalog("low", "medium", "high", "xhigh"),
    )
    captured: dict[str, Any] = {}
    proc = FakeProcess(stdout=_success_jsonl("Hello from Codex"), stderr="plugin sync warning")

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    provider = CodexCliProvider(model="gpt-5.5", repo_path=tmp_path)
    result = await provider.generate("system rules", "user context", reasoning="minimal")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Codex"
    assert proc.stdin_input is not None
    prompt = proc.stdin_input.decode("utf-8")
    assert "system rules" in prompt
    assert "user context" in prompt
    args = list(captured["args"])
    assert args[:6] == [codex_cmd, "exec", "--ephemeral", "--sandbox", "read-only", "--json"]
    assert args[args.index("--cd") + 1] == str(tmp_path.resolve())
    assert args[args.index("--config") + 1] == 'model_reasoning_effort="low"'
    assert args[args.index("--model") + 1] == "gpt-5.5"
    assert args[-1] == "-"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE


async def test_generate_passes_supported_codex_reasoning_effort(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.codex_cli._load_codex_model_catalog",
        lambda _cmd: _reasoning_catalog("low", "medium", "high", "xhigh"),
    )
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **_kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess(stdout=_success_jsonl("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await CodexCliProvider(model="gpt-5.5", repo_path=tmp_path).generate(
        "sys",
        "user",
        reasoning="xhigh",
    )

    args = list(captured["args"])
    assert args[args.index("--config") + 1] == 'model_reasoning_effort="xhigh"'


async def test_generate_passes_unknown_model_effort_without_catalog_block(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.codex_cli._load_codex_model_catalog",
        lambda _cmd: _reasoning_catalog("low", "medium", "high", "xhigh"),
    )
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **_kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess(stdout=_success_jsonl("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await CodexCliProvider(model="future-codex", repo_path=tmp_path).generate(
        "sys",
        "user",
        reasoning="max",
    )

    args = list(captured["args"])
    assert args[args.index("--config") + 1] == 'model_reasoning_effort="max"'
    assert args[args.index("--model") + 1] == "future-codex"


async def test_generate_uses_jsonl_usage_and_ignores_noise(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await CodexCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.input_tokens == 120
    assert result.output_tokens == 40
    assert result.cached_tokens == 30
    assert result.usage["reasoning_output_tokens"] == 10
    assert result.usage["source"] == "codex_exec"
    assert "estimated" not in result.usage


async def test_generate_accepts_cache_hints(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("OK"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await CodexCliProvider(repo_path=tmp_path).generate(
        "sys",
        "user",
        cache_hints=(),
    )

    assert result.content == "OK"


async def test_generate_closes_subprocess_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
    transport = FakeTransport()

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_success_jsonl("OK"), transport=transport)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await CodexCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert transport.closed is True


async def test_generate_marks_missing_usage_as_estimated(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(
            stdout=_jsonl(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "OK"},
                }
            )
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await CodexCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.usage["estimated"] is True


async def test_generate_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="not logged in"):
        await CodexCliProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_hides_structured_error_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=2, stdout='{"error":{"message":"secret details"}}')

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="codex exec exited with 2") as exc_info:
        await CodexCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert "secret details" not in str(exc_info.value)


async def test_generate_raises_when_jsonl_has_no_agent_message(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=_jsonl({"type": "turn.completed", "usage": {}}))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="no agent_message"):
        await CodexCliProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_serializes_subprocess_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
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
    provider = CodexCliProvider(repo_path=tmp_path)

    await asyncio.gather(
        provider.generate("sys", "user 1"),
        provider.generate("sys", "user 2"),
    )

    assert max_active == 1


async def test_generate_times_out_and_kills_codex_exec(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.codex_cli._EXEC_TIMEOUT_SECONDS",
        0.01,
    )

    async def on_communicate() -> None:
        await asyncio.sleep(1)

    proc = FakeProcess(stdout="", on_communicate=on_communicate)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="timed out"):
        await CodexCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert proc.killed


async def test_generate_rejects_unsupported_reasoning_off(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda cmd: "codex" if cmd == "codex" else None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.codex_cli._load_codex_model_catalog",
        lambda _cmd: _reasoning_catalog("low", "medium", "high", "xhigh"),
    )

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        raise AssertionError("subprocess should not be started")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="model_reasoning_effort='none'"):
        await CodexCliProvider(repo_path=tmp_path).generate("sys", "user", reasoning="off")
