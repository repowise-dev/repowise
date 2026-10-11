"""Shared fixtures for provider tests."""

from __future__ import annotations

import pytest

from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider


@pytest.fixture
def mock_provider() -> MockProvider:
    """A fresh MockProvider instance for each test."""
    return MockProvider()


@pytest.fixture
def preset_response() -> GeneratedResponse:
    """A standard GeneratedResponse for use in preset-based tests."""
    return GeneratedResponse(
        content="## Test Documentation\n\nThis is a test page.",
        input_tokens=100,
        output_tokens=50,
        cached_tokens=0,
        usage={"mock": True},
    )


@pytest.fixture
def two_preset_responses() -> list[GeneratedResponse]:
    """Two distinct GeneratedResponse objects for sequence testing."""
    return [
        GeneratedResponse("First page", 100, 50),
        GeneratedResponse("Second page", 200, 100),
    ]
