"""Reasoning propagation tests for programmatic generation paths."""

from __future__ import annotations

from repowise.core.pipeline import run_pipeline
from repowise.core.providers.llm.mock import MockProvider


async def test_run_pipeline_loads_repo_reasoning_config(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "main.py").write_text(
        'def hello() -> str:\n    return "hello"\n',
        encoding="utf-8",
    )
    (repo_path / ".repowise").mkdir()
    (repo_path / ".repowise" / "config.yaml").write_text(
        "reasoning: off  # disable thinking for Qwen3-style models\n",
        encoding="utf-8",
    )

    provider = MockProvider()
    result = await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=provider,
        concurrency=1,
        test_run=True,
    )

    assert result.generated_pages
    assert provider.calls
    assert all(call["reasoning"] == "off" for call in provider.calls)


async def test_run_pipeline_language_from_repowise_language_env(tmp_path, monkeypatch):
    """Issue #1756: REPOWISE_LANGUAGE pins the generation language when the
    repo config sets none, which is the Docker/UI deploy path with no CLI flag."""
    from repowise.core.generation import GenerationConfig

    seen_language: list[object] = []
    real_from_repo_config = GenerationConfig.from_repo_config

    def spy_from_repo_config(cls, config, **overrides):
        seen_language.append(overrides.get("language"))
        return real_from_repo_config(config, **overrides)

    monkeypatch.setattr(GenerationConfig, "from_repo_config", classmethod(spy_from_repo_config))
    monkeypatch.setenv("REPOWISE_LANGUAGE", "pt")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "main.py").write_text(
        'def hello() -> str:\n    return "hello"\n',
        encoding="utf-8",
    )
    (repo_path / ".repowise").mkdir()
    (repo_path / ".repowise" / "config.yaml").write_text("", encoding="utf-8")

    provider = MockProvider()
    result = await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=provider,
        concurrency=1,
        test_run=True,
    )

    assert result.generated_pages
    # Assert on what the generation config actually received. Asserting on
    # ``resolve_language(repo_path)`` would test the helper twice and the wiring
    # not at all: run_pipeline would still pass if the ``language=`` argument
    # above it were reverted to the English default.
    assert seen_language, "GenerationConfig.from_repo_config was never called"
    assert seen_language[0] == "pt"


async def test_repo_config_language_beats_repowise_language_env(tmp_path, monkeypatch):
    """The precedence the maintainer asked for: a repo that has committed to a
    language cannot be flipped by an ambient variable in someone's shell."""
    from repowise.core.repo_config import resolve_language

    repo_path = tmp_path / "repo"
    (repo_path / ".repowise").mkdir(parents=True)
    (repo_path / ".repowise" / "config.yaml").write_text("language: de\n", encoding="utf-8")
    monkeypatch.setenv("REPOWISE_LANGUAGE", "pt")

    assert resolve_language(repo_path) == "de"
    # The variable fills the gap where nothing was chosen.
    (repo_path / ".repowise" / "config.yaml").write_text("", encoding="utf-8")
    assert resolve_language(repo_path) == "pt"
    monkeypatch.delenv("REPOWISE_LANGUAGE")
    assert resolve_language(repo_path) == "en"
