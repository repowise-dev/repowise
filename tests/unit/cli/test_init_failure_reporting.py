"""Unit tests for issue #1094: failure reporting during repowise init completion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from repowise.cli.commands.init_cmd.generation import run_repo_generation
from repowise.cli.commands.init_cmd.reporting import show_completion
from repowise.core.generation.job_system import JobSystem
from repowise.core.generation.models import GeneratedPage


def _page(page_id: str) -> GeneratedPage:
    return GeneratedPage(
        page_id=page_id,
        page_type="file_page",
        title=f"Title {page_id}",
        content="content",
        source_hash="hash",
        model_name="mock-model",
        provider_name="mock-provider",
        input_tokens=10,
        output_tokens=5,
        cached_tokens=0,
        generation_level=2,
        target_path=f"{page_id}.py",
        created_at="2026-07-26T00:00:00Z",
        updated_at="2026-07-26T00:00:00Z",
    )


def test_run_repo_generation_reports_failures(tmp_path, monkeypatch):
    """When job system records failed pages, run_repo_generation prints breakdown and resume hint."""
    jobs_dir = tmp_path / ".repowise" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    js = JobSystem(jobs_dir)
    config_mock = SimpleNamespace(max_concurrency=1)
    job_id = js.create_job(str(tmp_path), config_mock, "mock", "mock-model")

    # Record some failures
    js.fail_page(job_id, "file_page:lib/a.ts", "Provider error")
    js.fail_page(job_id, "file_page:lib/b.ts", "Provider error")
    js.fail_page(job_id, "module_page:lib/api", "Timeout")
    js.fail_page(job_id, "layer_page:layer1", "Rate limit")
    js.fail_page(job_id, "onboarding:quickstart", "403 Forbidden")

    printed_lines = []

    def mock_print(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        printed_lines.append(text)

    monkeypatch.setattr("repowise.cli.commands.init_cmd.generation.console.print", mock_print)

    async def mock_run_gen(*args, **kwargs):
        return [_page("file_page:lib/c.ts")]

    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.run_async",
        lambda coro: [_page("file_page:lib/c.ts")],
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation._enrich_knowledge_graph",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.flush_cost_tracker",
        lambda tracker: None,
    )

    result = SimpleNamespace(
        repo_name="test_repo",
        parsed_files=[],
        source_map={},
        graph_builder=MagicMock(),
        repo_structure=MagicMock(),
        git_meta_map={},
    )
    provider = SimpleNamespace(provider_name="mock", model_name="mock-model")
    gen_config = SimpleNamespace(max_concurrency=1, deterministic=False)

    pages = run_repo_generation(
        repo_path=tmp_path,
        result=result,
        provider=provider,
        gen_config=gen_config,
        concurrency=1,
        embedder_name_resolved="mock",
        resume=False,
        verbose=False,
    )

    assert len(pages) == 1
    assert getattr(result, "failed_page_ids", None) == [
        "file_page:lib/a.ts",
        "file_page:lib/b.ts",
        "module_page:lib/api",
        "layer_page:layer1",
        "onboarding:quickstart",
    ]

    output = "\n".join(printed_lines)
    assert "Generated [bold]1[/bold] pages" in output
    assert "5 failed" in output
    assert "Failed pages by type:" in output
    assert "file_page: 2" in output
    assert "module_page: 1" in output
    assert "layer_page: 1" in output
    assert "onboarding: 1" in output
    assert "The wiki is incomplete due to provider failures." in output
    assert "repowise init --resume" in output


@pytest.mark.parametrize("verbose_flag", [True, False])
def test_run_repo_generation_zero_failures(tmp_path, monkeypatch, verbose_flag):
    """When no pages fail, run_repo_generation prints success summary only when verbose, stays silent otherwise."""
    jobs_dir = tmp_path / ".repowise" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    js = JobSystem(jobs_dir)
    config_mock = SimpleNamespace(max_concurrency=1)
    js.create_job(str(tmp_path), config_mock, "mock", "mock-model")

    printed_lines = []

    def mock_print(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        printed_lines.append(text)

    monkeypatch.setattr("repowise.cli.commands.init_cmd.generation.console.print", mock_print)
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.run_async",
        lambda coro: [_page("file_page:lib/c.ts")],
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation._enrich_knowledge_graph",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.flush_cost_tracker",
        lambda tracker: None,
    )

    result = SimpleNamespace(
        repo_name="test_repo",
        parsed_files=[],
        source_map={},
        graph_builder=MagicMock(),
        repo_structure=MagicMock(),
        git_meta_map={},
    )
    provider = SimpleNamespace(provider_name="mock", model_name="mock-model")
    gen_config = SimpleNamespace(max_concurrency=1, deterministic=False)

    pages = run_repo_generation(
        repo_path=tmp_path,
        result=result,
        provider=provider,
        gen_config=gen_config,
        concurrency=1,
        embedder_name_resolved="mock",
        resume=False,
        verbose=verbose_flag,
    )

    assert len(pages) == 1
    output = "\n".join(printed_lines)
    assert "failed" not in output
    assert "The wiki is incomplete due to provider failures." not in output
    if verbose_flag:
        assert "Generated [bold]1[/bold] pages" in output
    else:
        assert output.strip() == ""


def test_show_completion_panel_with_failures(monkeypatch):
    """show_completion reflects failed count in metric line when failures exist."""
    printed_panels = []

    def mock_completion_panel(title, metrics, next_steps=None):
        printed_panels.append(metrics)
        return "PANEL"

    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.reporting.build_completion_panel", mock_completion_panel
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.reporting.console.print", lambda *a, **kw: None
    )

    graph_mock = MagicMock()
    graph_mock.number_of_nodes.return_value = 10
    graph_mock.number_of_edges.return_value = 5

    result = SimpleNamespace(
        graph_builder=MagicMock(graph=lambda: graph_mock),
        dead_code_report=None,
        decision_report=None,
        git_summary=None,
        git_meta_map={},
        repo_structure=SimpleNamespace(root_language_distribution={"Python": 1.0}),
        generated_pages=[_page("file_page:a")],
        failed_page_ids=["file_page:b", "module_page:m1"],
        # The full-mode panel reports the index it built, same as index-only.
        file_count=3,
        symbol_count=42,
    )
    provider = SimpleNamespace(provider_name="mock", model_name="mock-model")

    show_completion(
        repo_path="/fake",
        result=result,
        start=0.0,
        effective_index_only=False,
        run_mode="standard",
        provider=provider,
    )

    assert printed_panels
    metrics_dict = dict(printed_panels[0])
    assert metrics_dict.get("Pages generated") == "1 (2 failed)"


def test_run_repo_generation_uses_exact_job_id_when_provided(tmp_path, monkeypatch):
    """When result has a job_id attached, run_repo_generation queries that exact job checkpoint."""
    jobs_dir = tmp_path / ".repowise" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    js = JobSystem(jobs_dir)
    config_mock = SimpleNamespace(max_concurrency=1)
    old_job_id = js.create_job(str(tmp_path), config_mock, "mock", "mock-model")
    js.fail_page(old_job_id, "file_page:old_failure.ts", "Old error")

    # Create target job created later
    target_job_id = js.create_job(str(tmp_path), config_mock, "mock", "mock-model")
    js.fail_page(target_job_id, "file_page:target_failure.ts", "Target error")

    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.console.print", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.run_async",
        lambda coro: [_page("file_page:lib/c.ts")],
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation._enrich_knowledge_graph",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.flush_cost_tracker",
        lambda tracker: None,
    )

    result = SimpleNamespace(
        job_id=target_job_id,
        repo_name="test_repo",
        parsed_files=[],
        source_map={},
        graph_builder=MagicMock(),
        repo_structure=MagicMock(),
        git_meta_map={},
    )
    provider = SimpleNamespace(provider_name="mock", model_name="mock-model")
    gen_config = SimpleNamespace(max_concurrency=1, deterministic=False)

    run_repo_generation(
        repo_path=tmp_path,
        result=result,
        provider=provider,
        gen_config=gen_config,
        concurrency=1,
        embedder_name_resolved="mock",
        resume=False,
        verbose=False,
    )

    assert getattr(result, "failed_page_ids", None) == ["file_page:target_failure.ts"]
