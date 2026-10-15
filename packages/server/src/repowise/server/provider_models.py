"""Live model lists for the settings form and the chat model picker.

A provider's ``available_model_options`` asks its ``/models`` endpoint (or a
local CLI), which can take seconds, so a list is kept for a short while per
provider, endpoint and key fingerprint (never the key itself), and concurrent
requests for the same list share one fetch. When nothing live comes back the
spec's built-in list is returned with the reason.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from repowise.core.providers.llm.openai import LOCAL_SERVER_API_KEY
from repowise.core.providers.llm.registry import get_provider
from repowise.core.providers.llm.resolve import (
    UntrustedBaseUrlError,
    llm_provider_kwargs,
    resolve_llm,
)
from repowise.core.providers.llm.specs import PROVIDER_SPECS
from repowise.server.provider_config import _redact_key

_LIVE_TTL = 120.0
# Short, so starting a local server and opening the picker again recovers.
_FAILURE_TTL = 15.0
_MAX_MODELS = 200
_MAX_ENTRIES = 64

# Default ports of the local servers people point an OpenAI-compatible base
# URL at, to name the likely culprit when nothing answers.
_LOCAL_SERVER_PORTS = {1234: "LM Studio", 11434: "Ollama", 8000: "vLLM"}


@dataclass(frozen=True, slots=True)
class _Fetched:
    models: tuple[dict[str, str], ...]
    live: bool
    error: str | None


_cache: dict[tuple[str, str, str], tuple[float, _Fetched]] = {}
_inflight: dict[tuple[str, str, str], asyncio.Future[_Fetched]] = {}


def clear_model_cache() -> None:
    _cache.clear()


async def list_provider_models(
    provider_id: str,
    repo_path: str | Path | None = None,
    *,
    base_url: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """``{models, source, default_model, error}`` for *provider_id*.

    ``base_url`` probes an endpoint before it is saved. The stored key is only
    sent to the endpoint it was saved for, so a probe of any other URL goes
    without one. ``refresh`` skips the cache, not the shared in-flight fetch.
    """
    spec = PROVIDER_SPECS[provider_id]
    selection = resolve_llm(repo_path, provider=provider_id, override_source="request")
    default_model = selection.model or spec.default_model
    probing = bool(base_url) and base_url != selection.base_url
    try:
        # A probe replaces the saved URL, so that URL's trust does not matter.
        kwargs = llm_provider_kwargs(
            replace(selection, base_url=None) if probing else selection, repo_path
        )
    except UntrustedBaseUrlError as exc:
        # The message names the URL without credentials, never the key.
        return _catalog_only(spec, default_model, str(exc))
    if probing:
        kwargs["base_url"] = base_url
        if spec.api_key_envs:
            kwargs["api_key"] = LOCAL_SERVER_API_KEY

    key = (provider_id, kwargs.get("base_url") or "", _fingerprint(kwargs.get("api_key")))
    fetched = None if refresh else _cached(key)
    if fetched is None:
        fetched = await _shared_fetch(key, provider_id, kwargs)

    ids = [m["id"] for m in fetched.models]
    if not fetched.live:
        ids = [*spec.models, *ids]
    labels = {m["id"]: m["label"] for m in fetched.models if m.get("label")}
    models = [
        {"id": model_id, **({"label": labels[model_id]} if model_id in labels else {})}
        for model_id in sort_models([*ids, default_model], default_model)
    ]
    return {
        "models": models,
        "source": "live" if fetched.live else "catalog",
        "default_model": default_model,
        "error": fetched.error,
    }


def _catalog_only(spec: Any, default_model: str, error: str) -> dict[str, Any]:
    return {
        "models": [{"id": m} for m in sort_models([*spec.models, default_model], default_model)],
        "source": "catalog",
        "default_model": default_model,
        "error": error,
    }


def _fingerprint(api_key: Any) -> str:
    if not api_key:
        return ""
    return hashlib.sha256(str(api_key).encode()).hexdigest()[:16]


def _cached(key: tuple[str, str, str]) -> _Fetched | None:
    hit = _cache.get(key)
    if hit is None or hit[0] < time.monotonic():
        return None
    return hit[1]


async def _shared_fetch(
    key: tuple[str, str, str], provider_id: str, kwargs: dict[str, Any]
) -> _Fetched:
    pending = _inflight.get(key)
    if pending is None:
        pending = asyncio.ensure_future(asyncio.to_thread(_fetch, provider_id, kwargs))
        _inflight[key] = pending

        def settle(done: asyncio.Future[_Fetched]) -> None:
            _inflight.pop(key, None)
            if not done.cancelled() and done.exception() is None:
                _store(key, done.result())

        pending.add_done_callback(settle)
    # shield: one caller hanging up must not cancel the fetch for the rest.
    return await asyncio.shield(pending)


def _store(key: tuple[str, str, str], fetched: _Fetched) -> None:
    if len(_cache) >= _MAX_ENTRIES:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    ttl = _LIVE_TTL if fetched.live else _FAILURE_TTL
    _cache[key] = (time.monotonic() + ttl, fetched)


def _fetch(provider_id: str, kwargs: dict[str, Any]) -> _Fetched:
    """Build the provider and ask it for models. Runs in a worker thread."""
    secret = kwargs.get("api_key")
    base_url = kwargs.get("base_url")
    label = PROVIDER_SPECS[provider_id].label
    try:
        provider = get_provider(provider_id, with_rate_limiter=False, **kwargs)
    except Exception as exc:
        if PROVIDER_SPECS[provider_id].api_key_envs and not secret:
            return _Fetched((), False, f"Add an API key for {label} to list its models.")
        return _Fetched((), False, _redact(f"{label} is not set up: {exc}", secret))
    # A strict discovery (OpenAI-compatible) raises with the reason; the
    # general one falls back to the configured model silently.
    discover = getattr(provider, "discover_model_options", None)
    try:
        options = discover() if callable(discover) else provider.available_model_options()
    except Exception as exc:
        return _Fetched((), False, _redact(describe_fetch_error(exc, base_url, label), secret))
    rows = tuple(
        {"id": o.model, **({"label": o.label} if o.label and o.label != o.model else {})}
        for o in options
        if o.model
    )
    if any(o.source != "fallback" for o in options):
        return _Fetched(rows, True, None)
    # One fallback option means discovery failed; several are a curated list.
    error = None if len(rows) > 1 else f"Could not list {label} models; showing the built-in list."
    return _Fetched(rows, False, error)


def describe_fetch_error(exc: Exception, base_url: str | None, label: str) -> str:
    """A sentence saying what failed and what to check, for the settings UI."""
    import httpx

    where = base_url or label
    if isinstance(exc, httpx.ConnectError):
        server = _LOCAL_SERVER_PORTS.get(urlparse(base_url or "").port or 0)
        hint = (
            f"Is {server}'s server running?"
            if server
            else "Check that the server is running and the URL is right."
        )
        return f"Could not reach {where}. {hint}"
    if isinstance(exc, httpx.TimeoutException):
        return f"{where} did not answer within 5 seconds."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return f"{where} turned the request down (HTTP {code}). Check the API key."
        if code == 404:
            return (
                f"{where} has no /models endpoint (HTTP 404). "
                "OpenAI-compatible URLs usually end in /v1."
            )
        return f"{where} answered HTTP {code}."
    return f"Could not list models from {where}: {exc}"


def _redact(text: str, secret: Any) -> str:
    """Drop the key in use, then anything else shaped like one."""
    if secret and secret != LOCAL_SERVER_API_KEY:
        text = text.replace(str(secret), "***")
    return _redact_key(text)


_NUMBER = re.compile(r"\d+")


def sort_models(ids: list[str], default_model: str | None) -> list[str]:
    """Unique ids: the default first, then newest-looking (higher version
    numbers in the id), then alphabetical. Capped at ``_MAX_MODELS``."""
    unique = sorted({i for i in ids if i})

    def version(model_id: str) -> tuple[int, ...]:
        leaf = model_id.rsplit("/", 1)[-1]
        return tuple(int(n) for n in _NUMBER.findall(leaf)[:4])

    unique.sort(key=version, reverse=True)
    if default_model in unique:
        unique.remove(default_model)
        unique.insert(0, default_model)
    return unique[:_MAX_MODELS]
