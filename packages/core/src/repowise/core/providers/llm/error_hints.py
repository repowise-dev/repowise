"""Turn a provider failure into a short hint a person can act on.

Raw provider errors are long SDK strings ("Error code: 401 - {'type': ...}")
that say what broke but not what to do. Job records, the web UI and the CLI
show the hint instead; the raw error stays in the log. Hints never quote the
error text, so a key echoed back by a provider cannot leak through them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from repowise.core.providers.llm.base import ProviderError, RateLimitError

ProviderIssueKind = Literal["auth", "rate_limit", "unreachable", "model_not_found"]

# Status-less SDK and HTTP-client connection failures, matched by name so the
# check needs neither SDK installed.
_CONNECTION_ERROR_NAMES = frozenset(
    {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout"}
)


@dataclass(frozen=True)
class ProviderErrorHint:
    kind: ProviderIssueKind
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "message": self.message}


def describe_provider_error(
    exc: BaseException, *, model: str | None = None
) -> ProviderErrorHint | None:
    """The actionable hint for *exc*, or ``None`` when it is not a provider
    failure this recognises (an unknown error keeps its own text)."""
    chain = _exception_chain(exc)
    provider_error = next((e for e in chain if isinstance(e, ProviderError)), None)
    provider = provider_error.provider if provider_error is not None else "the provider"

    if any(isinstance(e, RateLimitError) for e in chain) or _status(provider_error) == 429:
        return ProviderErrorHint(
            "rate_limit",
            f"{provider} is rate limiting requests. Wait a few minutes, then run it again.",
        )
    status = _status(provider_error)
    if status in (401, 403):
        return ProviderErrorHint(
            "auth", f"API key rejected by {provider}. Update it in Settings."
        )
    if status == 404:
        named = f"Model {model}" if model else "The configured model"
        return ProviderErrorHint(
            "model_not_found",
            f"{named} was not found at {provider}. Choose another model in Settings.",
        )
    if any(_is_connection_error(e) for e in chain):
        target = next((url for e in chain if (url := _request_origin(e))), None)
        where = target or provider
        return ProviderErrorHint(
            "unreachable",
            f"Could not reach {where}. Is the server running and the base URL correct?",
        )
    return None


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _status(exc: ProviderError | None) -> int | None:
    return exc.status_code if exc is not None else None


def _is_connection_error(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionError) or type(exc).__name__ in _CONNECTION_ERROR_NAMES


def _request_origin(exc: BaseException) -> str | None:
    """``scheme://host:port`` of the request *exc* failed on, without the
    path, query or credentials a full URL can carry."""
    try:
        parts = urlsplit(str(exc.request.url))  # type: ignore[attr-defined]
        port = f":{parts.port}" if parts.port else ""
    except Exception:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.hostname}{port}"
