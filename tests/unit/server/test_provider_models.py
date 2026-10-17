"""GET /api/providers/{id}/models: live lists, fallback, cache, redaction."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from httpx import AsyncClient

from repowise.core.providers.llm.base import ProviderError, ProviderModelOption
from repowise.core.providers.llm.openai import LOCAL_SERVER_API_KEY, OpenAIProvider
from repowise.server import provider_models
from repowise.server.provider_models import list_provider_models, sort_models


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    provider_models.clear_model_cache()
    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "REPOWISE_PROVIDER", "REPOWISE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield
    provider_models.clear_model_cache()


class _Fake:
    """Stands in for a provider; records the kwargs it was built with."""

    def __init__(self, options=(), error: Exception | None = None, gate=None):
        self.options = options
        self.error = error
        self.gate = gate
        self.built: list[dict] = []
        self.calls = 0

    def factory(self, name, with_rate_limiter=True, **kwargs):
        self.built.append(kwargs)
        return self

    def discover_model_options(self):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return self.options


def _api(*ids):
    return tuple(ProviderModelOption(model=i, label=i, source="api") for i in ids)


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        stub = _Fake(**kw)
        monkeypatch.setattr(provider_models, "get_provider", stub.factory)
        return stub

    return install


@pytest.mark.asyncio
async def test_live_models_put_the_default_first_then_newest(client: AsyncClient, fake, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    fake(options=_api("gpt-5.4", "gpt-6", "gpt-5.6-luna", "gpt-6-mini"))

    resp = await client.get("/api/providers/openai/models")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "live"
    assert body["error"] is None
    assert body["default_model"] == "gpt-5.6-luna"
    assert [m["id"] for m in body["models"]] == ["gpt-5.6-luna", "gpt-6", "gpt-6-mini", "gpt-5.4"]


@pytest.mark.asyncio
async def test_unreachable_local_server_falls_back_with_a_plain_reason(
    client: AsyncClient, fake
):
    url = "http://localhost:1234/v1"
    fake(error=httpx.ConnectError("refused"))

    body = (await client.get(f"/api/providers/openai/models?base_url={url}")).json()

    assert body["source"] == "catalog"
    assert "gpt-5.6-luna" in [m["id"] for m in body["models"]]
    assert body["error"] == f"Could not reach {url}. Is LM Studio's server running?"


@pytest.mark.asyncio
async def test_a_probe_never_sends_the_stored_key_elsewhere(client: AsyncClient, fake, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stored-1234567890")
    stub = fake(options=_api("qwen3-coder"))

    await client.get("/api/providers/openai/models?base_url=https://example.com/v1")

    assert stub.built[-1]["api_key"] == LOCAL_SERVER_API_KEY
    assert stub.built[-1]["base_url"] == "https://example.com/v1"


@pytest.mark.asyncio
async def test_errors_never_carry_the_key(client: AsyncClient, fake, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "plainsecretvalue")
    fake(error=RuntimeError("bad key plainsecretvalue and sk-other-abcdefgh"))

    body = (await client.get("/api/providers/openai/models")).json()

    assert "plainsecretvalue" not in body["error"]
    assert "sk-other-abcdefgh" not in body["error"]


@pytest.mark.asyncio
async def test_no_key_says_to_add_one(client: AsyncClient):
    body = (await client.get("/api/providers/anthropic/models")).json()

    assert body["source"] == "catalog"
    assert body["error"] == "Add an API key for Anthropic to list its models."


@pytest.mark.asyncio
async def test_lists_are_cached_until_refresh(client: AsyncClient, fake, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    stub = fake(options=_api("gpt-6"))

    await client.get("/api/providers/openai/models")
    await client.get("/api/providers/openai/models")
    assert stub.calls == 1

    await client.get("/api/providers/openai/models?refresh=true")
    assert stub.calls == 2


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_fetch(fake, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    gate = threading.Event()
    stub = fake(options=_api("gpt-6"), gate=gate)

    tasks = [asyncio.ensure_future(list_provider_models("openai")) for _ in range(5)]
    await asyncio.sleep(0.05)
    gate.set()
    results = await asyncio.gather(*tasks)

    assert stub.calls == 1
    assert all(r["source"] == "live" for r in results)


@pytest.mark.asyncio
async def test_bad_base_url_and_unknown_provider_are_rejected(client: AsyncClient):
    assert (await client.get("/api/providers/openai/models?base_url=ftp://x")).status_code == 400
    assert (await client.get("/api/providers/nope/models")).status_code == 404


def test_sort_models_dedupes_and_caps():
    ids = ["b-1", "a-2", "a-2", "x", "custom"]
    assert sort_models(ids, "custom") == ["custom", "a-2", "b-1", "x"]
    assert len(sort_models([f"m-{i}" for i in range(500)], None)) == 200


def test_openai_needs_no_key_for_a_local_server(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    local = OpenAIProvider(model="m", base_url="http://127.0.0.1:1234/v1")
    assert local._api_key == LOCAL_SERVER_API_KEY
    with pytest.raises(ProviderError):
        OpenAIProvider(model="m", base_url="https://api.example.com/v1")


@pytest.mark.asyncio
async def test_openai_on_a_local_server_counts_as_configured(client: AsyncClient):
    resp = await client.patch(
        "/api/providers/active",
        json={"provider": "openai", "model": "qwen3", "base_url": "http://localhost:1234/v1"},
    )
    assert resp.status_code == 200
    openai = next(p for p in resp.json()["providers"] if p["id"] == "openai")
    assert openai["configured"] is True
    assert openai["accepts_base_url"] is True


@pytest.mark.asyncio
async def test_an_unconfirmed_repo_url_is_flagged_until_saved_from_settings(
    client: AsyncClient, monkeypatch
):
    from pathlib import Path

    from repowise.core.repo_config import save_repo_config
    from tests.unit.server.conftest import create_test_repo

    monkeypatch.setenv("OPENAI_API_KEY", "sk-stored-1234567890")
    repo = await create_test_repo(client)
    url = "https://gateway.example.com/v1"
    save_repo_config(
        Path(repo["local_path"]), {"provider": "openai", "openai": {"base_url": url}}
    )

    status = (await client.get(f"/api/providers?repo_id={repo['id']}")).json()
    assert status["active"]["base_url_unconfirmed"] is True
    models = (await client.get(f"/api/providers/openai/models?repo_id={repo['id']}")).json()
    assert models["source"] == "catalog"
    assert url in models["error"]
    assert "sk-stored" not in models["error"]

    resp = await client.patch(
        "/api/providers/active",
        json={"provider": "openai", "base_url": url, "repo_id": repo["id"]},
    )
    assert resp.json()["active"]["base_url_unconfirmed"] is False


@pytest.mark.asyncio
async def test_a_recognised_provider_failure_uses_the_shared_hint(
    client: AsyncClient, fake, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    fake(error=ProviderError("openai", "bad key sk-test-1234567890", status_code=401))

    body = (await client.get("/api/providers/openai/models")).json()

    assert body["error"] == "API key rejected by openai. Update it in Settings."


@pytest.mark.asyncio
async def test_url_credentials_from_env_never_reach_the_error(
    client: AsyncClient, fake, monkeypatch
):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://bob:hunter2@localhost:1234/v1")
    fake(error=httpx.ConnectError("refused at http://bob:hunter2@localhost:1234/v1/models"))

    body = (await client.get("/api/providers/openai/models")).json()

    assert "hunter2" not in body["error"]
    assert "bob" not in body["error"]
    assert "http://localhost:1234/v1" in body["error"]


@pytest.mark.asyncio
async def test_a_trailing_slash_does_not_make_the_saved_url_a_probe(
    client: AsyncClient, fake, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stored-1234567890")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1/")
    stub = fake(options=_api("gpt-6"))

    await client.get("/api/providers/openai/models?base_url=https://gateway.example.com/v1")

    assert stub.built[-1]["api_key"] == "sk-stored-1234567890"
