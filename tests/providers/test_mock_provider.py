"""Tests for MockProvider.

Verifies that MockProvider correctly implements the BaseProvider interface
and that all test-helper features work as expected. No API keys or network
access are required to run these tests.
"""

from __future__ import annotations

from repowise.core.providers.llm.base import BaseProvider, GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider

# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


class TestBaseProviderInterface:
    """MockProvider must fully satisfy the BaseProvider interface contract."""

    def test_is_base_provider_subclass(self) -> None:
        assert issubclass(MockProvider, BaseProvider)

    def test_provider_name_is_mock(self) -> None:
        provider = MockProvider()
        assert provider.provider_name == "mock"

    def test_model_name_default(self) -> None:
        provider = MockProvider()
        assert provider.model_name == "mock-model-1"

    def test_model_name_custom(self) -> None:
        provider = MockProvider(model="custom-test-model")
        assert provider.model_name == "custom-test-model"

    async def test_generate_returns_generated_response(self) -> None:
        provider = MockProvider()
        result = await provider.generate(
            system_prompt="You are a documentation assistant.",
            user_prompt="Document this function: def add(a, b): return a + b",
        )
        assert isinstance(result, GeneratedResponse)

    async def test_generate_content_is_non_empty_string(self) -> None:
        provider = MockProvider()
        result = await provider.generate(system_prompt="sys", user_prompt="user")
        assert isinstance(result.content, str)
        assert len(result.content) > 0

    async def test_generate_token_counts_are_non_negative(self) -> None:
        provider = MockProvider()
        result = await provider.generate(system_prompt="sys", user_prompt="user")
        assert result.input_tokens >= 0
        assert result.output_tokens >= 0
        assert result.cached_tokens >= 0

    async def test_total_tokens_equals_input_plus_output(self) -> None:
        provider = MockProvider()
        result = await provider.generate(system_prompt="sys", user_prompt="user")
        assert result.total_tokens == result.input_tokens + result.output_tokens

    async def test_usage_dict_is_populated(self) -> None:
        provider = MockProvider()
        result = await provider.generate(system_prompt="sys", user_prompt="user")
        assert isinstance(result.usage, dict)

    async def test_generate_accepts_all_parameters(self) -> None:
        """All BaseProvider.generate() parameters must be accepted."""
        provider = MockProvider()
        result = await provider.generate(
            system_prompt="system",
            user_prompt="user",
            max_tokens=2048,
            temperature=0.0,
            request_id="test-req-001",
        )
        assert isinstance(result, GeneratedResponse)


# ---------------------------------------------------------------------------
# Call tracking
# ---------------------------------------------------------------------------


class TestCallTracking:
    """MockProvider records every call for test-time assertions."""

    async def test_initial_call_count_is_zero(self) -> None:
        provider = MockProvider()
        assert provider.call_count == 0

    async def test_call_count_increments_per_generate(self) -> None:
        provider = MockProvider()
        await provider.generate("sys", "first")
        assert provider.call_count == 1
        await provider.generate("sys", "second")
        assert provider.call_count == 2

    async def test_calls_list_records_each_call(self) -> None:
        provider = MockProvider()
        await provider.generate("sys", "user1")
        await provider.generate("sys", "user2")
        assert len(provider.calls) == 2

    async def test_calls_record_all_arguments(self) -> None:
        provider = MockProvider()
        await provider.generate(
            system_prompt="my system",
            user_prompt="my user",
            max_tokens=2048,
            temperature=0.5,
            request_id="req-abc",
        )
        call = provider.calls[0]
        assert call["system_prompt"] == "my system"
        assert call["user_prompt"] == "my user"
        assert call["max_tokens"] == 2048
        assert call["temperature"] == 0.5
        assert call["request_id"] == "req-abc"

    async def test_calls_list_is_ordered(self) -> None:
        provider = MockProvider()
        await provider.generate("sys", "first")
        await provider.generate("sys", "second")
        assert provider.calls[0]["user_prompt"] == "first"
        assert provider.calls[1]["user_prompt"] == "second"

    async def test_reset_clears_call_count(self) -> None:
        provider = MockProvider()
        await provider.generate("sys", "user")
        provider.reset()
        assert provider.call_count == 0

    async def test_reset_clears_calls_list(self) -> None:
        provider = MockProvider()
        await provider.generate("sys", "user")
        provider.reset()
        assert provider.calls == []

    async def test_reset_allows_reuse(self) -> None:
        provider = MockProvider()
        await provider.generate("sys", "user")
        provider.reset()
        await provider.generate("sys", "new user")
        assert provider.call_count == 1
        assert provider.calls[0]["user_prompt"] == "new user"

    def test_calls_property_returns_copy(self) -> None:
        """Mutating the returned list must not affect internal state."""
        provider = MockProvider()
        calls = provider.calls
        calls.append({"tampered": True})  # type: ignore[arg-type]
        assert provider.call_count == 0


# ---------------------------------------------------------------------------
# Preset responses
# ---------------------------------------------------------------------------


class TestPresetResponses:
    """MockProvider can return specific responses in a controlled sequence."""

    async def test_single_preset_returned_on_first_call(self) -> None:
        response = GeneratedResponse("Only response", 100, 50)
        provider = MockProvider(responses=[response])
        result = await provider.generate("sys", "user")
        assert result.content == "Only response"

    async def test_preset_responses_returned_in_order(self) -> None:
        responses = [
            GeneratedResponse("First", 100, 50),
            GeneratedResponse("Second", 200, 100),
            GeneratedResponse("Third", 300, 150),
        ]
        provider = MockProvider(responses=responses)

        r1 = await provider.generate("sys", "u")
        r2 = await provider.generate("sys", "u")
        r3 = await provider.generate("sys", "u")

        assert r1.content == "First"
        assert r2.content == "Second"
        assert r3.content == "Third"

    async def test_last_preset_repeated_after_exhaustion(self) -> None:
        """Once presets run out, the last one is repeated — never raises."""
        responses = [
            GeneratedResponse("First", 100, 50),
            GeneratedResponse("Last", 200, 100),
        ]
        provider = MockProvider(responses=responses)

        await provider.generate("sys", "u")  # "First"
        await provider.generate("sys", "u")  # "Last"
        r3 = await provider.generate("sys", "u")  # "Last" again
        r4 = await provider.generate("sys", "u")  # "Last" again

        assert r3.content == "Last"
        assert r4.content == "Last"

    async def test_preset_token_counts_preserved(self) -> None:
        response = GeneratedResponse(
            content="content",
            input_tokens=500,
            output_tokens=250,
            cached_tokens=100,
        )
        provider = MockProvider(responses=[response])
        result = await provider.generate("sys", "user")

        assert result.input_tokens == 500
        assert result.output_tokens == 250
        assert result.cached_tokens == 100

    async def test_preset_overrides_fixture_file(self) -> None:
        """When preset responses are provided, fixture files are not loaded."""
        responses = [GeneratedResponse("preset wins", 10, 5)]
        provider = MockProvider(fixture_name="default", responses=responses)
        result = await provider.generate("sys", "user")
        assert result.content == "preset wins"


# ---------------------------------------------------------------------------
# GeneratedResponse dataclass
# ---------------------------------------------------------------------------


class TestGeneratedResponse:
    """GeneratedResponse must behave correctly as a dataclass."""

    def test_default_cached_tokens_is_zero(self) -> None:
        r = GeneratedResponse(content="x", input_tokens=10, output_tokens=5)
        assert r.cached_tokens == 0

    def test_default_usage_is_empty_dict(self) -> None:
        r = GeneratedResponse(content="x", input_tokens=10, output_tokens=5)
        assert r.usage == {}

    def test_total_tokens(self) -> None:
        r = GeneratedResponse(content="x", input_tokens=100, output_tokens=50)
        assert r.total_tokens == 150

    def test_total_tokens_with_zero_values(self) -> None:
        r = GeneratedResponse(content="x", input_tokens=0, output_tokens=0)
        assert r.total_tokens == 0
