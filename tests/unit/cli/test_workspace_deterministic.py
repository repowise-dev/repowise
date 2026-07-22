"""Workspace init's deterministic (template) generation path.

An index-only (or provider-less / cost-declined) workspace repo now renders a
complete template wiki like single-repo init, instead of being left with no
pages and ``docs_mode: "none"``. These tests pin the two behaviours that make
that safe: the run uses the null ``TemplateProvider`` with a deterministic
config, and it never puts a hosted embedder on the bill unless the user asked.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from repowise.cli.commands.init_cmd import workspace as ws


def _capture_run_repo_generation(monkeypatch: Any) -> dict[str, Any]:
    """Patch ``run_repo_generation`` and return a dict capturing its kwargs."""
    captured: dict[str, Any] = {}

    def fake_run_repo_generation(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return ["page1", "page2"]

    monkeypatch.setattr(ws, "run_repo_generation", fake_run_repo_generation)
    return captured


def test_deterministic_generation_uses_template_provider_and_config(
    tmp_path: Any, monkeypatch: Any
) -> None:
    captured = _capture_run_repo_generation(monkeypatch)

    pages, embedder = ws._run_workspace_deterministic_generation(
        repo_path=tmp_path,
        result=SimpleNamespace(repo_name="demo"),
        embedder_name_resolved="mock",
        embedder_was_requested=False,
        concurrency=8,
        resume=False,
        onboarding=True,
        wiki_style="comprehensive",
        language="en",
    )

    assert pages == ["page1", "page2"]
    # Null provider (no model, no tokens, no key) + deterministic config.
    from repowise.core.providers.llm.template import TemplateProvider

    assert isinstance(captured["provider"], TemplateProvider)
    assert captured["gen_config"].deterministic is True
    assert captured["verbose"] is False
    assert embedder == "mock"


def test_deterministic_generation_drops_inferred_hosted_embedder(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """A hosted embedder inferred from the environment is dropped to the mock.

    The mode is sold as "no key, no spend"; embedding thousands of pages through
    a hosted embedder is a real bill nobody who ran index-only asked for.
    """
    captured = _capture_run_repo_generation(monkeypatch)

    _pages, embedder = ws._run_workspace_deterministic_generation(
        repo_path=tmp_path,
        result=SimpleNamespace(repo_name="demo"),
        embedder_name_resolved="openai",  # hosted, inferred (not requested)
        embedder_was_requested=False,
        concurrency=8,
        resume=False,
        onboarding=True,
        wiki_style="comprehensive",
        language="en",
    )

    assert embedder == "mock"
    assert captured["embedder_name_resolved"] == "mock"


def test_deterministic_generation_keeps_requested_hosted_embedder(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """A hosted embedder the user explicitly named is honored."""
    captured = _capture_run_repo_generation(monkeypatch)

    _pages, embedder = ws._run_workspace_deterministic_generation(
        repo_path=tmp_path,
        result=SimpleNamespace(repo_name="demo"),
        embedder_name_resolved="openai",
        embedder_was_requested=True,
        concurrency=8,
        resume=False,
        onboarding=True,
        wiki_style="comprehensive",
        language="en",
    )

    assert embedder == "openai"
    assert captured["embedder_name_resolved"] == "openai"
