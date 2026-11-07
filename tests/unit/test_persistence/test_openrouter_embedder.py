"""Unit tests for OpenRouterEmbedder.

All tests mock openai.OpenAI and the OpenRouter model catalog — no real API
calls are made. The catalog fetch (``fetch_model_list``) is patched to
``None`` by default, so constructing an unknown model never touches the
network unless a test says otherwise.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

import repowise.core.providers.embedding.openrouter as openrouter_module
from repowise.core.providers.embedding.openrouter import (
    OpenRouterEmbedder,
    _is_embedding_capable,
    clear_embedding_model_cache,
)


@pytest.fixture(autouse=True)
def _no_catalog(monkeypatch):
    """No live catalog in any test: discovery sees an unreachable catalog."""
    clear_embedding_model_cache()
    monkeypatch.setattr(openrouter_module, "fetch_model_list", lambda *a, **k: None)
    yield
    clear_embedding_model_cache()


def _catalog_entry(model_id: str, **overrides):
    entry = {"id": model_id, "name": model_id}
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OpenRouter API key required"):
        OpenRouterEmbedder(api_key=None)


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    emb = OpenRouterEmbedder()
    assert emb._api_key == "sk-or-test"


def test_default_model():
    emb = OpenRouterEmbedder(api_key="k")
    assert emb._model == "google/gemini-embedding-001"


def test_dimensions_gemini():
    emb = OpenRouterEmbedder(api_key="k", model="google/gemini-embedding-001")
    assert emb.dimensions == 768


def test_dimensions_openai_small():
    emb = OpenRouterEmbedder(api_key="k", model="openai/text-embedding-3-small")
    assert emb.dimensions == 1536


def test_dimensions_openai_large():
    emb = OpenRouterEmbedder(api_key="k", model="openai/text-embedding-3-large")
    assert emb.dimensions == 3072


def test_malformed_env_raises_the_same_message(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "abc")
    with pytest.raises(ValueError, match="dimensions must be a positive integer"):
        OpenRouterEmbedder(api_key="k")


def test_unknown_model_accepted_when_catalog_unavailable():
    """Minimum fallback: no hardcoded rejection when the catalog is down.

    The width stays unknown (None) until the first embed returns; the API
    itself is the final authority on whether the model exists.
    """
    emb = OpenRouterEmbedder(api_key="k", model="some/future-model")
    assert emb._model == "some/future-model"
    assert emb.dimensions is None


def test_unknown_model_rejected_when_discovery_disabled(monkeypatch):
    """Offline escape hatch: with discovery off, an unknown model needs a width."""
    monkeypatch.setenv("REPOWISE_OPENROUTER_MODEL_DISCOVERY", "0")
    with pytest.raises(ValueError, match="REPOWISE_EMBEDDING_DIMS"):
        OpenRouterEmbedder(api_key="k", model="some/future-model")


def test_unknown_model_with_explicit_dimensions_and_discovery_disabled(monkeypatch):
    monkeypatch.setenv("REPOWISE_OPENROUTER_MODEL_DISCOVERY", "off")
    emb = OpenRouterEmbedder(api_key="k", model="some/future-model", dimensions=1024)
    assert emb.dimensions == 1024


def test_unknown_model_accepted_when_catalog_lists_it_as_embedding_capable():
    catalog = [
        _catalog_entry(
            "mongodb/voyage-code-4",
            architecture={"modality": "text->text-embedding"},
        ),
    ]
    with patch.object(openrouter_module, "fetch_model_list", return_value=catalog):
        emb = OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
    assert emb._model == "mongodb/voyage-code-4"
    # No width configured: the model default applies, width learned on embed.
    assert emb.dimensions is None


def test_unknown_model_rejected_when_catalog_says_not_embedding_capable():
    catalog = [
        _catalog_entry(
            "acme/chat-1",
            architecture={"modality": "text->text"},
            supported_parameters=["temperature", "tools"],
        ),
    ]
    with (
        patch.object(openrouter_module, "fetch_model_list", return_value=catalog),
        pytest.raises(ValueError, match="not an embedding model"),
    ):
        OpenRouterEmbedder(api_key="k", model="acme/chat-1")


def test_model_absent_from_catalog_accepted_opaquely(capsys):
    """A catalog that simply does not list the model yet must not block it.

    The catalog may be stale; the API rejects a truly invalid id at embed time.
    """
    catalog = [
        _catalog_entry(
            "google/gemini-embedding-001",
            architecture={"modality": "text->text-embedding"},
        ),
    ]
    with patch.object(openrouter_module, "fetch_model_list", return_value=catalog):
        emb = OpenRouterEmbedder(api_key="k", model="brand/new-embed-model")
    assert emb.dimensions is None
    assert "not in the OpenRouter catalog" in capsys.readouterr().err


def test_base_url_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.example.com/api/v1")
    emb = OpenRouterEmbedder(api_key="k")
    assert emb._base_url == "https://proxy.example.com/api/v1"


# ---------------------------------------------------------------------------
# Catalog capability detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"id": "x/y", "architecture": {"modality": "text->text-embedding"}}, True),
        ({"id": "x/y", "architecture": {"output_modalities": ["text-embedding"]}}, True),
        ({"id": "x/y", "supported_parameters": ["dimensions"]}, True),
        ({"id": "vendor/some-embed-model"}, True),  # id heuristic, no metadata
        ({"id": "acme/chat-1", "architecture": {"modality": "text->text"}}, False),
        ({"id": "acme/chat-1", "supported_parameters": ["temperature"]}, False),
        ({"id": "acme/chat-1"}, False),
    ],
)
def test_is_embedding_capable(entry, expected):
    assert _is_embedding_capable(entry) is expected


# ---------------------------------------------------------------------------
# Catalog caching
# ---------------------------------------------------------------------------


def test_catalog_fetched_once_per_ttl():
    catalog = [
        _catalog_entry(
            "mongodb/voyage-code-4",
            architecture={"modality": "text->text-embedding"},
        ),
    ]
    calls: list = []

    def fake_fetch(*args, **kwargs):
        calls.append(args)
        return catalog

    with patch.object(openrouter_module, "fetch_model_list", side_effect=fake_fetch):
        OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
        OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
    assert len(calls) == 1  # second construction served from cache


def test_catalog_refetched_after_ttl(monkeypatch):
    catalog = [
        _catalog_entry(
            "mongodb/voyage-code-4",
            architecture={"modality": "text->text-embedding"},
        ),
    ]
    calls: list = []

    def fake_fetch(*args, **kwargs):
        calls.append(args)
        return catalog

    with patch.object(openrouter_module, "fetch_model_list", side_effect=fake_fetch):
        OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
        # Age the cached entry past the TTL, then construct again.
        key = "https://openrouter.ai/api/v1"
        fetched_at, payload = openrouter_module._MODEL_CACHE[key]
        openrouter_module._MODEL_CACHE[key] = (
            fetched_at - openrouter_module._MODEL_CACHE_TTL_S - 1,
            payload,
        )
        OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
    assert len(calls) == 2


def test_stale_catalog_served_when_refresh_fails():
    catalog = [
        _catalog_entry(
            "mongodb/voyage-code-4",
            architecture={"modality": "text->text-embedding"},
        ),
    ]
    with patch.object(openrouter_module, "fetch_model_list", return_value=catalog):
        OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
    # Expire the entry, then make the refresh fail: the stale set still answers.
    key = "https://openrouter.ai/api/v1"
    fetched_at, payload = openrouter_module._MODEL_CACHE[key]
    openrouter_module._MODEL_CACHE[key] = (
        fetched_at - openrouter_module._MODEL_CACHE_TTL_S - 1,
        payload,
    )
    with patch.object(openrouter_module, "fetch_model_list", return_value=None):
        emb = OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
    assert emb.dimensions is None


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _make_mock_embedding(values: list[float]) -> MagicMock:
    item = MagicMock()
    item.embedding = values
    return item


def _make_mock_response(vectors: list[list[float]]) -> MagicMock:
    response = MagicMock()
    response.data = [_make_mock_embedding(v) for v in vectors]
    return response


async def test_embed_empty_returns_empty():
    emb = OpenRouterEmbedder(api_key="k")
    result = await emb.embed([])
    assert result == []


async def test_embed_returns_normalized_vectors():
    # dimensions=3 so the declared width matches the 3-wide mock — original vector preserved.
    raw = [1.0, 0.0, 0.0]
    emb = OpenRouterEmbedder(api_key="k", dimensions=3)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([raw])
        result = await emb.embed(["hello"])

    assert len(result) == 1
    norm = math.sqrt(sum(x * x for x in result[0]))
    assert abs(norm - 1.0) < 1e-6


async def test_embed_batch_returns_correct_count():
    # dimensions=2 so the declared width matches the 2-wide mock — original vectors preserved.
    texts = ["a", "b", "c"]
    raw_vecs = [[1.0, 0.0], [0.0, 1.0], [0.707, 0.707]]
    emb = OpenRouterEmbedder(api_key="k", dimensions=2)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(raw_vecs)
        result = await emb.embed(texts)

    assert len(result) == 3


async def test_embed_passes_model_and_input():
    # dimensions=2 so the 2-wide mock response passes the width guard — original intent preserved.
    emb = OpenRouterEmbedder(api_key="k", model="openai/text-embedding-3-large", dimensions=2)
    captured: list = []

    def fake_create(**kwargs):
        captured.append({"model": kwargs["model"], "input": kwargs["input"]})
        return _make_mock_response([[1.0, 0.0]])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test text"])

    assert captured[0]["model"] == "openai/text-embedding-3-large"
    assert captured[0]["input"] == ["test text"]


async def test_embed_uses_openrouter_base_url():
    """Verify the client is created with the OpenRouter base URL."""
    # dimensions=1 so the 1-wide mock response passes the width guard — original mock preserved.
    emb = OpenRouterEmbedder(api_key="sk-or-test", dimensions=1)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([[1.0]])
        await emb.embed(["test"])

    call_kwargs = mock_client.call_args
    assert call_kwargs.kwargs.get("base_url") == "https://openrouter.ai/api/v1"


async def test_embed_sends_selected_dimensions():
    """REPOWISE_EMBEDDING_DIMS / dimensions= is sent as the API parameter."""
    emb = OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4", dimensions=1024)
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _make_mock_response([[1.0] + [0.0] * 1023])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test text"])

    assert captured["dimensions"] == 1024
    assert captured["model"] == "mongodb/voyage-code-4"


@pytest.mark.parametrize("width", [1024, 2048])
async def test_embed_voyage_code_4_matryoshka_widths(width):
    """The issue's model: 1024 (default) and 2048 both round-trip."""
    emb = OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4", dimensions=width)
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _make_mock_response([[1.0] + [0.0] * (width - 1)])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        result = await emb.embed(["def f(): pass"])

    assert captured["dimensions"] == width
    assert len(result[0]) == width


async def test_embed_sends_env_dimensions(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "512")
    emb = OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4")
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _make_mock_response([[1.0] + [0.0] * 511])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test"])

    assert captured["dimensions"] == 512


async def test_embed_sends_no_dimensions_when_none_configured():
    """No width configured: the model default applies, no parameter sent."""
    emb = OpenRouterEmbedder(api_key="k", model="google/gemini-embedding-001")
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _make_mock_response([[1.0] + [0.0] * 767])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test"])

    assert "dimensions" not in captured


async def test_embed_declared_dimensions_never_sent():
    """Declaration-only path: width declared for the store, never sent."""
    emb = OpenRouterEmbedder(api_key="k", declared_dimensions=768)
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _make_mock_response([[1.0] + [0.0] * 767])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test"])

    assert "dimensions" not in captured
    assert emb.dimensions == 768


async def test_embed_learns_width_from_response_when_unknown():
    """Returned vector length is authoritative when no width was configured."""
    emb = OpenRouterEmbedder(api_key="k", model="some/future-model")
    assert emb.dimensions is None

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(
            [[1.0, 0.0, 0.0, 0.0, 0.0]]
        )
        result = await emb.embed(["hello"])

    assert len(result[0]) == 5
    assert emb.dimensions == 5  # learned: later calls validate against it


async def test_embed_raises_on_mixed_width_response_when_unknown():
    emb = OpenRouterEmbedder(api_key="k", model="some/future-model")

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(
            [[1.0, 0.0], [1.0, 0.0, 0.0]]
        )
        with pytest.raises(ValueError, match="mixed-width"):
            await emb.embed(["a", "b"])


# ---------------------------------------------------------------------------
# Width verification — the check added in fix/embedder-width-verification
# ---------------------------------------------------------------------------


async def test_embed_raises_when_api_returns_wrong_width():
    # google/gemini-embedding-001 is declared as 768 in _DIMS.
    # Fake API returns 3-wide vectors (wrong width).
    # Error message should name both numbers and mention _DIMS.
    emb = OpenRouterEmbedder(api_key="k", model="google/gemini-embedding-001")
    assert emb.dimensions == 768

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(
            [[1.0, 0.0, 0.0]]
        )
        with pytest.raises(ValueError, match="768") as exc_info:
            await emb.embed(["hello"])

    msg = str(exc_info.value)
    assert "3" in msg  # actual width named
    assert "_DIMS" in msg  # points at the table entry


async def test_embed_raises_when_endpoint_ignores_requested_dimensions():
    """Requested width sent but the server returned its native width."""
    emb = OpenRouterEmbedder(api_key="k", model="mongodb/voyage-code-4", dimensions=2048)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(
            [[1.0] + [0.0] * 1023]
        )
        with pytest.raises(ValueError, match="REPOWISE_EMBEDDING_DIMS") as exc_info:
            await emb.embed(["hello"])

    assert "1024" in str(exc_info.value)  # actual width named


async def test_embed_raises_for_declared_only_mismatch():
    emb = OpenRouterEmbedder(api_key="k", declared_dimensions=512)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(
            [[1.0] + [0.0] * 767]
        )
        with pytest.raises(ValueError, match="REPOWISE_EMBEDDING_DECLARED_DIMS"):
            await emb.embed(["hello"])


async def test_embed_width_check_not_triggered_on_empty():
    emb = OpenRouterEmbedder(api_key="k")
    assert await emb.embed([]) == []
