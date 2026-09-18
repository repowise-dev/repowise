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


async def test_run_pipeline_language_from_repowise_language_env(
    tmp_path, monkeypatch
):
    """Issue #1756: REPOWISE_LANGUAGE pins the generation language when the
    repo config sets none — the Docker/UI deploy path without a CLI flag."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "main.py").write_text(
        'def hello() -> str:\n    return "hello"\n',
        encoding="utf-8",
    )
    (repo_path / ".repowise").mkdir()
    (repo_path / ".repowise" / "config.yaml").write_text("", encoding="utf-8")

    monkeypatch.setenv("REPOWISE_LANGUAGE", "pt")

    provider = MockProvider()
    result = await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=provider,
        concurrency=1,
        test_run=True,
    )

    assert result.generated_pages
    # The language flows into the generation config; exercise the resolved
    # generation pipeline did not fall back to English default.
    from repowise.core.pipeline.orchestrator import _resolve_language

    assert _resolve_language(repo_path) == "pt"
