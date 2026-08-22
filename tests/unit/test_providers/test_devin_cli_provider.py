"""Unit tests for DevinCliProvider.

All tests mock the Devin subprocess; no real Devin CLI calls are made.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.devin_cli import (
    DevinCliProvider,
    _load_devin_model_catalog,
    _model_label,
    _normalize_model,
    _parse_devin_models,
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
        self.killed = False

    async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
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
# Helpers
# ---------------------------------------------------------------------------


def test_normalize_model():
    assert _normalize_model(None) is None
    assert _normalize_model("devin_cli/default") is None
    assert _normalize_model("opus") == "opus"
    assert _normalize_model("devin_cli/opus") == "opus"
    assert _normalize_model("devin_cli/MODEL_GPT_5_2_LOW") == "MODEL_GPT_5_2_LOW"


def test_model_label():
    assert _model_label(None) == "devin_cli/default"
    assert _model_label("opus") == "devin_cli/opus"
    assert _model_label("devin_cli/claude-opus-5-medium") == "devin_cli/claude-opus-5-medium"


def test_validate_model_name_accepts_valid_names():
    for name in [
        "opus",
        "claude-opus-5-medium",
        "MODEL_GPT_5_2_LOW",
        "swe-1-6-fast",
        "gpt-5-6-luna-medium",
    ]:
        _validate_model_name(name)


def test_validate_model_name_rejects_shell_metacharacters():
    for name in ["bad; ls", "name$(whoami)", "name|cat", "`evil`", "name name"]:
        with pytest.raises(ProviderError, match="Invalid model name"):
            _validate_model_name(name)


def test_parse_devin_models_extracts_variants():
    raw = {
        "families": [
            {
                "family_label": "Claude Opus 5",
                "family_uid": "claude-opus-5",
                "slug": "claude-opus-5",
                "aliases": ["opus"],
                "variants": [
                    {
                        "model_uid": "claude-opus-5-medium",
                        "label": "Claude Opus 5 Medium",
                        "max_context_tokens": 1000000,
                        "max_output_tokens": 128000,
                    },
                    {
                        "model_uid": "claude-opus-5-low",
                        "label": "Claude Opus 5 Low",
                        "max_context_tokens": 1000000,
                        "max_output_tokens": 128000,
                    },
                ],
            },
            {
                "family_label": "Unknown",
                "variants": [
                    {"bad": "entry"},
                ],
            },
        ]
    }

    models = _parse_devin_models(raw)
    assert len(models) == 2
    assert set(models) == {
        ("claude-opus-5-low", "Claude Opus 5 Low", 1000000, 128000),
        ("claude-opus-5-medium", "Claude Opus 5 Medium", 1000000, 128000),
    }


def test_parse_devin_models_ignores_invalid_input():
    assert _parse_devin_models(None) == []
    assert _parse_devin_models({"not_families": []}) == []
    assert _parse_devin_models([]) == []


def test_load_devin_model_catalog_with_valid_json(monkeypatch):
    completed = type(
        "Completed",
        (),
        {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "families": [
                        {
                            "variants": [
                                {
                                    "model_uid": "swe-1-6",
                                    "label": "SWE-1.6",
                                    "max_context_tokens": 200000,
                                    "max_output_tokens": 128000,
                                },
                            ]
                        }
                    ]
                }
            ),
        },
    )()
    _load_devin_model_catalog.cache_clear()
    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: completed)
    catalog = _load_devin_model_catalog("devin")
    assert catalog == [("swe-1-6", "SWE-1.6", 200000, 128000)]


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_name_and_default_model(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    provider = DevinCliProvider(repo_path=tmp_path)

    assert provider.provider_name == "devin_cli"
    assert provider.model_name == "devin_cli/default"


def test_custom_model_is_normalized(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    assert DevinCliProvider(model="opus", repo_path=tmp_path).model_name == "devin_cli/opus"
    assert (
        DevinCliProvider(model="devin_cli/claude-opus-5-medium", repo_path=tmp_path).model_name
        == "devin_cli/claude-opus-5-medium"
    )
    assert (
        DevinCliProvider(model="devin_cli/default", repo_path=tmp_path).model_name
        == "devin_cli/default"
    )


def test_invalid_model_name_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    with pytest.raises(ProviderError, match="Invalid model name"):
        DevinCliProvider(model="bad; name", repo_path=tmp_path)


def test_missing_cli_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    with pytest.raises(ProviderError, match="Devin CLI not found"):
        DevinCliProvider(repo_path=tmp_path)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def test_generate_invokes_devin_with_prompt_file(monkeypatch, tmp_path):
    devin_cmd = str(tmp_path / "bin" / "devin")
    monkeypatch.setattr("shutil.which", lambda _cmd: devin_cmd)
    captured: dict[str, Any] = {}
    proc = FakeProcess(stdout="Hello from Devin", stderr="")

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        prompt_file = Path(args[args.index("--prompt-file") + 1])
        assert prompt_file.exists()
        captured["prompt_content"] = prompt_file.read_text(encoding="utf-8")
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    provider = DevinCliProvider(model="opus", repo_path=tmp_path)
    result = await provider.generate("system rules", "user context")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Devin"
    assert result.usage["estimated"] is True
    assert result.usage["source"] == "devin_cli"

    args = list(captured["args"])
    assert args[0] == devin_cmd
    assert "-p" in args
    assert "--prompt-file" in args
    assert "--respect-workspace-trust" in args
    assert "false" in args
    assert "--permission-mode" in args
    assert "auto" in args
    assert args[args.index("--model") + 1] == "opus"
    assert "devin_prompt_" in args[args.index("--prompt-file") + 1]
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())

    # Verify the prompt file was written and contains the combined prompt
    written = captured["prompt_content"]
    assert "system rules" in written
    assert "user context" in written
    assert "Do not edit any files" in written


async def test_generate_default_model_omits_model_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        return FakeProcess(stdout="OK")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await DevinCliProvider(repo_path=tmp_path).generate("sys", "user")

    args = list(captured["args"])
    assert "--model" not in args


async def test_generate_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="not logged in"):
        await DevinCliProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_raises_when_stdout_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout="", stderr="")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="no output"):
        await DevinCliProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_closes_subprocess_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    transport = FakeTransport()

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout="OK", transport=transport)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await DevinCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert transport.closed is True


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
    provider = DevinCliProvider(repo_path=tmp_path)

    await asyncio.gather(
        provider.generate("sys", "user 1"),
        provider.generate("sys", "user 2"),
    )

    assert max_active == 1


async def test_generate_times_out_and_kills_process(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_cli._EXEC_TIMEOUT_SECONDS",
        0.01,
    )

    async def on_communicate() -> None:
        await asyncio.sleep(1)

    proc = FakeProcess(stdout="", on_communicate=on_communicate)

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="timed out"):
        await DevinCliProvider(repo_path=tmp_path).generate("sys", "user")

    assert proc.killed


async def test_generate_handles_file_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeProcess:
        raise FileNotFoundError("No such file")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="Devin CLI not found"):
        await DevinCliProvider(repo_path=tmp_path).generate("sys", "user")


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------


def test_available_model_options_with_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_cli._load_devin_model_catalog",
        lambda _cmd: [
            ("swe-1-6", "SWE-1.6", 200000, 128000),
            ("claude-opus-5-medium", "Claude Opus 5 Medium", 1000000, 128000),
        ],
    )

    options = DevinCliProvider(repo_path=tmp_path).available_model_options()

    assert options[0].model == "devin_cli/default"
    assert options[0].recommended is True
    assert options[0].source == "local"
    assert options[1].model == "devin_cli/claude-opus-5-medium"
    assert options[1].label == "Claude Opus 5 Medium"
    assert options[1].reasoning_modes == ("auto",)
    assert options[2].model == "devin_cli/swe-1-6"


def test_available_model_options_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_cli._load_devin_model_catalog",
        lambda _cmd: None,
    )

    options = DevinCliProvider(repo_path=tmp_path).available_model_options()

    assert len(options) == 1
    assert options[0].model == "devin_cli/default"
    assert options[0].recommended is True
    assert options[0].source == "fallback"
