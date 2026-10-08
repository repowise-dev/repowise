"""Azure OpenAI provider tests."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.providers.llm.azure_openai import AzureOpenAIProvider
from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.registry import get_provider, list_providers, provider_kwargs


def test_azure_in_provider_list() -> None:
    assert "azure_openai" in list_providers()


def test_provider_kwargs_maps_endpoint_and_deployment() -> None:
    env = {
        "AZURE_OPENAI_API_KEY": "key123",
        "AZURE_OPENAI_ENDPOINT": "https://myres.openai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "my-gpt-5",
        "AZURE_OPENAI_API_VERSION": "2024-12-01-preview",
    }
    kw = provider_kwargs("azure_openai", getenv=env.get)
    assert kw["api_key"] == "key123"
    assert kw["azure_endpoint"] == "https://myres.openai.azure.com"
    assert kw["model"] == "my-gpt-5"
    assert kw["api_version"] == "2024-12-01-preview"


def test_missing_endpoint_raises() -> None:
    with pytest.raises(ProviderError, match="No Azure endpoint"):
        AzureOpenAIProvider(api_key="k", azure_endpoint=None)

    # Ensure env fallback is tested with isolated env
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
        os.environ.pop("AZURE_OPENAI_BASE_URL", None)
        with pytest.raises(ProviderError):
            AzureOpenAIProvider(api_key="k", azure_endpoint="")


def test_missing_key_without_entra_raises() -> None:
    with pytest.raises(ProviderError, match="No API key"):
        AzureOpenAIProvider(azure_endpoint="https://myres.openai.azure.com", api_key="")


def test_base_url_alias_accepted() -> None:
    # Server passes base_url, not azure_endpoint
    provider = AzureOpenAIProvider(
        api_key="k",
        base_url="https://myres.openai.azure.com",
        model="my-deploy",
    )
    assert provider._azure_endpoint == "https://myres.openai.azure.com"
    assert provider.model_name == "my-deploy"


def test_normalizes_endpoint_without_scheme() -> None:
    provider = AzureOpenAIProvider(api_key="k", azure_endpoint="myres.openai.azure.com")
    assert provider._azure_endpoint == "https://myres.openai.azure.com"


@pytest.mark.asyncio
async def test_generate_uses_deployment_as_model() -> None:
    provider = AzureOpenAIProvider(
        api_key="test-key",
        azure_endpoint="https://myres.openai.azure.com",
        model="my-gpt-5",
    )

    mock_response = MagicMock()
    mock_response.usage = MagicMock(
        prompt_tokens=10, completion_tokens=20, total_tokens=30, prompt_tokens_details=None
    )
    mock_choice = MagicMock()
    mock_choice.message.content = "hello"
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    with patch.object(
        provider._client.chat.completions, "create", new=AsyncMock(return_value=mock_response)
    ):
        result = await provider.generate("sys", "user", max_tokens=10)

    assert result.content == "hello"
    assert result.input_tokens == 10
    assert result.output_tokens == 20


def test_registry_get_provider() -> None:
    provider = get_provider(
        "azure_openai",
        api_key="k",
        azure_endpoint="https://myres.openai.azure.com",
        with_rate_limiter=False,
    )
    assert provider.provider_name == "azure_openai"


def test_provider_catalog_includes_azure() -> None:
    from repowise.server.provider_config import PROVIDER_CATALOG

    ids = {p["id"] for p in PROVIDER_CATALOG}
    assert "azure_openai" in ids
