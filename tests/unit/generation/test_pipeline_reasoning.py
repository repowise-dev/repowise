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
