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
from repowise.cli.helpers import load_config
from repowise.core.generation.selection import SelectionInputs, select_pages
from tests.unit.generation.test_selection_contract import _build_synthetic_repo


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


def test_mock_embedder_still_builds_vector_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the mock embedder, an index-only refresh still builds the store.

    Before the fix, the `!= "mock"` gate in `_render_pages` skipped the
    store entirely, so page vectors were never maintained and `doctor`
    reported permanent SQL/vector-store drift (#5420).
    """
    calls: dict[str, object] = {}
    embedder = object()

    def _fake_build_embedder(name, _repo_path):
        calls["embedder"] = name
        return embedder

    def _fake_build_store(repo_path, embedder):
        calls["store"] = (repo_path, embedder)
        return object()

    import repowise.cli.providers as providers
    import repowise.core.generation as generation

    class _Config:
        language = "en"

        @classmethod
        def from_repo_config(cls, _cfg, **kwargs):
            return cls()

    class _Generator:
        def __init__(self, provider, _assembler, _config, **_kwargs):
            pass

        async def generate_all(self, parsed, source, *_args, only_page_ids=None, **_kwargs):
            return ["scc"]

    monkeypatch.setattr(generation, "GenerationConfig", _Config)
    monkeypatch.setattr(generation, "ContextAssembler", lambda *_a, **_k: object())
    monkeypatch.setattr(generation, "PageGenerator", _Generator)
    monkeypatch.setattr(providers, "build_embedder", _fake_build_embedder)
    monkeypatch.setattr(providers, "build_vector_store", _fake_build_store)
    monkeypatch.setattr(deterministic, "deterministic_embedder_name", lambda _cfg: "mock")

    result = regenerate_deterministic_page_ids(
        repo_path=tmp_path,
        parsed_files=[object()],
        source_map={},
        graph_builder=object(),
        repo_structure=object(),
        git_meta_map={},
        page_ids={"scc_page:scc-current"},
        cfg={},
        concurrency=2,
        degraded=[],
    )

    assert result == ["scc"]
    assert calls["embedder"] == "mock"
    assert calls["store"] == (tmp_path, embedder)


def test_mock_would_clobber_only_refuses_real_width_store(tmp_path) -> None:
    """The guard the fix relies on: a mock write is refused only when the
    existing store holds a real embedder's width."""
    from repowise.cli.providers.vector_store import _mock_would_clobber
    from repowise.core.providers.embedding.base import MockEmbedder

    lance_dir = tmp_path / ".repowise" / "lancedb"
    lance_dir.mkdir(parents=True)
    (lance_dir / "wiki_pages.lance").mkdir()
    record = lance_dir / "wiki_pages.vector_dim"

    record.write_text("1536", encoding="ascii")
    assert _mock_would_clobber(lance_dir, MockEmbedder()) is True

    record.write_text("8", encoding="ascii")
    assert _mock_would_clobber(lance_dir, MockEmbedder()) is False

    record.unlink()
    assert _mock_would_clobber(lance_dir, MockEmbedder()) is False

    record.write_text("1536", encoding="ascii")
    assert _mock_would_clobber(lance_dir, object()) is False


@pytest.mark.parametrize("choice, expected", [(None, 4500), (0, 4501), (25, 25)])
def test_deterministic_refresh_preserves_saved_file_page_choice(
    tmp_path, monkeypatch: pytest.MonkeyPatch, choice: int | None, expected: int
) -> None:
    parsed, pagerank, betweenness, community = _build_synthetic_repo(4501)
    state = tmp_path / ".repowise"
    state.mkdir()
    config_path = state / "config.yaml"
    config_path.write_text(
        "embedder: mock\n" + ("" if choice is None else f"max_file_pages: {choice}\n")
    )
    original = config_path.read_bytes()

    class SelectionGenerator:
        def __init__(self, _provider, _assembler, config, **_kwargs):
            self.config = config

        async def generate_all(self, parsed_files, _source, *_args, **_kwargs):
            return select_pages(
                SelectionInputs(
                    parsed_files=parsed_files,
                    pagerank=pagerank,
                    betweenness=betweenness,
                    community=community,
                    community_info=None,
                    sccs=[],
                    git_meta_map=None,
                    config=self.config,
                )
            ).file_page_paths

    import repowise.core.generation as generation

    monkeypatch.setattr(generation, "PageGenerator", SelectionGenerator)
    degraded: list[str] = []
    selected = deterministic.regenerate_deterministic_pages(
        repo_path=tmp_path,
        parsed_files=parsed,
        source_map={},
        graph_builder=object(),
        repo_structure=object(),
        git_meta_map={},
        regenerate_paths=[],
        cfg=load_config(tmp_path),
        concurrency=1,
        degraded=degraded,
        full_scope=True,
        embed_during_generation=False,
    )

    assert not degraded
    assert len(selected) == expected
    assert config_path.read_bytes() == original
