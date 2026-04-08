"""Unit tests for MiniMaxProvider.

All tests use mocked API calls — no MINIMAX_API_KEY required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.providers.llm.base import BaseProvider, GeneratedResponse, ProviderError
from repowise.core.providers.llm.minimax import MiniMaxProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(api_key: str = "test-minimax-key", model: str = "MiniMax-M2.7") -> MiniMaxProvider:
    return MiniMaxProvider(api_key=api_key, model=model)


def _make_mock_response(content: str = "Test response", input_tokens: int = 100, output_tokens: int = 50):
    block = MagicMock()
    block.type = "text"
    block.text = content
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


class TestMiniMaxProviderInterface:
    def test_is_base_provider_subclass(self) -> None:
        assert issubclass(MiniMaxProvider, BaseProvider)

    def test_provider_name(self) -> None:
        provider = _make_provider()
        assert provider.provider_name == "minimax"

    def test_model_name_default(self) -> None:
        provider = _make_provider()
        assert provider.model_name == "MiniMax-M2.7"

    def test_model_name_highspeed(self) -> None:
        provider = _make_provider(model="MiniMax-M2.7-highspeed")
        assert provider.model_name == "MiniMax-M2.7-highspeed"

    def test_no_api_key_raises_provider_error(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os
            original = os.environ.pop("MINIMAX_API_KEY", None)
            try:
                with pytest.raises(ProviderError, match="No API key"):
                    MiniMaxProvider(api_key=None)
            finally:
                if original is not None:
                    os.environ["MINIMAX_API_KEY"] = original

    def test_api_key_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key-123")
        provider = MiniMaxProvider()
        assert provider is not None


# ---------------------------------------------------------------------------
# Temperature clamping
# ---------------------------------------------------------------------------


class TestTemperatureClamping:
    def test_temperature_above_zero_unchanged(self) -> None:
        provider = _make_provider()
        assert provider._clamp_temperature(0.7) == 0.7

    def test_temperature_one_unchanged(self) -> None:
        provider = _make_provider()
        assert provider._clamp_temperature(1.0) == 1.0

    def test_temperature_zero_clamped_to_minimum(self) -> None:
        provider = _make_provider()
        assert provider._clamp_temperature(0.0) == 0.01

    def test_temperature_negative_clamped(self) -> None:
        provider = _make_provider()
        assert provider._clamp_temperature(-0.5) == 0.01

    def test_temperature_above_one_clamped(self) -> None:
        provider = _make_provider()
        assert provider._clamp_temperature(1.5) == 1.0


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


class TestGenerate:
    async def test_generate_returns_generated_response(self) -> None:
        provider = _make_provider()
        mock_response = _make_mock_response("Hello from MiniMax")

        with patch.object(provider._client.messages, "create", new=AsyncMock(return_value=mock_response)):
            result = await provider.generate(
                system_prompt="You are helpful.",
                user_prompt="Say hello.",
            )

        assert isinstance(result, GeneratedResponse)
        assert result.content == "Hello from MiniMax"

    async def test_generate_token_counts(self) -> None:
        provider = _make_provider()
        mock_response = _make_mock_response(input_tokens=200, output_tokens=80)

        with patch.object(provider._client.messages, "create", new=AsyncMock(return_value=mock_response)):
            result = await provider.generate(system_prompt="sys", user_prompt="user")

        assert result.input_tokens == 200
        assert result.output_tokens == 80
        assert result.cached_tokens == 0

    async def test_generate_passes_correct_model(self) -> None:
        provider = _make_provider(model="MiniMax-M2.7-highspeed")
        mock_response = _make_mock_response()
        create_mock = AsyncMock(return_value=mock_response)

        with patch.object(provider._client.messages, "create", new=create_mock):
            await provider.generate(system_prompt="sys", user_prompt="user")

        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["model"] == "MiniMax-M2.7-highspeed"

    async def test_generate_clamps_zero_temperature(self) -> None:
        provider = _make_provider()
        mock_response = _make_mock_response()
        create_mock = AsyncMock(return_value=mock_response)

        with patch.object(provider._client.messages, "create", new=create_mock):
            await provider.generate(system_prompt="sys", user_prompt="user", temperature=0.0)

        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["temperature"] > 0.0

    async def test_generate_system_prompt_passed(self) -> None:
        provider = _make_provider()
        mock_response = _make_mock_response()
        create_mock = AsyncMock(return_value=mock_response)

        with patch.object(provider._client.messages, "create", new=create_mock):
            await provider.generate(
                system_prompt="Be concise.",
                user_prompt="Say OK.",
            )

        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["system"] == "Be concise."


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_minimax_in_list_providers(self) -> None:
        from repowise.core.providers.llm.registry import list_providers
        assert "minimax" in list_providers()

    def test_get_provider_minimax(self) -> None:
        from repowise.core.providers.llm.registry import get_provider
        provider = get_provider("minimax", api_key="test-key", with_rate_limiter=False)
        assert isinstance(provider, MiniMaxProvider)
        assert provider.provider_name == "minimax"

    def test_get_provider_minimax_with_model(self) -> None:
        from repowise.core.providers.llm.registry import get_provider
        provider = get_provider(
            "minimax",
            api_key="test-key",
            model="MiniMax-M2.7-highspeed",
            with_rate_limiter=False,
        )
        assert provider.model_name == "MiniMax-M2.7-highspeed"
