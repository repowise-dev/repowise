"""Unit tests for LiteLLMProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.litellm import LiteLLMProvider


def test_available_model_options_uses_litellm_model_list(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    monkeypatch.setattr(
        litellm,
        "model_list",
        ["vendor/plain", "vendor/reasoner"],
        raising=False,
    )
    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "vendor/plain": {"supports_reasoning": False},
            "vendor/reasoner": {"supports_reasoning": True},
        },
        raising=False,
    )
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda _model: pytest.fail("catalog enumeration must stay local"),
        raising=False,
    )

    options = LiteLLMProvider(model="vendor/plain").available_model_options()

    models = [option.model for option in options]
    assert models[:2] == ["vendor/plain", "vendor/reasoner"]
    reasoner = next(option for option in options if option.model == "vendor/reasoner")
    assert reasoner.source == "local"
    assert reasoner.reasoning_modes == ("auto", "low", "medium", "high")
    assert "reasoning support" in reasoner.notes


def test_discovery_falls_back_when_model_is_missing_from_catalog(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    monkeypatch.setattr(litellm, "model_list", ["vendor/reasoner"], raising=False)
    monkeypatch.setattr(litellm, "model_cost", {}, raising=False)
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda _model, **_kwargs: {"supports_reasoning": True},
        raising=False,
    )
    monkeypatch.setattr(
        litellm,
        "supports_reasoning",
        lambda **_kwargs: pytest.fail("model metadata must decide the capability"),
        raising=False,
    )
    provider = LiteLLMProvider(model="vendor/reasoner")

    option = provider.available_model_options()[0]

    assert option.reasoning_modes == provider.supported_reasoning_modes()
    assert option.reasoning_modes == ("auto", "low", "medium", "high")


def test_discovery_uses_bare_model_metadata_for_indecisive_provider_row(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    monkeypatch.setattr(litellm, "model_list", ["vendor/reasoner"], raising=False)
    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "vendor/reasoner": {"litellm_provider": "vendor", "mode": "chat"},
            "Reasoner": {"supports_reasoning": True},
        },
        raising=False,
    )
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda _model, **_kwargs: pytest.fail("the loaded catalog contains the fallback metadata"),
        raising=False,
    )
    provider = LiteLLMProvider(model="vendor/reasoner")

    option = provider.available_model_options()[0]

    assert option.reasoning_modes == provider.supported_reasoning_modes()
    assert option.reasoning_modes == ("auto", "low", "medium", "high")


def test_available_model_options_uses_exact_litellm_effort_metadata(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    monkeypatch.setattr(litellm, "model_list", ["vendor/reasoner"], raising=False)
    metadata = {
        "supports_reasoning": True,
        "supports_none_reasoning_effort": True,
        "supports_minimal_reasoning_effort": True,
        "supports_low_reasoning_effort": False,
        "supports_xhigh_reasoning_effort": True,
        "supports_max_reasoning_effort": True,
    }
    monkeypatch.setattr(litellm, "model_cost", {"vendor/reasoner": metadata}, raising=False)
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda _model: pytest.fail("catalog enumeration must stay local"),
        raising=False,
    )

    option = LiteLLMProvider(model="vendor/reasoner").available_model_options()[0]

    assert option.reasoning_modes == (
        "auto",
        "none",
        "minimal",
        "medium",
        "high",
        "xhigh",
        "max",
    )


def test_explicit_empty_effort_metadata_is_authoritative(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda _model, **_kwargs: {"reasoning_effort_levels": []},
        raising=False,
    )
    monkeypatch.setattr(
        litellm,
        "supports_reasoning",
        lambda **_kwargs: pytest.fail("explicit metadata must win"),
        raising=False,
    )

    assert LiteLLMProvider(model="vendor/plain").supported_reasoning_modes() == ("auto",)


async def test_generate_rejects_explicit_reasoning_before_litellm_call(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(), raising=False)

    provider = LiteLLMProvider(model="vendor/plain")

    with pytest.raises(ProviderError, match="reasoning='low' is not supported"):
        await provider.generate("sys", "user", reasoning="low")

    litellm.acompletion.assert_not_called()


async def test_generate_forwards_reasoning_effort(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    fake_response = type(
        "Response",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {
                        "message": type("Message", (), {"content": "ok"})(),
                        "finish_reason": "length",
                    },
                )()
            ],
            "usage": None,
        },
    )()
    completion = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(litellm, "acompletion", completion, raising=False)
    monkeypatch.setattr(
        litellm,
        "supports_reasoning",
        lambda *, model, **_kwargs: model == "reasoner",
        raising=False,
    )

    provider = LiteLLMProvider(model="vendor/reasoner")
    result = await provider.generate("sys", "user", reasoning="high")

    assert completion.call_args.kwargs["reasoning_effort"] == "high"
    assert result.stop_reason == "max_tokens"
    assert result.provider_stop_reason == "length"


async def test_discovery_and_execution_accept_the_same_effort_flags(monkeypatch):
    litellm = pytest.importorskip("litellm", reason="litellm SDK not installed")
    metadata = {
        "supports_reasoning": None,
        "supports_none_reasoning_effort": False,
        "supports_minimal_reasoning_effort": True,
        "supports_low_reasoning_effort": None,
        "supports_xhigh_reasoning_effort": False,
    }
    fake_response = type(
        "Response",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {
                        "message": type("Message", (), {"content": "ok"})(),
                        "finish_reason": "stop",
                    },
                )()
            ],
            "usage": None,
        },
    )()
    completion = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(litellm, "model_list", ["gpt-5-search-api"], raising=False)
    monkeypatch.setattr(litellm, "model_cost", {"gpt-5-search-api": metadata}, raising=False)
    monkeypatch.setattr(litellm, "get_model_info", lambda _model: metadata, raising=False)
    monkeypatch.setattr(litellm, "supports_reasoning", lambda **_kwargs: False, raising=False)
    monkeypatch.setattr(litellm, "acompletion", completion, raising=False)
    provider = LiteLLMProvider(model="gpt-5-search-api")

    option = provider.available_model_options()[0]
    assert provider.supported_reasoning_modes() == option.reasoning_modes

    await provider.generate("sys", "user", reasoning="minimal")

    assert completion.call_args.kwargs["reasoning_effort"] == "minimal"
