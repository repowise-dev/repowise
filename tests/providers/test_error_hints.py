"""Provider failures map to a hint a person can act on, without the raw text."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.providers.llm.base import ProviderError, RateLimitError
from repowise.core.providers.llm.error_hints import describe_provider_error


def _raised_from(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_names_the_provider_and_the_fix(status):
    exc = ProviderError("anthropic", "Error code: 401 - invalid x-api-key sk-ant-secret", status)

    hint = describe_provider_error(exc)

    assert hint is not None and hint.kind == "auth"
    assert hint.message == "API key rejected by anthropic. Update it in Settings."
    assert "sk-ant" not in hint.message


def test_rate_limit():
    hint = describe_provider_error(RateLimitError("openai", "slow down"))

    assert hint is not None and hint.kind == "rate_limit"
    assert "openai" in hint.message


def test_missing_model_names_the_model_when_known():
    exc = ProviderError("ollama", "model 'llama9' not found", 404)

    assert describe_provider_error(exc, model="llama9").message.startswith("Model llama9 ")
    assert describe_provider_error(exc).kind == "model_not_found"


def test_unreachable_local_server_names_its_origin_without_path_or_credentials():
    class APIConnectionError(Exception):
        request = SimpleNamespace(url="http://user:pw@localhost:11434/v1/chat/completions?k=1")

    exc = _raised_from(ProviderError("ollama", "Connection error."), APIConnectionError())

    hint = describe_provider_error(exc)

    assert hint is not None and hint.kind == "unreachable"
    assert "Could not reach http://localhost:11434." in hint.message
    assert "pw" not in hint.message and "chat" not in hint.message


def test_connection_refused_without_a_request_falls_back_to_the_provider():
    exc = _raised_from(ProviderError("litellm", "boom"), ConnectionRefusedError())

    assert describe_provider_error(exc).message.startswith("Could not reach litellm.")


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("artifact check rejected the page"),
        ProviderError("openai", "server error", 500),
    ],
)
def test_unrecognised_errors_get_no_hint(exc):
    assert describe_provider_error(exc) is None
