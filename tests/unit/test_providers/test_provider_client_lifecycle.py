"""A provider's SDK client is closed on the event loop that used it (#2946).

The CLI runs one provider through several ``asyncio.run`` calls. The SDK client
pools its connections on the loop that opened them; left pooled when that loop
closes, they can no longer be closed, and the SDK's ``__del__`` schedules an
``aclose()`` on whichever loop is running that dies with ``Event loop is
closed`` in a task nobody awaits.

Every test talks to a loopback fake server over real sockets: the fault is in
the connection pool, which a mocked client does not have.
"""

from __future__ import annotations

import asyncio
import gc
import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("anthropic", reason="anthropic SDK not installed")
pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.llm.anthropic import AnthropicProvider
from repowise.core.providers.llm.base import (
    BaseProvider,
    SdkClientOwner,
    close_provider_clients,
)
from repowise.core.providers.llm.deepseek import DeepSeekProvider
from repowise.core.providers.llm.mock import MockProvider
from repowise.core.providers.llm.ollama import OllamaProvider
from repowise.core.providers.llm.openai import OpenAIProvider
from repowise.core.providers.llm.openrouter import OpenRouterProvider

_ANTHROPIC_REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-5",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
_OPENAI_REPLY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: the connection stays pooled

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length", 0)))
        reply = _ANTHROPIC_REPLY if self.path.endswith("/messages") else _OPENAI_REPLY
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def endpoint() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


_PROVIDERS: dict[str, Callable[[str], BaseProvider]] = {
    "anthropic": lambda url: AnthropicProvider(api_key="k", model="claude-sonnet-4-5", base_url=url),
    "openai": lambda url: OpenAIProvider(api_key="k", model="gpt-5.4-nano", base_url=f"{url}/v1"),
    "deepseek": lambda url: DeepSeekProvider(api_key="k", base_url=f"{url}/v1"),
    "openrouter": lambda url: OpenRouterProvider(api_key="k", base_url=f"{url}/v1"),
}


def _one_loop(coro: Any, *, close: bool) -> Any:
    """One ``asyncio.run`` step, the way ``run_async`` drives it."""

    async def run() -> Any:
        try:
            return await coro
        finally:
            if close:
                await close_provider_clients()

    return asyncio.run(run())


def _unretrieved_after_drop(make: Callable[[], BaseProvider], *, close: bool) -> list[str]:
    """Two steps on two loops, then drop the provider while a third loop runs.

    Returns what that loop's exception handler was told.
    """
    provider: BaseProvider | None = make()

    async def step() -> str:
        assert provider is not None
        return (await provider.generate("s", "u", max_tokens=16)).content

    assert _one_loop(step(), close=close) == "ok"
    assert _one_loop(step(), close=close) == "ok"

    seen: list[str] = []

    async def later() -> None:
        nonlocal provider
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: seen.append(repr(context.get("exception")))
        )
        provider = None
        gc.collect()
        await asyncio.sleep(0.2)
        gc.collect()

    asyncio.run(later())
    gc.collect()
    return seen


@pytest.fixture
def sdk_owned_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``OpenAIProvider`` treat the fake server as a remote endpoint.

    For a loopback URL it hands the SDK its own ``httpx.AsyncClient``, which
    has no ``__del__`` and so never shows the fault. A real endpoint gets the
    SDK's client wrapper, the one that schedules ``aclose()`` when collected.
    """
    monkeypatch.setattr("repowise.core.providers.llm.openai._is_loopback_url", lambda url: False)


@pytest.mark.usefixtures("sdk_owned_http_client")
@pytest.mark.parametrize("name", sorted(_PROVIDERS))
def test_a_provider_used_across_loops_leaves_no_unretrieved_task(name: str, endpoint: str) -> None:
    assert _unretrieved_after_drop(lambda: _PROVIDERS[name](endpoint), close=True) == []


@pytest.mark.usefixtures("sdk_owned_http_client")
@pytest.mark.parametrize("name", sorted(_PROVIDERS))
def test_without_the_close_the_stray_task_is_what_the_issue_shows(name: str, endpoint: str) -> None:
    """The control: the same sequence with nothing closed is the bug."""
    seen = _unretrieved_after_drop(lambda: _PROVIDERS[name](endpoint), close=False)
    assert any("Event loop is closed" in entry for entry in seen)


def test_closing_once_after_the_last_step_is_too_late(endpoint: str) -> None:
    """Why the close runs per loop: by a later loop the connections are dead."""
    provider = _PROVIDERS["anthropic"](endpoint)

    async def step() -> None:
        await provider.generate("s", "u", max_tokens=16)

    asyncio.run(step())
    with pytest.raises(RuntimeError, match="Event loop is closed"):
        asyncio.run(provider.aclose())


def test_a_closed_provider_gets_a_fresh_client_and_keeps_working(endpoint: str) -> None:
    provider = _PROVIDERS["openai"](endpoint)
    first = provider._client

    async def step() -> str:
        reply = await provider.generate("s", "u", max_tokens=16)
        await provider.aclose()
        return reply.content

    assert asyncio.run(step()) == "ok"
    assert first.is_closed()
    assert provider._client is not first
    assert asyncio.run(step()) == "ok"


def test_the_loopback_openai_client_still_ignores_proxy_settings(endpoint: str) -> None:
    provider = _PROVIDERS["openai"](endpoint)
    asyncio.run(provider.aclose())
    assert provider._client._client._trust_env is False


def test_ollama_owns_a_client_too() -> None:
    provider = OllamaProvider(base_url="http://127.0.0.1:9")
    first = provider._client
    asyncio.run(provider.aclose())
    assert isinstance(provider, SdkClientOwner)
    assert first.is_closed() and provider._client is not first


def test_a_client_put_in_its_place_is_left_alone() -> None:
    """Tests swap ``_client`` for a double; closing must not swap it back."""
    provider = AnthropicProvider(api_key="k")
    double = MagicMock()
    provider._client = double
    asyncio.run(provider.aclose())
    assert provider._client is double
    double.close.assert_not_called()


def test_a_provider_with_nothing_to_close_has_a_no_op_hook() -> None:
    asyncio.run(MockProvider().aclose())


def test_a_client_that_will_not_close_does_not_fail_the_step() -> None:
    provider = AnthropicProvider(api_key="k")

    async def boom() -> None:
        raise RuntimeError("Event loop is closed")

    provider._owned_client = MagicMock(close=boom)
    asyncio.run(close_provider_clients())


def test_a_dropped_provider_is_not_kept_alive_to_be_closed() -> None:
    from repowise.core.providers.llm import base

    provider = AnthropicProvider(api_key="k")
    assert provider in base._CLIENT_OWNERS
    before = len(base._CLIENT_OWNERS)
    del provider
    gc.collect()
    assert len(base._CLIENT_OWNERS) == before - 1
