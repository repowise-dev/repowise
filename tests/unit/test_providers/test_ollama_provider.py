"""Unit tests for OllamaProvider."""

from __future__ import annotations

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.llm.ollama import OllamaProvider


def test_available_model_options_reads_local_tags(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "models": [
                    {
                        "name": "llama3.2:latest",
                        "details": {
                            "family": "llama",
                            "parameter_size": "3B",
                        },
                    },
                    {"model": "qwen2.5-coder:7b"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = OllamaProvider(base_url="http://localhost:11434").available_model_options()

    assert captured["url"] == "http://localhost:11434/api/tags"
    models = [option.model for option in options]
    assert models == ["llama3.2:latest", "qwen2.5-coder:7b"]
    llama = options[0]
    assert llama.source == "local"
    assert llama.notes == "llama, 3B"
    assert llama.reasoning_modes == ("auto",)
