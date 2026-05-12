"""Unit tests for OpenAICompatibleEmbedder.

Tests verify that OpenAI-compatible embedders (Ollama, LocalAI, etc.) can be
instantiated with appropriate fallback behavior for local servers that don't
require authentication.

All tests mock the OpenAI SDK — no real API calls are made.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.embedding.openai_compatible import OpenAICompatibleEmbedder
from repowise.core.providers.embedding.registry import get_embedder

# ---------------------------------------------------------------------------
# Base URL resolution
# ---------------------------------------------------------------------------


def test_uses_explicit_base_url():
    """When base_url is passed explicitly, it takes priority."""
    with patch.dict(os.environ, {}, clear=True):
        emb = OpenAICompatibleEmbedder(api_key="test", base_url="http://localhost:11434/v1")
        assert emb._base_url == "http://localhost:11434/v1"


def test_reads_openai_compatible_base_url_env():
    """When OPENAI_COMPATIBLE_BASE_URL env var is set, use it."""
    with patch.dict(
        os.environ, {"OPENAI_COMPATIBLE_BASE_URL": "http://localhost:8080/v1"}, clear=True
    ):
        emb = OpenAICompatibleEmbedder(api_key="test")
        assert emb._base_url == "http://localhost:8080/v1"


def test_reads_openai_base_url_env():
    """When only OPENAI_BASE_URL env var is set, use it."""
    with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://localhost:9999/v1"}, clear=True):
        emb = OpenAICompatibleEmbedder(api_key="test")
        assert emb._base_url == "http://localhost:9999/v1"


def test_compatible_base_url_takes_priority_over_openai_base_url():
    """OPENAI_COMPATIBLE_BASE_URL takes precedence over OPENAI_BASE_URL."""
    with patch.dict(
        os.environ,
        {
            "OPENAI_COMPATIBLE_BASE_URL": "http://localhost:11434/v1",
            "OPENAI_BASE_URL": "http://localhost:9999/v1",
        },
        clear=True,
    ):
        emb = OpenAICompatibleEmbedder(api_key="test")
        assert emb._base_url == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# API key resolution and placeholder behavior
# ---------------------------------------------------------------------------


def test_api_key_placeholder_for_local_servers():
    """When no API key is supplied, instantiation succeeds (doesn't raise ValueError)."""
    with patch.dict(os.environ, {}, clear=True):
        emb = OpenAICompatibleEmbedder(base_url="http://localhost:11434/v1")
        # Should not raise ValueError
        assert emb._api_key is not None  # Will be "none" placeholder


def test_explicit_api_key_used():
    """When api_key is passed explicitly, that key is used."""
    with patch.dict(os.environ, {}, clear=True):
        emb = OpenAICompatibleEmbedder(api_key="sk-test")
        assert emb._api_key == "sk-test"


def test_reads_openai_compatible_api_key_env():
    """When OPENAI_COMPATIBLE_API_KEY env var is set, use it."""
    with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "sk-compatible"}, clear=True):
        emb = OpenAICompatibleEmbedder()
        assert emb._api_key == "sk-compatible"


def test_reads_openai_api_key_env():
    """When only OPENAI_API_KEY env var is set, use it."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai"}, clear=True):
        emb = OpenAICompatibleEmbedder()
        assert emb._api_key == "sk-openai"


def test_compatible_api_key_takes_priority_over_openai_api_key():
    """OPENAI_COMPATIBLE_API_KEY takes precedence over OPENAI_API_KEY."""
    with patch.dict(
        os.environ,
        {"OPENAI_COMPATIBLE_API_KEY": "sk-compatible", "OPENAI_API_KEY": "sk-openai"},
        clear=True,
    ):
        emb = OpenAICompatibleEmbedder()
        assert emb._api_key == "sk-compatible"


# ---------------------------------------------------------------------------
# Dimensions property
# ---------------------------------------------------------------------------


def test_dimensions_default_1536():
    """dimensions property returns 1536 as default."""
    with patch.dict(os.environ, {}, clear=True):
        emb = OpenAICompatibleEmbedder(api_key="test")
        assert emb.dimensions == 1536


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_registered_in_registry():
    """get_embedder('openai_compatible') resolves without ImportError."""
    with patch.dict(os.environ, {}, clear=True):
        embedder = get_embedder(
            "openai_compatible", api_key="test", base_url="http://localhost:11434/v1"
        )
        assert embedder is not None
        assert embedder._base_url == "http://localhost:11434/v1"
