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

    # Spy on the language the generator is constructed with. The phase imports
    # ``PageGenerator`` inside the function body from ``repowise.core.generation``,
    # so the patch goes on that module: the real pipeline still runs and only the
    # constructor argument is recorded.
    seen_language: list[str] = []
    import repowise.core.generation as gen_pkg

    RealPageGenerator = gen_pkg.PageGenerator

    class _SpyPageGenerator(RealPageGenerator):
        def __init__(self, *args, **kwargs):
            seen_language.append(kwargs.get("language"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(gen_pkg, "PageGenerator", _SpyPageGenerator)

    provider = MockProvider()
    result = await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=provider,
        concurrency=1,
        test_run=True,
    )

    assert result.generated_pages
    # Assert on what the generator actually received. Asserting on
    # ``resolve_language(repo_path)`` would test the helper twice and the wiring
    # not at all: run_pipeline would still pass if the ``language=`` argument
    # above it were reverted to the English default.
    assert seen_language, "PageGenerator was never constructed"
    assert seen_language[0] == "pt"
