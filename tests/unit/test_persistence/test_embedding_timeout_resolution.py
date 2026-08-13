"""One policy for the embedding timeout, across every embedder.

`REPOWISE_EMBEDDING_TIMEOUT` is documented as applying to whichever embedder is
active, so the contract belongs here rather than in one provider's file: the
same precedence, the same rejection of values that would hang or expire
instantly, and the same refusal to turn a typo into a broken index.
"""

from __future__ import annotations

import pytest

from repowise.core.providers.embedding.base import resolve_embedding_timeout
from repowise.core.providers.embedding.ollama import OllamaEmbedder

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.embedding.openai import OpenAIEmbedder


def _clear(monkeypatch):
    for var in (
        "REPOWISE_EMBEDDING_TIMEOUT",
        "OPENAI_EMBEDDING_TIMEOUT",
        "OLLAMA_EMBEDDING_TIMEOUT",
        "GEMINI_EMBEDDING_TIMEOUT",
        "OPENROUTER_EMBEDDING_TIMEOUT",
    ):
        monkeypatch.delenv(var, raising=False)


def test_shared_variable_applies(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "180")
    assert resolve_embedding_timeout(None, 10.0, provider_env="OPENAI_EMBEDDING_TIMEOUT") == 180.0


def test_provider_variable_wins(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "180")
    monkeypatch.setenv("OPENAI_EMBEDDING_TIMEOUT", "45")
    assert resolve_embedding_timeout(None, 10.0, provider_env="OPENAI_EMBEDDING_TIMEOUT") == 45.0


def test_a_malformed_provider_variable_reports_the_shared_value_that_won(monkeypatch, capsys):
    # A value that did not parse is not a value, so it cannot outrank a good one.
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "180")
    monkeypatch.setenv("OPENAI_EMBEDDING_TIMEOUT", "45s")
    assert resolve_embedding_timeout(None, 10.0, provider_env="OPENAI_EMBEDDING_TIMEOUT") == 180.0
    stderr = capsys.readouterr().err
    assert "OPENAI_EMBEDDING_TIMEOUT='45s'" in stderr
    assert "using 180.0s" in stderr


@pytest.mark.parametrize("bad", ["abc", "30s", "0", "-5", "inf", "nan", "1e400"])
def test_a_bad_value_keeps_the_default_and_reaches_stderr(monkeypatch, capsys, bad):
    # Falling back must not be quiet: the CLI filters structlog to ERROR without
    # -v, so a log line alone would leave the user with an unexplained
    # "N/N items failed to embed" — the symptom this variable exists to cure.
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", bad)
    assert resolve_embedding_timeout(None, 10.0) == 10.0
    assert "not a positive number of seconds" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["abc", "30s", "inf", "-5"])
def test_no_embedder_is_destroyed_by_a_typo(monkeypatch, bad):
    # build_embedder turns a construction error into a keyless 8-wide store, so
    # raising anywhere here would silently end semantic search for a run that
    # worked before the variable was read at all.
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", bad)
    assert OpenAIEmbedder(api_key="k", model="local")._timeout == 10.0
    assert OllamaEmbedder(model="embeddinggemma")._timeout == 30.0


def test_each_embedder_keeps_its_own_default(monkeypatch):
    _clear(monkeypatch)
    assert OpenAIEmbedder(api_key="k")._timeout == 10.0
    assert OllamaEmbedder()._timeout == 30.0


def test_ollama_honours_the_shared_variable(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "600")
    assert OllamaEmbedder()._timeout == 600.0
