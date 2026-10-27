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
    error = next((e for e in chain if isinstance(e, ProviderError)), None)
    for classify in (_rate_limit_hint, _auth_hint, _not_found_hint, _unreachable_hint):
        hint = classify(chain, error, model)
        if hint is not None:
            return hint
    return None


def _provider(error: ProviderError | None) -> tuple[str | None, str]:
    """The provider's name if known, and how a message names it either way."""
    known = error.provider if error is not None else None
    return known, known or "the provider"


def _rate_limit_hint(
    chain: list[BaseException], error: ProviderError | None, model: str | None
) -> ProviderErrorHint | None:
    if not any(isinstance(e, RateLimitError) for e in chain) and _status(error) != 429:
        return None
    known, name = _provider(error)
    return ProviderErrorHint(
        "rate_limit",
        f"{name} is rate limiting requests. Wait a few minutes, then run it again.",
        provider=known,
    )


def _auth_hint(
    chain: list[BaseException], error: ProviderError | None, model: str | None
) -> ProviderErrorHint | None:
    if _status(error) not in (401, 403):
        return None
    known, name = _provider(error)
    return ProviderErrorHint(
        "auth", f"API key rejected by {name}. Update it in Settings.", provider=known
    )


def _not_found_hint(
    chain: list[BaseException], error: ProviderError | None, model: str | None
) -> ProviderErrorHint | None:
    if _status(error) != 404:
        return None
    known, name = _provider(error)
    named = f"Model {model}" if model else "The configured model"
    # A bare 404 is as often a wrong base URL path as a missing model.
    if "model" in str(error).lower():
        kind: ProviderIssueKind = "model_not_found"
        message = f"{named} was not found at {name}. Choose another model in Settings."
    else:
        kind = "not_found"
        message = f"{named} or the base URL path was not found at {name}. Check both in Settings."
    return ProviderErrorHint(kind, message, provider=known, model=model)


def _unreachable_hint(
    chain: list[BaseException], error: ProviderError | None, model: str | None
) -> ProviderErrorHint | None:
    if not any(_is_connect_error(e) for e in chain):
        return None
    known, name = _provider(error)
    target = next((url for e in chain if (url := _request_origin(e))), None)
    return ProviderErrorHint(
        "unreachable",
        f"Could not reach {target or name}. Is the server running and the base URL correct?",
        provider=known,
        target=target,
    )


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
    # No request attached (AttributeError; httpx raises RuntimeError when
    # unset) or an unparseable URL (ValueError).
    except (AttributeError, RuntimeError, ValueError):
        return None
    host = parts.netloc.rpartition("@")[2]
    if not parts.scheme or not host:
        return None
    return f"{parts.scheme}://{host}"
