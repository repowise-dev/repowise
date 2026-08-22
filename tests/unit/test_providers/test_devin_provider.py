"""Unit tests for DevinProvider.

All tests mock the devin subprocess; no real Devin CLI calls are made.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.devin import (
    DevinProvider,
    _normalize_model,
    _validate_model_name,
)
from repowise.core.providers.llm.registry import (
    _BUILTIN_PROVIDERS,
    get_provider,
    list_providers,
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
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
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


# ---------------------------------------------------------------------------
# Model name normalization / validation
# ---------------------------------------------------------------------------


def test_normalize_model():
    assert _normalize_model(None) is None
    assert _normalize_model("") is None
    assert _normalize_model("devin/adaptive") is None
    assert _normalize_model("devin/opus") == "opus"
    assert _normalize_model("opus") == "opus"


def test_validate_model_name_accepts_valid_names():
    for name in ["adaptive", "opus", "sonnet", "swe-1-6-fast", "gpt-5.5"]:
        _validate_model_name(name)


def test_validate_model_name_rejects_shell_metacharacters():
    for name in ["bad; ls", "name$(whoami)", "name|cat", "`evil`"]:
        with pytest.raises(ProviderError, match="Invalid model name"):
            _validate_model_name(name)


def test_validate_model_name_rejects_empty():
    with pytest.raises(ProviderError, match="Invalid model name"):
        _validate_model_name("")


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_name_and_default_model(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    provider = DevinProvider(repo_path=tmp_path)

    assert provider.provider_name == "devin"
    assert provider.model_name == "devin/adaptive"


def test_custom_model_is_normalized(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    assert DevinProvider(model="devin/opus", repo_path=tmp_path).model_name == "devin/opus"
    assert DevinProvider(model="devin/adaptive", repo_path=tmp_path).model_name == "devin/adaptive"
    assert DevinProvider(model="opus", repo_path=tmp_path).model_name == "devin/opus"


def test_invalid_model_name_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    with pytest.raises(ProviderError, match="Invalid model name"):
        DevinProvider(model="bad;name", repo_path=tmp_path)


def test_missing_cli_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    with pytest.raises(ProviderError, match="Devin CLI is not installed"):
        DevinProvider(repo_path=tmp_path)


def test_available_model_options(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    options = DevinProvider(repo_path=tmp_path).available_model_options()

    models = [o.model for o in options]
    assert models[0] == "devin/adaptive"
    assert options[0].recommended is True
    assert "devin/opus" in models
    assert "devin/sonnet" in models
    assert "devin/gpt" in models
    assert "devin/swe" in models
    assert "devin/codex" in models
    assert "devin/gemini" in models


def test_supported_reasoning_modes(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    assert DevinProvider(repo_path=tmp_path).supported_reasoning_modes() == ("auto",)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_devin_is_registered_as_a_builtin_provider():
    assert "devin" in _BUILTIN_PROVIDERS
    assert "devin" in list_providers()
    assert _BUILTIN_PROVIDERS["devin"] == (
        "repowise.core.providers.llm.devin",
        "DevinProvider",
    )


def test_get_provider_resolves_devin(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    provider = get_provider("devin", model="devin/opus", repo_path=tmp_path)

    assert isinstance(provider, DevinProvider)
    assert provider.provider_name == "devin"
    assert provider.model_name == "devin/opus"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def test_generate_invokes_devin_with_prompt(monkeypatch, tmp_path):
    devin_cmd = str(tmp_path / "bin" / "devin")
    monkeypatch.setattr("shutil.which", lambda _cmd: devin_cmd)
    captured: dict[str, Any] = {}
    proc = FakeProcess(stdout="Hello from Devin")

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    provider = DevinProvider(model="devin/opus", repo_path=tmp_path)
    result = await provider.generate("system rules", "user context")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Devin"
    args = list(captured["args"])
    assert args[0] == devin_cmd
    assert args[1] == "-p"
    assert "--permission-mode" in args
    assert args[args.index("--permission-mode") + 1] == "normal"
    assert "--respect-workspace-trust" in args
    assert args[args.index("--model") + 1] == "opus"
    # The combined prompt is the trailing positional arg.
    combined = args[-1]
    assert "system rules" in combined
    assert "user context" in combined
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())


async def test_generate_default_model_omits_model_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess(stdout="OK")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await DevinProvider(repo_path=tmp_path).generate("sys", "user")

    args = list(captured["args"])
    assert "--model" not in args


async def test_generate_marks_usage_as_estimated(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout="OK")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await DevinProvider(repo_path=tmp_path).generate("sys", "user")

    assert result.content == "OK"
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.usage["source"] == "devin_p"
    assert result.usage["estimated"] is True
    assert result.usage["model"] == "devin/adaptive"


async def test_generate_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=1, stdout="", stderr="not authenticated")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="not authenticated"):
        await DevinProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_raises_when_no_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout="", stderr="")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="no output"):
        await DevinProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_serializes_subprocess_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    active = 0
    max_active = 0

    async def on_communicate() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout="OK", on_communicate=on_communicate)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    provider = DevinProvider(repo_path=tmp_path)

    await asyncio.gather(
        provider.generate("sys", "user 1"),
        provider.generate("sys", "user 2"),
    )

    assert max_active == 1


async def test_generate_times_out_and_kills_process(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin._EXEC_TIMEOUT_SECONDS",
        0.01,
    )

    async def on_communicate() -> None:
        await asyncio.sleep(1)

    proc = FakeProcess(stdout="", on_communicate=on_communicate)

    async def fake_exec(*_a: str, **_k: Any) -> FakeProcess:
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="timed out"):
        await DevinProvider(repo_path=tmp_path).generate("sys", "user")

    assert proc.killed


async def test_generate_closes_subprocess_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    transport = FakeTransport()

    async def fake_exec(*_a: str, **_k: Any) -> FakeProcess:
        return FakeProcess(stdout="OK", transport=transport)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await DevinProvider(repo_path=tmp_path).generate("sys", "user")

    assert transport.closed is True


async def test_generate_handles_file_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    async def fake_exec(*_a: str, **_k: Any) -> FakeProcess:
        raise FileNotFoundError("No such file")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match=r"cli\.devin\.ai"):
        await DevinProvider(repo_path=tmp_path).generate("sys", "user")
