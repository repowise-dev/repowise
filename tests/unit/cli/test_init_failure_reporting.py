"""Unit tests for issue #1094: failure reporting during repowise init completion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from repowise.cli.commands.init_cmd.generation import run_repo_generation
from repowise.cli.commands.init_cmd.reporting import show_completion
from repowise.cli.ui.brand import THEME
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
    # Deliberately not "due to provider failures": the commonest cause is this
    # run's own artifact check rejecting the model's text, and naming the
    # provider sent people to check a key that was working.
    assert "The documentation is incomplete: some pages were not written." in output
    assert "provider failures" not in output
    assert "repowise init --resume" in output


def test_recorded_cost_includes_the_knowledge_graph_enrichment(tmp_path, monkeypatch):
    """Layer naming and the guided tour are real model calls billed through the
    same tracker as the pages, so the figure the completion panel prints has to
    be read after enrichment. Capturing it earlier printed a number the
    llm_costs table disagreed with."""
    jobs_dir = tmp_path / ".repowise" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    JobSystem(jobs_dir).create_job(str(tmp_path), SimpleNamespace(max_concurrency=1), "m", "m")

    class _Tracker:
        def __init__(self):
            self.session_cost = 1.00

    tracker = _Tracker()

    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.console.print", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.run_async",
        lambda coro: [_page("file_page:a")],
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.build_cost_tracker",
        lambda *a, **k: tracker,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.flush_cost_tracker", lambda t: None
    )

    # Enrichment spends another $0.25 through the same tracker.
    def _enrich(**_kwargs):
        tracker.session_cost += 0.25

    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation._enrich_knowledge_graph", _enrich
    )

    result = SimpleNamespace(
        repo_name="test_repo",
        parsed_files=[],
        source_map={},
        graph_builder=MagicMock(),
        repo_structure=MagicMock(),
        git_meta_map={},
    )

    run_repo_generation(
        repo_path=tmp_path,
        result=result,
        provider=SimpleNamespace(provider_name="mock", model_name="mock-model"),
        gen_config=SimpleNamespace(max_concurrency=1, deterministic=False),
        concurrency=1,
        embedder_name_resolved="mock",
        resume=False,
        verbose=False,
    )

    assert result.llm_cost_usd == 1.25


@pytest.mark.parametrize("verbose_flag", [True, False])
def test_run_repo_generation_zero_failures(tmp_path, monkeypatch, verbose_flag):
    """A clean run prints nothing here: the "Generating pages" done row carries the count."""
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
    assert "The documentation is incomplete" not in output
    assert "Generated" not in output


def test_show_completion_panel_with_failures(monkeypatch):
    """show_completion reflects failed count in metric line when failures exist."""
    printed_panels = []

    def mock_completion_panel(console, title, metrics, next_steps=None, **_kw):
        printed_panels.append(metrics)

    monkeypatch.setattr(
        "repowise.cli.ui.finish_panel.print_completion_panel", mock_completion_panel
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
    assert metrics_dict.get("Documentation") == "1 page (2 failed)"


def _completion_result(pages: list) -> SimpleNamespace:
    graph_mock = MagicMock()
    graph_mock.number_of_nodes.return_value = 10
    graph_mock.number_of_edges.return_value = 5
    return SimpleNamespace(
        graph_builder=MagicMock(graph=lambda: graph_mock),
        dead_code_report=None,
        decision_report=None,
        git_summary=None,
        git_meta_map={},
        repo_structure=SimpleNamespace(root_language_distribution={"Python": 1.0}),
        generated_pages=pages,
        failed_page_ids=[],
        file_count=3,
        symbol_count=42,
    )


# Every generation check the report carries.
_CHECK_ROWS = ("Orientation overlap", "Layer grouping", "Artifact checks", "Overview length")


class TestInitRendersGenerationChecks:
    """The generation checks are a -v readout; the default finish is one panel."""

    def _output(self, monkeypatch, *, index_only: bool, verbose: bool = True) -> str:
        import io

        from rich.console import Console

        buf = io.StringIO()
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd.reporting.console",
            Console(file=buf, width=200, force_terminal=False, theme=THEME),
        )
        show_completion(
            repo_path="/fake",
            result=_completion_result([_page("file_page:a")]),
            start=0.0,
            effective_index_only=index_only,
            run_mode="standard",
            provider=SimpleNamespace(provider_name="mock", model_name="mock-model"),
            verbose=verbose,
        )
        return buf.getvalue()

    @pytest.mark.parametrize("index_only", [False, True])
    def test_checks_render_under_verbose(self, monkeypatch, index_only):
        output = self._output(monkeypatch, index_only=index_only)

        for row in _CHECK_ROWS:
            assert row in output, f"{row} missing from init -v output"

    @pytest.mark.parametrize("index_only", [False, True])
    def test_default_finish_has_no_checks_table(self, monkeypatch, index_only):
        output = self._output(monkeypatch, index_only=index_only, verbose=False)

        assert "Generation Checks" not in output
        for row in _CHECK_ROWS:
            assert row not in output

    def test_check_failure_is_calm_and_only_under_verbose(self, monkeypatch):
        """A check that could not run is not an error on an exit-0 run."""

        def _boom(*_args, **_kwargs):
            raise RuntimeError("overlap exploded")

        monkeypatch.setattr("repowise.core.generation.report.GenerationReport.from_pages", _boom)
        verbose = self._output(monkeypatch, index_only=False)
        assert "Generation checks were skipped" in verbose
        assert "overlap exploded" in verbose
        assert "did not run" not in verbose

        default = self._output(monkeypatch, index_only=False, verbose=False)
        assert "overlap exploded" not in default
        assert "Generation checks" not in default


def _stub_page(page_id: str) -> GeneratedPage:
    """A page whose provider call failed and fell back to a structural render."""
    from repowise.core.generation.models import STUB_FALLBACK_ERROR

    page = _page(page_id)
    page.metadata[STUB_FALLBACK_ERROR] = "provider exploded"
    return page


class TestStubsAreNotCountedTwice:
    """A failed page that left a placeholder is one page, not two outcomes.

    A provider failure still hands back a row, rendered from structure alone,
    so the page exists and a later run can find and refill it. That row is in
    ``generated_pages`` like any other, so the summary counted it as generated
    and, from the job checkpoint, as failed. On a real build that read as 3762
    generated and 10 failed out of a 92-page plan: three numbers that cannot
    all describe the same run.
    """

    def _run(self, tmp_path, monkeypatch, pages, failures):
        jobs_dir = tmp_path / ".repowise" / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        js = JobSystem(jobs_dir)
        job_id = js.create_job(str(tmp_path), SimpleNamespace(max_concurrency=1), "mock", "m")
        for page_id in failures:
            js.fail_page(job_id, page_id, "Provider error")

        printed: list[str] = []
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd.generation.console.print",
            lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        )
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd.generation.run_async", lambda coro: pages
        )
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd.generation._enrich_knowledge_graph",
            lambda **kw: None,
        )
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd.generation.flush_cost_tracker", lambda t: None
        )

        run_repo_generation(
            repo_path=tmp_path,
            result=SimpleNamespace(
                job_id=job_id,
                repo_name="test_repo",
                parsed_files=[],
                source_map={},
                graph_builder=MagicMock(),
                repo_structure=MagicMock(),
                git_meta_map={},
            ),
            provider=SimpleNamespace(provider_name="mock", model_name="mock-model"),
            gen_config=SimpleNamespace(max_concurrency=1, deterministic=False),
            concurrency=1,
            embedder_name_resolved="mock",
            resume=False,
            verbose=True,
        )
        return "\n".join(printed)

    def test_placeholder_pages_are_excluded_from_the_generated_count(self, tmp_path, monkeypatch):
        """Two written pages plus two placeholders is "2 generated", not 4."""
        pages = [
            _page("module_page:a"),
            _page("module_page:b"),
            _stub_page("module_page:c"),
            _stub_page("module_page:d"),
        ]
        output = self._run(tmp_path, monkeypatch, pages, ["module_page:c", "module_page:d"])

        assert "Generated [bold]2[/bold] pages" in output
        assert "2 failed" in output
        assert "Generated [bold]4[/bold] pages" not in output

    def test_the_placeholder_is_named_so_a_present_page_is_not_a_surprise(
        self, tmp_path, monkeypatch
    ):
        """Calling a page that exists "missing" reads as the report being wrong."""
        pages = [_page("module_page:a"), _stub_page("module_page:c")]
        output = self._run(tmp_path, monkeypatch, pages, ["module_page:c"])

        assert "1 of them left a placeholder page" in output
        assert "repowise init --resume" in output

    def test_hard_failures_alone_mention_no_placeholder(self, tmp_path, monkeypatch):
        """A failure that produced no row at all has nothing to explain away."""
        output = self._run(tmp_path, monkeypatch, [_page("module_page:a")], ["module_page:z"])

        assert "Generated [bold]1[/bold] pages" in output
        assert "placeholder" not in output

    def test_a_clean_run_is_unaffected(self, tmp_path, monkeypatch):
        output = self._run(tmp_path, monkeypatch, [_page("module_page:a")], [])

        assert "Generated" not in output
        assert "failed" not in output


def test_completion_panel_excludes_placeholders_from_the_page_count(monkeypatch):
    """The panel derives the same split from the same helper, not its own count."""
    printed_panels = []
    monkeypatch.setattr(
        "repowise.cli.ui.finish_panel.print_completion_panel",
        lambda console, title, metrics, next_steps=None, **_kw: printed_panels.append(metrics),
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.reporting.console.print", lambda *a, **kw: None
    )

    result = _completion_result([_page("module_page:a"), _stub_page("module_page:b")])
    result.failed_page_ids = ["module_page:b"]

    show_completion(
        repo_path="/fake",
        result=result,
        start=0.0,
        effective_index_only=False,
        run_mode="standard",
        provider=SimpleNamespace(provider_name="mock", model_name="mock-model"),
    )

    assert dict(printed_panels[0]).get("Documentation") == "1 page (1 failed)"


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


class TestInitClosingKeylessNote:
    """A keyless index closes by naming its upgrade commands (#3105).

    The note must not depend on editor setup: the headless path has no
    ``generate`` row in the next-steps panel, so this line is the only place
    it hears about the upgrades.
    """

    def _output(self, monkeypatch, *, setup, pages, state=None) -> str:
        import io

        from rich.console import Console

        buf = io.StringIO()
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd.reporting.console",
            Console(file=buf, width=200, force_terminal=False, theme=THEME),
        )
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd.reporting.load_state",
            lambda _p: state if state is not None else {"docs_mode": "deterministic"},
        )
        monkeypatch.setattr("repowise.cli.commands.init_cmd.reporting.load_config", lambda _p: {})
        show_completion(
            repo_path="/fake",
            result=_completion_result(pages),
            start=0.0,
            effective_index_only=True,
            run_mode="standard",
            provider=SimpleNamespace(provider_name="template", model_name="template"),
            setup=setup,
        )
        return buf.getvalue()

    @pytest.mark.parametrize(
        "setup",
        [
            None,
            SimpleNamespace(editor_setup_disabled=True),
            SimpleNamespace(
                editor_setup_disabled=False,
                claude_code_connected=True,
                interactive=True,
                first_index=True,
                autosync_hook_installed=True,
                rewrite_hook_installed=True,
            ),
        ],
        ids=["no-setup", "editor-setup-disabled", "editor-setup-done"],
    )
    def test_note_prints_with_and_without_editor_setup(self, monkeypatch, setup):
        out = self._output(monkeypatch, setup=setup, pages=[_page("file_page:a")])
        # Once, as a step row: the duplicate closing sentence is gone.
        assert out.count("repowise generate") == 1
        assert "Semantic search is off: repowise reindex turns it on" in out
        assert "--embedder ..." not in out

    def test_note_drops_the_search_half_when_semantic_search_is_available(self, monkeypatch):
        state = {
            "docs_mode": "deterministic",
            "index_scope": {
                "content_provenance": "template",
                "search": {"full_text": "available", "semantic": "available"},
            },
        }
        out = self._output(monkeypatch, setup=None, pages=[_page("file_page:a")], state=state)
        assert "repowise generate" in out
        assert "reindex" not in out

    def test_no_search_line_when_no_documentation_was_built(self, monkeypatch):
        out = self._output(monkeypatch, setup=None, pages=[])
        assert "Semantic search" not in out
        assert "Documentation" in out and "none" in out
