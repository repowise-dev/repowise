"""Persistence must not be a silent stretch at the end of a run.

Everything after "Generated N pages" ran under one indeterminate spinner:
SQL upserts, the stale-page sweep, and a full-text index loop that awaits once
per generated page. On a few thousand pages that is minutes of a screen that
looks identical whether the run is working or wedged — directly after a
generation bar that had just announced it was finished.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.cli.commands.init_cmd.persistence import persist_result
from repowise.core.generation.models import GeneratedPage


class _RecordingProgress:
    def __init__(self) -> None:
        self.started: list[tuple[str, int | None]] = []
        self.items: list[str] = []
        self.done: list[str] = []

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self.started.append((phase, total))

    def on_item_done(self, phase: str) -> None:
        self.items.append(phase)

    def on_phase_done(self, phase: str) -> None:
        self.done.append(phase)

    def on_message(self, level: str, text: str) -> None:
        pass


def _page(target: str) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=f"module_page:{target}",
        page_type="module_page",
        title=target,
        content=f"content for {target}",
        source_hash="x" * 64,
        model_name="mock",
        provider_name="mock",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        generation_level=4,
        target_path=target,
        created_at=now,
        updated_at=now,
    )


def _result(pages: list[GeneratedPage]) -> SimpleNamespace:
    return SimpleNamespace(
        repo_name="r",
        index_persisted_incrementally=True,
        generated_pages=pages,
        tech_stack=None,
        vector_store=None,
        dead_code_report=None,
        health_report=None,
        decision_report=None,
        git_metadata_list=[],
        knowledge_graph_result=None,
        authoritative_page_types=set(),
        preserved_page_ids=set(),
    )


async def test_full_text_indexing_reports_a_page_at_a_time(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    progress = _RecordingProgress()

    pages = [_page("a"), _page("b"), _page("c")]
    await persist_result(_result(pages), repo_path, progress)

    # A real denominator, not a spinner: the loop length is known up front.
    assert ("persist", len(pages)) in progress.started
    assert progress.items.count("persist") == len(pages)
    assert "persist" in progress.done


async def test_persistence_still_works_without_a_progress_callback(tmp_path):
    """The parameter is optional — the workspace flow and the tests pass none."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    await persist_result(_result([_page("a")]), repo_path)


async def test_an_index_only_run_announces_no_page_loop(tmp_path):
    """With no pages there is no per-page work, so there is no total to show."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    progress = _RecordingProgress()

    await persist_result(_result([]), repo_path, progress)

    assert [phase for phase, _ in progress.started if phase == "persist"] == []
