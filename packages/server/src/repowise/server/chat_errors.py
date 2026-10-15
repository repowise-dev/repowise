"""User-facing copy for the chat failures a person can fix.

A missing provider or a rejected API key is a setup problem, not a server
bug: the chat says what happened and what to do next. Anything else stays an
internal error, logged with its traceback.
"""

from __future__ import annotations

from repowise.core.providers.llm.base import ProviderError

NO_PROVIDER_MESSAGE = (
    "No LLM provider is configured. Open Settings to add one, then send your message again."
)

# Provider SDKs whose exceptions a chat provider may let through unwrapped.
_SDK_PACKAGES = frozenset({"anthropic", "openai", "google", "httpx", "litellm", "ollama"})


def provider_failure(exc: BaseException, provider_name: str) -> str | None:
    """The message for a provider call that failed, or None when *exc* is not one."""
    from_sdk = type(exc).__module__.split(".", 1)[0] in _SDK_PACKAGES
    if not (isinstance(exc, ProviderError) or from_sdk):
        return None
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return (
            f"The {provider_name} provider rejected its API key (HTTP {status}). "
            "Check the key in Settings, then send your message again."
        )
    if isinstance(exc, ProviderError):
        return str(exc)
    if status == 429:
        return (
            f"The {provider_name} provider is rate limiting requests. "
            "Wait a moment, then send your message again."
        )
    return f"The {provider_name} provider failed: {type(exc).__name__}: {exc}"
