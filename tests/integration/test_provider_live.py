"""Live integration tests for LLM providers.

These tests make real API calls. They are skipped automatically when the
required environment variable / API key is not present.

Run a specific provider:
    pytest tests/integration/test_provider_live.py -k openai -v
    pytest tests/integration/test_provider_live.py -k gemini -v
    pytest tests/integration/test_provider_live.py -k anthropic -v
"""

from __future__ import annotations

import os

import pytest

from repowise.core.providers.llm.base import GeneratedResponse

# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")


@pytest.mark.skipif(not OPENAI_KEY, reason="OPENAI_API_KEY not set")
@pytest.mark.parametrize("model", ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4"])
async def test_openai_live(model):
    from repowise.core.providers.llm.openai import OpenAIProvider

    provider = OpenAIProvider(api_key=OPENAI_KEY, model=model)
    result = await provider.generate(
        system_prompt="You are a concise assistant.",
        user_prompt="Reply with exactly: OK",
        max_tokens=16,
    )
    assert isinstance(result, GeneratedResponse)
    assert result.content.strip()
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    print(
        f"\n[{model}] tokens: {result.input_tokens}in / {result.output_tokens}out | content: {result.content!r}"
    )


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")

_has_google_genai = True
try:
    import google.genai  # noqa: F401
except ImportError:
    _has_google_genai = False


@pytest.mark.skipif(
    not GEMINI_KEY or not _has_google_genai,
    reason="GEMINI_API_KEY not set or google-genai not installed",
)
@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
    ],
)
async def test_gemini_live(model):
    from repowise.core.providers.llm.gemini import GeminiProvider

    provider = GeminiProvider(api_key=GEMINI_KEY, model=model)
    result = await provider.generate(
        system_prompt="You are a concise assistant.",
        user_prompt="Reply with exactly: OK",
        max_tokens=16,
    )
    assert isinstance(result, GeneratedResponse)
    assert result.content.strip()
    print(
        f"\n[{model}] tokens: {result.input_tokens}in / {result.output_tokens}out | content: {result.content!r}"
    )


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


@pytest.mark.skipif(not ANTHROPIC_KEY, reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"])
async def test_anthropic_live(model):
    from repowise.core.providers.llm.anthropic import AnthropicProvider

    provider = AnthropicProvider(api_key=ANTHROPIC_KEY, model=model)
    result = await provider.generate(
        system_prompt="You are a concise assistant.",
        user_prompt="Reply with exactly: OK",
        max_tokens=16,
    )
    assert isinstance(result, GeneratedResponse)
    assert result.content.strip()
    print(
        f"\n[{model}] tokens: {result.input_tokens}in / {result.output_tokens}out | content: {result.content!r}"
    )


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")


@pytest.mark.skipif(not OPENROUTER_KEY, reason="OPENROUTER_API_KEY not set")
@pytest.mark.parametrize(
    "model",
    ["anthropic/claude-sonnet-4.6", "google/gemini-3.1-flash-lite-preview"],
)
async def test_openrouter_live(model):
    from repowise.core.providers.llm.openrouter import OpenRouterProvider

    provider = OpenRouterProvider(api_key=OPENROUTER_KEY, model=model)
    result = await provider.generate(
        system_prompt="You are a concise assistant.",
        user_prompt="Reply with exactly: OK",
        max_tokens=16,
    )
    assert isinstance(result, GeneratedResponse)
    assert result.content.strip()
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    print(
        f"\n[{model}] tokens: {result.input_tokens}in / {result.output_tokens}out | content: {result.content!r}"
    )
