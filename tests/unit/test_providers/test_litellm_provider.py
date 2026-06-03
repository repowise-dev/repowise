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
        "supports_reasoning",
        lambda *, model: model == "vendor/reasoner",
        raising=False,
    )

    options = LiteLLMProvider(model="vendor/plain").available_model_options()

    models = [option.model for option in options]
    assert models[:2] == ["vendor/plain", "vendor/reasoner"]
    reasoner = next(option for option in options if option.model == "vendor/reasoner")
    assert reasoner.source == "local"
    assert reasoner.reasoning_modes == ("auto", "low", "medium", "high")
    assert "reasoning support" in reasoner.notes


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
                    {"message": type("Message", (), {"content": "ok"})()},
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
        lambda *, model: model == "vendor/reasoner",
        raising=False,
    )

    provider = LiteLLMProvider(model="vendor/reasoner")
    await provider.generate("sys", "user", reasoning="high")

    assert completion.call_args.kwargs["reasoning_effort"] == "high"
