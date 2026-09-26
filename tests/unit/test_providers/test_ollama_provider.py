"""Unit tests for OllamaProvider."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.ollama import OllamaProvider


def test_supported_reasoning_modes_are_auto_and_off():
    provider = OllamaProvider(model="test")

    assert provider.supported_reasoning_modes() == ("auto", "off")


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
    model_names = [option.model for option in options]
    assert model_names == ["llama3.2:latest", "qwen2.5-coder:7b"]
    llama = options[0]
    assert llama.source == "local"
    assert llama.notes == "llama, 3B"
    assert llama.reasoning_modes == ("auto", "off")


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _serve(monkeypatch, handler) -> list[httpx.Request]:
    """Route the provider's HTTP calls to ``handler`` and record each request."""
    seen: list[httpx.Request] = []

    async def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response = handler(request, len(seen))
        return await response if asyncio.iscoroutine(response) else response

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(recording), **kw),
    )
    return seen


def _stream(text: str = "Generated wiki content") -> httpx.Response:
    lines = [
        {"message": {"role": "assistant", "content": text[:5]}, "done": False},
        {"message": {"role": "assistant", "content": text[5:]}, "done": False},
        {
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 10,
            "eval_count": 20,
        },
    ]
    return httpx.Response(200, text="\n".join(json.dumps(line) for line in lines))


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _provider(base_url: str = "http://localhost:9999") -> OllamaProvider:
    return OllamaProvider(model="test-model", base_url=base_url)


async def test_generate_uses_native_chat_with_a_context_window(monkeypatch):
    seen = _serve(monkeypatch, lambda request, n: _stream())

    result = await _provider().generate("system", "user", max_tokens=100)

    assert str(seen[0].url) == "http://localhost:9999/api/chat"
    body = _body(seen[0])
    assert body["model"] == "test-model"
    assert body["stream"] is True
    assert body["options"]["num_predict"] == 100
    assert body["options"]["num_ctx"] == 8192
    assert "think" not in body
    assert result.content == "Generated wiki content"
    assert (result.input_tokens, result.output_tokens) == (10, 20)
    assert (result.stop_reason, result.provider_stop_reason) == ("end_turn", "stop")


async def test_base_url_with_v1_suffix_still_reaches_native_api(monkeypatch):
    seen = _serve(monkeypatch, lambda request, n: _stream())
    await _provider("http://localhost:9999/v1").generate("s", "u", max_tokens=10)
    assert str(seen[0].url) == "http://localhost:9999/api/chat"


async def test_generate_off_disables_thinking(monkeypatch):
    seen = _serve(monkeypatch, lambda request, n: _stream())
    await _provider().generate("system", "user", reasoning="off")
    assert _body(seen[0])["think"] is False


@pytest.mark.parametrize("reasoning", ["none", "minimal", "low"])
async def test_generate_rejects_unsupported_reasoning_modes(monkeypatch, reasoning):
    seen = _serve(monkeypatch, lambda request, n: _stream())
    with pytest.raises(ProviderError, match=f"reasoning='{reasoning}' is not supported"):
        await _provider().generate("system", "user", reasoning=reasoning)
    assert seen == []


async def test_context_window_grows_to_fit_and_never_shrinks(monkeypatch):
    seen = _serve(monkeypatch, lambda request, n: _stream())
    provider = _provider()

    await provider.generate("s", "x" * 40_000, max_tokens=4096)
    await provider.generate("s", "short", max_tokens=100)

    assert [_body(r)["options"]["num_ctx"] for r in seen] == [32768, 32768]


async def test_context_window_env_override(monkeypatch):
    monkeypatch.setenv("REPOWISE_OLLAMA_NUM_CTX", "12000")
    seen = _serve(monkeypatch, lambda request, n: _stream())
    await _provider().generate("s", "x" * 60_000, max_tokens=4096)
    assert _body(seen[0])["options"]["num_ctx"] == 12000


@pytest.mark.parametrize("parallel, expected", [(None, 1), ("2", 2)])
async def test_in_flight_requests_capped_at_server_parallelism(monkeypatch, parallel, expected):
    if parallel is None:
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", parallel)
    in_flight = peak = 0

    async def slow(request, n):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return _stream()

    _serve(monkeypatch, slow)
    provider = _provider()
    await asyncio.gather(*(provider.generate("s", "u", max_tokens=10) for _ in range(5)))

    assert peak == expected


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(httpx.ReadTimeout("timed out"), id="read_timeout"),
        pytest.param(httpx.ConnectError("refused"), id="connect_error"),
    ],
)
async def test_transport_errors_are_retried(monkeypatch, exc):
    def handler(request, n):
        raise exc

    seen = _serve(monkeypatch, handler)
    with pytest.raises(ProviderError, match="ollama"):
        await _provider().generate("s", "u", max_tokens=10)
    assert len(seen) == 3


async def test_server_error_is_retried(monkeypatch):
    seen = _serve(monkeypatch, lambda request, n: httpx.Response(500, text="boom"))
    with pytest.raises(ProviderError):
        await _provider().generate("s", "u", max_tokens=10)
    assert len(seen) == 3


async def test_succeeds_after_a_timeout(monkeypatch):
    def handler(request, n):
        if n == 1:
            raise httpx.ReadTimeout("first attempt timed out")
        return _stream()

    seen = _serve(monkeypatch, handler)
    result = await _provider().generate("s", "u", max_tokens=10)
    assert len(seen) == 2
    assert result.content == "Generated wiki content"


@pytest.mark.parametrize("status", [400, 404])
async def test_client_errors_fail_fast(monkeypatch, status):
    seen = _serve(monkeypatch, lambda request, n: httpx.Response(status, text="model not found"))
    with pytest.raises(ProviderError) as exc_info:
        await _provider().generate("s", "u", max_tokens=10)
    assert len(seen) == 1
    assert exc_info.value.status_code == status


async def test_error_mid_stream_is_a_provider_error(monkeypatch):
    body = json.dumps({"error": "model runner has unexpectedly stopped"})
    _serve(monkeypatch, lambda request, n: httpx.Response(200, text=body))
    with pytest.raises(ProviderError, match="unexpectedly stopped"):
        await _provider().generate("s", "u", max_tokens=10)
