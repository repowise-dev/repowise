"""The no-spend rules around a template wiki.

Two promises are under test. A run that says it costs nothing does not put a
hosted embedder on the bill, and it does not silently change which embedder a
repo uses between one run and the next. Both are easy to break by accident,
because the embedder is resolved from whatever API key happens to be in the
environment.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.update_cmd import deterministic
from repowise.cli.commands.update_cmd.deterministic import (
    deterministic_embedder_name,
    regenerate_deterministic_page_ids,
)


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an environment that infers nothing."""
    for var in (
        "REPOWISE_EMBEDDER",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestDeterministicEmbedderName:
    def test_config_choice_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The repo was indexed with this embedder, so its store holds vectors
        # of that width. Re-deciding here would rewrite the store.
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        assert deterministic_embedder_name({"embedder": "openai"}) == "openai"

    def test_ambient_key_alone_does_not_bill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The key is there to pay for a model somewhere else. Nobody asked for
        # 2000 pages of embeddings on a run advertised as free.
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        assert deterministic_embedder_name({}) == "mock"

    def test_explicit_env_embedder_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")
        assert deterministic_embedder_name({}) == "openai"

    def test_ollama_needs_no_permission(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The keyless one: running it costs nothing, so inferring it is fine.
        monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
        assert deterministic_embedder_name({}) == "ollama"

    def test_nothing_configured_is_mock(self) -> None:
        assert deterministic_embedder_name({}) == "mock"


def test_exact_structural_refresh_uses_full_context_without_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class _Config:
        language = "en"

        @classmethod
        def from_repo_config(cls, _cfg, **kwargs):
            captured["config"] = kwargs
            return cls()

    class _Generator:
        def __init__(self, provider, _assembler, _config, **_kwargs):
            captured["provider"] = type(provider).__name__

        async def generate_all(self, parsed, source, *_args, only_page_ids=None, **_kwargs):
            captured["parsed"] = parsed
            captured["source"] = source
            captured["only_page_ids"] = only_page_ids
            return ["scc"]

    import repowise.core.generation as generation

    monkeypatch.setattr(generation, "GenerationConfig", _Config)
    monkeypatch.setattr(generation, "ContextAssembler", lambda *_a, **_k: object())
    monkeypatch.setattr(generation, "PageGenerator", _Generator)
    monkeypatch.setattr(deterministic, "deterministic_embedder_name", lambda _cfg: "mock")

    parsed = [object(), object()]
    source = {"a.py": b"a", "b.py": b"b"}
    result = regenerate_deterministic_page_ids(
        repo_path=tmp_path,
        parsed_files=parsed,
        source_map=source,
        graph_builder=object(),
        repo_structure=object(),
        git_meta_map={},
        page_ids={"scc_page:scc-current"},
        cfg={},
        concurrency=2,
        degraded=[],
    )

    assert result == ["scc"]
    assert captured["provider"] == "TemplateProvider"
    assert captured["config"]["deterministic"] is True
    assert captured["config"]["file_pages_only"] is False
    assert captured["parsed"] == parsed
    assert captured["source"] == source
    assert captured["only_page_ids"] == {"scc_page:scc-current"}
