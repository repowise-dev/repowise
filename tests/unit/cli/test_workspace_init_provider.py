"""A workspace init must hand its provider to the index phase, not just to generation.

The index phase mines decisions with a model — pull requests, git history and
code comments each have a model stage — and it decides whether those stages can
run from the ``llm_client`` it was given. The workspace path resolved a provider
and then called ``run_pipeline`` without it, so a run launched with an explicit
``--provider`` reported "No LLM provider is configured" for every one of those
sources while single-repo init mined them normally.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.cli.commands.init_cmd import workspace as ws_mod
from repowise.cli.commands.init_cmd.workspace import _ingest_and_generate_repo, _WorkspaceCtx


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        file_count=3,
        symbol_count=9,
        generated_pages=[],
        knowledge_graph_result=None,
        repo_name="repo",
    )


def _fake_repo(path: Path) -> SimpleNamespace:
    return SimpleNamespace(alias="repo", path=path)


def _ctx(repo: Path, *, provider: Any, dry_run: bool = False) -> _WorkspaceCtx:
    return _WorkspaceCtx(
        provider=provider,
        ws_config=SimpleNamespace(get_repo=lambda _alias: SimpleNamespace()),
        editor_options=SimpleNamespace(),
        index_only=False,
        dry_run=dry_run,
        force=False,
        follow_renames=False,
        include_submodules=False,
        exclude_patterns=[],
        skip_tests=False,
        skip_infra=False,
        concurrency=1,
        test_run=False,
        yes=True,
        resume=False,
        onboarding=False,
        wiki_style="default",
        language="en",
        resolved_reasoning="auto",
        embedder_name_resolved="mock",
        embedder_was_requested=False,
        resolved_commit_limit=10,
        # Fast mode indexes without generating, which isolates the index phase:
        # whatever reaches ``run_pipeline`` got there on the index phase's
        # account, not generation's.
        run_mode="fast",
    )


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run the index phase against a stubbed pipeline, capturing its kwargs."""
    seen: dict[str, Any] = {}

    async def fake_pipeline(*_a: object, **kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        return _fake_result()

    async def noop_async(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("repowise.core.pipeline.run_pipeline", fake_pipeline)
    monkeypatch.setattr(ws_mod, "persist_result", noop_async)
    monkeypatch.setattr(ws_mod, "get_head_commit", lambda *_a, **_k: "c0ffee")
    monkeypatch.setattr(ws_mod, "save_state", lambda *_a, **_k: None)
    monkeypatch.setattr(ws_mod, "write_editor_project_files", lambda *_a, **_k: None)
    return seen


def _run(tmp_path: Path, ctx_provider: Any, **ctx_kwargs: Any) -> SimpleNamespace:
    repo = _fake_repo(tmp_path / "repo")
    (repo.path / ".repowise").mkdir(parents=True, exist_ok=True)
    _ingest_and_generate_repo(repo, 1, 1, _ctx(repo.path, provider=ctx_provider, **ctx_kwargs))
    return repo


def test_the_index_phase_is_given_the_resolved_provider(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    provider = SimpleNamespace(provider_name="omp", model_name="omp/ccs/claude-opus-5")

    _run(tmp_path, provider)

    assert captured["llm_client"] is provider


def test_a_repo_path_provider_is_rebound_to_the_repo_being_indexed(
    tmp_path: Path, captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that shells out with a working directory has to point at the
    repo under index, not at whichever one resolved it first."""
    rebound = SimpleNamespace(provider_name="codex_cli", model_name="codex_cli/gpt-5.5")
    monkeypatch.setattr(ws_mod, "resolve_provider", lambda *_a, **_k: rebound)

    _run(tmp_path, SimpleNamespace(provider_name="codex_cli", model_name="codex_cli/gpt-5.5"))

    assert captured["llm_client"] is rebound


def test_a_dry_run_keeps_the_index_phase_modelless(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    """Mirrors single-repo init: decision extraction and KG enrichment both call
    the client before the dry-run return is reached, so a dry run passes none."""
    _run(tmp_path, SimpleNamespace(provider_name="omp", model_name="omp/default"), dry_run=True)

    assert captured["llm_client"] is None
