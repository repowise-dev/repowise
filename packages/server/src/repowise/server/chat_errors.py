"""User-facing copy for the chat failures a person can fix.

A missing provider or a rejected API key is a setup problem, not a server
bug: the chat says what happened and what to do next. Provider error text can
quote the request (a URL with its key, an echoed body), so the person sees a
plain line and the server log keeps the redacted detail. Anything else stays
an internal error, logged with its traceback.
"""

from __future__ import annotations

import functools
import importlib
import logging

from repowise.core.providers.llm.base import ProviderError
from repowise.core.redaction import redact_secrets

logger = logging.getLogger(__name__)

NO_PROVIDER_MESSAGE = (
    "No LLM provider is configured. Open Settings to add one, then send your message again."
)
INVALID_PROVIDER_MESSAGE = (
    "The chat provider configuration is invalid. Check it in Settings, then send "
    "your message again."
)
PROVIDER_START_MESSAGE = (
    "Could not start the chat provider. Check its settings in Settings; "
    "the server log has the details."
)

# Base classes of the provider SDKs a chat provider may let through unwrapped.
# Matched by class, not package: a tool's own httpx or google.auth error is
# not a provider failure. litellm's errors subclass openai's.
_SDK_ERRORS = (
    ("anthropic", "APIError"),
    ("openai", "OpenAIError"),
    ("google.genai.errors", "APIError"),
    ("google.api_core.exceptions", "GoogleAPIError"),
    ("ollama", "ResponseError"),
)


@functools.cache
def _sdk_error_types() -> tuple[type[BaseException], ...]:
    """The installed SDKs' error bases, imported on the first failure only."""
    found: list[type[BaseException]] = []
    for module, name in _SDK_ERRORS:
        try:
            found.append(getattr(importlib.import_module(module), name))
        except (ImportError, AttributeError):
            continue
    return tuple(found)


def _status(exc: BaseException) -> int | None:
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def provider_failure(exc: BaseException, provider_name: str) -> str | None:
    """The message for a provider call that failed, or None when *exc* is not one."""
    is_provider_error = isinstance(exc, ProviderError)
    if not (is_provider_error or isinstance(exc, _sdk_error_types())):
        return None
    detail = redact_secrets(str(exc))
    logger.warning("chat_provider_failed: %s: %s", type(exc).__name__, detail)
    status = _status(exc)
    if status in (401, 403):
        return (
            f"The {provider_name} provider rejected its API key (HTTP {status}). "
            "Check the key in Settings, then send your message again."
        )
    if status == 429:
        return (
            f"The {provider_name} provider is rate limiting requests. "
            "Wait a moment, then send your message again."
        )
    if is_provider_error:
        # Repowise's own wrapper: its text is written to be read.
        return detail
    http = f", HTTP {status}" if status is not None else ""
    return (
        f"The {provider_name} provider failed ({type(exc).__name__}{http}). "
        "See the server log for details."
    )


def internal_error(exc: BaseException) -> str:
    """The line for a failure no person can fix; the traceback goes to the log."""
    return f"Internal error: {type(exc).__name__}: {redact_secrets(str(exc))}"
