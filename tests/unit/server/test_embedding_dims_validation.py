"""``REPOWISE_EMBEDDING_DIMS`` must not reach any embedder constructor unvalidated.

``_server.py:171`` passed ``int(dims) if dims else 768`` straight through, so
``REPOWISE_EMBEDDING_DIMS=-5`` gave the gemini embedder a negative width with
no warning. The same hole existed in ``app.py`` for the non-MCP code path.

These tests verify that non-positive, non-numeric, and otherwise malformed
values fall back to 768 and reach stderr, matching the pattern
``resolve_embedding_timeout`` already follows for timeout env vars.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._server import _embedder_kwargs


def _clear_dims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOWISE_EMBEDDING_DIMS", raising=False)
    monkeypatch.delenv("REPOWISE_EMBEDDING_MODEL", raising=False)


def test_valid_dims_are_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_dims(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "256")
    kwargs = _embedder_kwargs("gemini")
    assert kwargs["output_dimensionality"] == 256


def test_missing_dims_default_to_768(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_dims(monkeypatch)
    kwargs = _embedder_kwargs("gemini")
    assert kwargs["output_dimensionality"] == 768


@pytest.mark.parametrize("bad", ["-5", "0", "-1", "abc", "3.14", "inf", "nan", ""])
def test_bad_dims_fall_back_to_768(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, bad: str
) -> None:
    _clear_dims(monkeypatch)
    if bad:
        monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", bad)
    kwargs = _embedder_kwargs("gemini")
    assert kwargs["output_dimensionality"] == 768
    if bad:
        assert "not a positive integer" in capsys.readouterr().err


def test_non_gemini_does_not_read_dims(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_dims(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "256")
    kwargs = _embedder_kwargs("openai")
    assert "output_dimensionality" not in kwargs


# -- Direct constructor guards (OllamaEmbedder) --


def test_ollama_rejects_negative_dimensions() -> None:
    from repowise.core.providers.embedding.ollama import OllamaEmbedder

    with pytest.raises(ValueError, match="positive"):
        OllamaEmbedder(dimensions=-5)


def test_ollama_rejects_zero_dimensions() -> None:
    from repowise.core.providers.embedding.ollama import OllamaEmbedder

    with pytest.raises(ValueError, match="positive"):
        OllamaEmbedder(dimensions=0)


def test_ollama_bad_env_dims_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.providers.embedding.ollama import OllamaEmbedder

    _clear_dims(monkeypatch)
    monkeypatch.delenv("OLLAMA_EMBEDDING_DIMS", raising=False)
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "-5")
    embedder = OllamaEmbedder()
    # Should not crash; dimensions fall back to model inference
    assert embedder.dimensions > 0
