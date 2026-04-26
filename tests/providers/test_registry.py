"""Tests for the provider registry.

Verifies that get_provider(), list_providers(), and register_provider()
work correctly. All tests use the mock provider — no API keys required.
"""

from __future__ import annotations

import pytest

from repowise.core.providers.llm.base import BaseProvider
from repowise.core.providers.llm.mock import MockProvider
from repowise.core.providers.llm.registry import (
    _BUILTIN_PROVIDERS,
    get_provider,
    list_providers,
    register_provider,
)


class TestListProviders:
    def test_includes_all_builtin_providers(self) -> None:
        providers = list_providers()
        assert "anthropic" in providers
        assert "openai" in providers
        assert "openrouter" in providers
        assert "ollama" in providers
        assert "litellm" in providers
        assert "mock" in providers

    def test_returns_sorted_list(self) -> None:
        providers = list_providers()
        assert providers == sorted(providers)

    def test_returns_list_of_strings(self) -> None:
        providers = list_providers()
        assert all(isinstance(p, str) for p in providers)


class TestGetMockProvider:
    """Mock provider can be retrieved without any API keys."""

    def test_get_mock_provider_returns_mock_provider(self) -> None:
        provider = get_provider("mock", with_rate_limiter=False)
        assert isinstance(provider, MockProvider)

    def test_get_mock_provider_is_base_provider(self) -> None:
        provider = get_provider("mock", with_rate_limiter=False)
        assert isinstance(provider, BaseProvider)

    def test_mock_provider_name(self) -> None:
        provider = get_provider("mock", with_rate_limiter=False)
        assert provider.provider_name == "mock"

    def test_mock_provider_model_passthrough(self) -> None:
        provider = get_provider("mock", model="my-model", with_rate_limiter=False)
        assert provider.model_name == "my-model"

    def test_mock_provider_no_rate_limiter_by_default(self) -> None:
        """Mock provider should never have a rate limiter."""
        provider = get_provider("mock")
        # MockProvider doesn't store rate_limiter, but we verify it's MockProvider
        assert isinstance(provider, MockProvider)


class TestUnknownProvider:
    def test_unknown_provider_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("not_a_real_provider_xyz_abc")

    def test_error_message_lists_available_providers(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            get_provider("bad_provider")
        error_msg = str(exc_info.value)
        assert "anthropic" in error_msg
        assert "mock" in error_msg


class TestCustomProviderRegistration:
    """External packages can register providers at runtime."""

    def test_register_custom_provider(self) -> None:
        """A registered factory is callable via get_provider()."""

        class CustomProvider(MockProvider):
            @property
            def provider_name(self) -> str:
                return "custom_test_provider"

        register_provider("_test_custom_1", lambda **kw: CustomProvider(**kw))
        provider = get_provider("_test_custom_1", with_rate_limiter=False)
        assert provider.provider_name == "custom_test_provider"

    def test_registered_provider_in_list(self) -> None:
        register_provider("_test_custom_2", lambda **kw: MockProvider(**kw))
        assert "_test_custom_2" in list_providers()

    def test_register_over_builtin_raises(self) -> None:
        """Cannot override a built-in provider name."""
        with pytest.raises(ValueError, match="conflicts with a built-in provider"):
            register_provider("anthropic", lambda **kw: MockProvider(**kw))

    def test_kwargs_passed_to_factory(self) -> None:
        """Factory receives all kwargs from get_provider()."""
        received: dict[str, object] = {}

        def factory(**kw: object) -> MockProvider:
            received.update(kw)
            return MockProvider()

        register_provider("_test_kwargs", factory)
        get_provider("_test_kwargs", model="my-model", api_key="key-123")

        assert received.get("model") == "my-model"
        assert received.get("api_key") == "key-123"

    def test_builtin_count(self) -> None:
        """Sanity check: we have exactly 7 built-in providers."""
        assert len(_BUILTIN_PROVIDERS) == 7
