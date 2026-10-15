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

ProviderIssueKind = Literal["auth", "rate_limit", "unreachable", "model_not_found", "not_found"]

# Status-less SDK and HTTP-client connect failures (openai/anthropic
# APIConnectionError, httpx ConnectError/ConnectTimeout), matched by name so
# the check needs no SDK installed. A reset or broken pipe means the server
# was reached, so it is not "unreachable".
_CONNECT_ERROR_NAMES = frozenset({"APIConnectionError", "ConnectError", "ConnectTimeout"})


@dataclass(frozen=True)
class ProviderErrorHint:
    kind: ProviderIssueKind
    message: str
    #: Values the message names, so a client can word it in its own language.
    provider: str | None = None
    model: str | None = None
    target: str | None = None

    def as_dict(self) -> dict[str, str]:
        fields = {"provider": self.provider, "model": self.model, "target": self.target}
        return {"kind": self.kind, "message": self.message} | {
            k: v for k, v in fields.items() if v
        }


def describe_provider_error(
    exc: BaseException, *, model: str | None = None
) -> ProviderErrorHint | None:
    """The actionable hint for *exc*, or ``None`` when it is not a provider
    failure this recognises (an unknown error keeps its own text)."""
    chain = _exception_chain(exc)
    provider_error = next((e for e in chain if isinstance(e, ProviderError)), None)
    known = provider_error.provider if provider_error is not None else None
    provider = known or "the provider"
    status = _status(provider_error)

    if any(isinstance(e, RateLimitError) for e in chain) or status == 429:
        return ProviderErrorHint(
            "rate_limit",
            f"{provider} is rate limiting requests. Wait a few minutes, then run it again.",
            provider=known,
        )
    if status in (401, 403):
        return ProviderErrorHint(
            "auth", f"API key rejected by {provider}. Update it in Settings.", provider=known
        )
    if status == 404:
        named = f"Model {model}" if model else "The configured model"
        # A bare 404 is as often a wrong base URL path as a missing model.
        if "model" in str(provider_error).lower():
            return ProviderErrorHint(
                "model_not_found",
                f"{named} was not found at {provider}. Choose another model in Settings.",
                provider=known,
                model=model,
            )
        return ProviderErrorHint(
            "not_found",
            f"{named} or the base URL path was not found at {provider}. Check both in Settings.",
            provider=known,
            model=model,
        )
    if any(_is_connect_error(e) for e in chain):
        target = next((url for e in chain if (url := _request_origin(e))), None)
        return ProviderErrorHint(
            "unreachable",
            f"Could not reach {target or provider}. Is the server running and the base URL correct?",
            provider=known,
            target=target,
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


def _is_connect_error(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionRefusedError) or type(exc).__name__ in _CONNECT_ERROR_NAMES


def _request_origin(exc: BaseException) -> str | None:
    """``scheme://host:port`` of the request *exc* failed on, without the
    path, query or credentials a full URL can carry. IPv6 hosts keep their
    brackets."""
    try:
        parts = urlsplit(str(exc.request.url))  # type: ignore[attr-defined]
    except Exception:
        return None
    host = parts.netloc.rpartition("@")[2]
    if not parts.scheme or not host:
        return None
    return f"{parts.scheme}://{host}"
