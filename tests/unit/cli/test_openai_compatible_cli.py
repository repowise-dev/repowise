"""Unit tests for OpenAI-compatible embedder CLI integration."""

from __future__ import annotations

import click

from repowise.cli.commands.init_cmd import _resolve_embedder, init_command
from repowise.cli.commands.reindex_cmd import reindex_command
from repowise.cli.ui import _resolve_embedder_from_env


class TestResolveEmbedderFromEnv:
    """Test _resolve_embedder_from_env in ui.py."""

    def test_resolve_embedder_prefers_compatible_url_env(self, monkeypatch):
        """With OPENAI_COMPATIBLE_BASE_URL set, should return 'openai_compatible'."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        
        monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        assert _resolve_embedder_from_env() == "openai_compatible"

    def test_resolve_embedder_openai_base_url_env(self, monkeypatch):
        """With OPENAI_BASE_URL set (no GEMINI key), should return 'openai_compatible'."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        assert _resolve_embedder_from_env() == "openai_compatible"

    def test_resolve_embedder_prefers_gemini_over_compatible(self, monkeypatch):
        """GEMINI + OPENAI_COMPATIBLE_BASE_URL -> returns 'gemini'."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        assert _resolve_embedder_from_env() == "gemini"

    def test_resolve_embedder_prefers_openai_api_key_when_no_base_url(self, monkeypatch):
        """With OPENAI_API_KEY but no base URL, should return 'openai'."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert _resolve_embedder_from_env() == "openai"


class TestResolveEmbedderInitCmd:
    """Test _resolve_embedder in init_cmd.py."""

    def test_resolve_embedder_init_cmd_with_compatible_url(self, monkeypatch):
        """With OPENAI_COMPATIBLE_BASE_URL set, should return 'openai_compatible'."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        
        monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        assert _resolve_embedder(None) == "openai_compatible"

    def test_resolve_embedder_init_cmd_with_openai_base_url(self, monkeypatch):
        """With OPENAI_BASE_URL set, should return 'openai_compatible'."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        assert _resolve_embedder(None) == "openai_compatible"

    def test_resolve_embedder_init_cmd_prefers_gemini(self, monkeypatch):
        """GEMINI key takes precedence over openai_compatible base URL."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        assert _resolve_embedder(None) == "gemini"

    def test_resolve_embedder_init_cmd_explicit_flag(self, monkeypatch):
        """Explicit --embedder flag should take precedence."""
        # Clear all embedder env vars for isolation
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        assert _resolve_embedder("openai_compatible") == "openai_compatible"


class TestInitCommand:
    """Test init_command click options."""

    def test_init_embedder_choice_includes_openai_compatible(self):
        """The --embedder option should include 'openai_compatible' in choices."""
        # Get the --embedder option from the command
        embedder_option = None
        for param in init_command.params:
            if param.name == "embedder_name":
                embedder_option = param
                break

        assert embedder_option is not None, "embedder option not found"
        assert isinstance(embedder_option.type, click.Choice)
        assert "openai_compatible" in embedder_option.type.choices

    def test_init_embedder_choice_includes_openrouter(self):
        """The --embedder option should include 'openrouter' in choices."""
        # Get the --embedder option from the command
        embedder_option = None
        for param in init_command.params:
            if param.name == "embedder_name":
                embedder_option = param
                break

        assert embedder_option is not None, "embedder option not found"
        assert isinstance(embedder_option.type, click.Choice)
        assert "openrouter" in embedder_option.type.choices


class TestReindexCommand:
    """Test reindex_command click options."""

    def test_reindex_choice_includes_openai_compatible(self):
        """The --embedder option should include 'openai_compatible' in choices."""
        # Get the --embedder option from the command
        embedder_option = None
        for param in reindex_command.params:
            if param.name == "embedder":
                embedder_option = param
                break

        assert embedder_option is not None, "embedder option not found"
        assert isinstance(embedder_option.type, click.Choice)
        assert "openai_compatible" in embedder_option.type.choices

    def test_reindex_choice_includes_openrouter(self):
        """The --embedder option should include 'openrouter' in choices."""
        # Get the --embedder option from the command
        embedder_option = None
        for param in reindex_command.params:
            if param.name == "embedder":
                embedder_option = param
                break

        assert embedder_option is not None, "embedder option not found"
        assert isinstance(embedder_option.type, click.Choice)
        assert "openrouter" in embedder_option.type.choices
