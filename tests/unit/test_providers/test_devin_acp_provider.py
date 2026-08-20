"""Unit tests for DevinAcpProvider.

All tests mock the ACP transport and Devin subprocess; no real Devin CLI calls.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.devin_acp import (
    DevinAcpClient,
    DevinAcpProvider,
    _load_devin_model_catalog,
    _model_label,
    _normalize_model,
    _parse_devin_models,
    _validate_model_name,
)


class FakeAcpConnection:
    """Stand-in for acp.client.ClientSideConnection."""

    def __init__(
        self,
        *,
        message_chunks: tuple[str, ...] = ("Hello from ACP",),
        stop_reason: str = "end_turn",
        usage: Any | None = None,
    ) -> None:
        self.message_chunks = list(message_chunks)
        self.stop_reason = stop_reason
        self.usage = usage or SimpleNamespace(
            total_tokens=11712,
            input_tokens=11351,
            output_tokens=361,
            thought_tokens=None,
            cached_read_tokens=128,
            cached_write_tokens=None,
            field_meta=None,
        )
        self.client: DevinAcpClient | None = None
        self.actions: list[dict[str, Any]] = []

    async def initialize(self, **kwargs: Any) -> Any:
        self.actions.append({"method": "initialize", "kwargs": kwargs})
        return SimpleNamespace(model_dump=lambda: {})

    async def new_session(self, **kwargs: Any) -> Any:
        self.actions.append({"method": "new_session", "kwargs": kwargs})
        return SimpleNamespace(session_id="test-session", model_dump=lambda: {})

    async def set_config_option(self, config_id: str, session_id: str, value: Any) -> Any:
        self.actions.append(
            {
                "method": "set_config_option",
                "config_id": config_id,
                "session_id": session_id,
                "value": value,
            }
        )
        return SimpleNamespace(model_dump=lambda: {})

    async def prompt(self, session_id: str, prompt: Any) -> Any:
        self.actions.append({"method": "prompt", "session_id": session_id, "prompt": prompt})
        if self.client is not None:
            for chunk in self.message_chunks:
                self.client.message_text.append(chunk)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            usage=self.usage,
            model_dump=lambda: {
                "stop_reason": self.stop_reason,
                "usage": {
                    "input_tokens": self.usage.input_tokens,
                    "output_tokens": self.usage.output_tokens,
                    "cached_read_tokens": self.usage.cached_read_tokens,
                },
            },
        )

    async def close(self) -> None:
        pass


class FakeAcpSpawn:
    """Makes an async context manager out of a FakeAcpConnection."""

    _connection: FakeAcpConnection = FakeAcpConnection()

    def __init__(
        self,
        to_client: Any,
        command: str,
        *args: str,
        **kwargs: Any,
    ) -> None:
        self.to_client = to_client
        self.command = command
        self.args = args
        self.kwargs = kwargs
        self._connection = type(self)._connection
        self._connection.client = to_client

    async def __aenter__(self) -> tuple[FakeAcpConnection, Any]:
        return self._connection, SimpleNamespace()

    async def __aexit__(self, *exc: object) -> None:
        pass


def _make_fake_spawn(connection: FakeAcpConnection) -> type[FakeAcpSpawn]:
    class _BoundFakeSpawn(FakeAcpSpawn):
        _connection = connection

    return _BoundFakeSpawn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_normalize_model():
    assert _normalize_model(None) is None
    assert _normalize_model("devin_acp/default") is None
    assert _normalize_model("opus") == "opus"
    assert _normalize_model("devin_acp/opus") == "opus"
    assert _normalize_model("devin_acp/MODEL_GPT_5_2_LOW") == "MODEL_GPT_5_2_LOW"


def test_model_label():
    assert _model_label(None) == "devin_acp/default"
    assert _model_label("opus") == "devin_acp/opus"
    assert _model_label("devin_acp/claude-opus-5-medium") == "devin_acp/claude-opus-5-medium"


def test_validate_model_name_accepts_valid_names():
    for name in [
        "opus",
        "claude-opus-5-medium",
        "MODEL_GPT_5_2_LOW",
        "swe-1-7-fast",
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
                "variants": [{"bad": "entry"}],
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
                                    "model_uid": "swe-1-7",
                                    "label": "SWE-1.7 Max",
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
    assert catalog == [("swe-1-7", "SWE-1.7 Max", 200000, 128000)]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def test_client_appends_message_and_thought_chunks():
    client = DevinAcpClient("devin_acp")

    class FakeTextBlock:
        type = "text"
        text = "chunk"

    msg_update = SimpleNamespace(session_update="agent_message_chunk", content=FakeTextBlock())
    thought_update = SimpleNamespace(session_update="agent_thought_chunk", content=FakeTextBlock())

    asyncio.run(client.session_update("session-1", msg_update))
    asyncio.run(client.session_update("session-1", thought_update))

    assert client.message_text == ["chunk"]
    assert client.thought_text == ["chunk"]


def test_client_denies_permission():
    client = DevinAcpClient("devin_acp")
    resp = asyncio.run(client.request_permission("session-1", None, None))
    assert resp.outcome.outcome == "cancelled"


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_name_and_default_model(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    provider = DevinAcpProvider(repo_path=tmp_path)

    assert provider.provider_name == "devin_acp"
    assert provider.model_name == "devin_acp/default"


def test_custom_model_is_normalized(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    assert DevinAcpProvider(model="opus", repo_path=tmp_path).model_name == "devin_acp/opus"
    assert (
        DevinAcpProvider(model="devin_acp/swe-1-7", repo_path=tmp_path).model_name
        == "devin_acp/swe-1-7"
    )


def test_invalid_model_name_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    with pytest.raises(ProviderError, match="Invalid model name"):
        DevinAcpProvider(model="bad; name", repo_path=tmp_path)


def test_missing_cli_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    with pytest.raises(ProviderError, match="Devin CLI not found"):
        DevinAcpProvider(repo_path=tmp_path)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def test_generate_invokes_acp_session(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    connection = FakeAcpConnection(message_chunks=("Hello from ACP",))
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp.spawn_agent_process",
        _make_fake_spawn(connection),
    )

    provider = DevinAcpProvider(model="swe-1-7", repo_path=tmp_path)
    result = await provider.generate("system rules", "user context")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from ACP"
    assert result.input_tokens == 11351
    assert result.output_tokens == 361
    assert result.cached_tokens == 128
    assert result.usage["source"] == "devin_acp"
    assert result.provider_stop_reason == "end_turn"

    # Mode was switched to ask and model was set
    modes = [a for a in connection.actions if a["method"] == "set_config_option"]
    assert any(a["config_id"] == "mode" and a["value"] == "ask" for a in modes)
    assert any(a["config_id"] == "model" and a["value"] == "swe-1-7" for a in modes)

    # New session was created with the repo as cwd
    sessions = [a for a in connection.actions if a["method"] == "new_session"]
    assert len(sessions) == 1
    assert sessions[0]["kwargs"]["cwd"] == str(tmp_path.resolve())
    assert sessions[0]["kwargs"]["mcp_servers"] == []


async def test_generate_default_model_omits_model_config(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    connection = FakeAcpConnection(message_chunks=("OK",))
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp.spawn_agent_process",
        _make_fake_spawn(connection),
    )

    await DevinAcpProvider(repo_path=tmp_path).generate("sys", "user")

    modes = [a for a in connection.actions if a["method"] == "set_config_option"]
    assert any(a["config_id"] == "mode" and a["value"] == "ask" for a in modes)
    assert not any(a["config_id"] == "model" for a in modes)


async def test_generate_raises_on_empty_response(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")

    connection = FakeAcpConnection(message_chunks=())
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp.spawn_agent_process",
        _make_fake_spawn(connection),
    )

    with pytest.raises(ProviderError, match="no assistant message"):
        await DevinAcpProvider(repo_path=tmp_path).generate("sys", "user")


async def test_generate_serializes_acp_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    state = {"active": 0, "max_active": 0, "spawns": 0}

    def make_spawn(to_client: Any, command: str, *args: str, **kwargs: Any) -> FakeAcpSpawn:
        state["spawns"] += 1

        class CountingConnection(FakeAcpConnection):
            async def prompt(self, session_id: str, prompt: Any) -> Any:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                await asyncio.sleep(0.01)
                state["active"] -= 1
                if self.client is not None:
                    for chunk in self.message_chunks:
                        self.client.message_text.append(chunk)
                return SimpleNamespace(
                    stop_reason=self.stop_reason,
                    usage=self.usage,
                    model_dump=lambda: {},
                )

        connection = CountingConnection(message_chunks=("OK",))
        return _make_fake_spawn(connection)(to_client, command, *args, **kwargs)

    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp.spawn_agent_process",
        make_spawn,
    )

    provider = DevinAcpProvider(repo_path=tmp_path)
    await asyncio.gather(
        provider.generate("sys", "user 1"),
        provider.generate("sys", "user 2"),
    )

    assert state["spawns"] == 2
    assert state["max_active"] == 1


async def test_generate_times_out(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp._EXEC_TIMEOUT_SECONDS",
        0.01,
    )

    class HangingConnection(FakeAcpConnection):
        async def prompt(self, session_id: str, prompt: Any) -> Any:
            await asyncio.sleep(1)
            return super().prompt(session_id, prompt)

    connection = HangingConnection(message_chunks=("OK",))
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp.spawn_agent_process",
        _make_fake_spawn(connection),
    )

    with pytest.raises(ProviderError, match="timed out"):
        await DevinAcpProvider(repo_path=tmp_path).generate("sys", "user")


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------


def test_available_model_options_with_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp._load_devin_model_catalog",
        lambda _cmd: [
            ("swe-1-7", "SWE-1.7 Max", 200000, 128000),
            ("claude-opus-5-medium", "Claude Opus 5 Medium", 1000000, 128000),
        ],
    )

    options = DevinAcpProvider(repo_path=tmp_path).available_model_options()

    assert options[0].model == "devin_acp/default"
    assert options[0].recommended is True
    assert options[0].source == "local"
    assert options[1].model == "devin_acp/claude-opus-5-medium"
    assert options[1].label == "Claude Opus 5 Medium"
    assert options[1].reasoning_modes == ("auto",)
    assert options[2].model == "devin_acp/swe-1-7"


def test_available_model_options_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: "devin")
    monkeypatch.setattr(
        "repowise.core.providers.llm.devin_acp._load_devin_model_catalog",
        lambda _cmd: None,
    )

    options = DevinAcpProvider(repo_path=tmp_path).available_model_options()

    assert len(options) == 1
    assert options[0].model == "devin_acp/default"
    assert options[0].recommended is True
