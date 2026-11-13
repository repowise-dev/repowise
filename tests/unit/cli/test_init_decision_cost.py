"""Init prices the decision mining its index phase runs (issue #5155).

Decisions are mined with the model during analysis, before generation. The
tracker used to be attached only in generation, so that spend never reached
``llm_costs`` or the init "Cost" line.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from repowise.core.providers.llm.base import GeneratedResponse, record_generation_cost


class _Provider:
    """Records its spend on whatever tracker the run attached, as real providers do."""

    provider_name = "anthropic"
    model_name = "claude-sonnet-4-6"

    def __init__(self) -> None:
        self._cost_tracker = None

    async def generate(self, *_args: Any, **_kwargs: Any) -> GeneratedResponse:
        response = GeneratedResponse(content="{}", input_tokens=1000, output_tokens=500)
        await record_generation_cost(self._cost_tracker, model=self.model_name, result=response)
        return response


async def _mining_pipeline(
    repo_path: Any, *, llm_client: Any = None, cost_tracker: Any = None, **_: Any
):
    """The orchestrator's tracker hand-off, then one labelled decision call."""
    from repowise.core.analysis.decisions.extractor import DecisionExtractor
    from repowise.core.pipeline._run.state import attach_cost_tracker

    attach_cost_tracker(llm_client, cost_tracker)
    if llm_client is not None:
        labelled = DecisionExtractor._cost_operation(
            SimpleNamespace(_provider=llm_client), "decision_extraction"
        )
        with labelled:
            await llm_client.generate("system", "user")
    return SimpleNamespace(repo_name=Path(repo_path).name, file_count=1, symbol_count=1)


def _run(repo: Path, **overrides: Any) -> Any:
    from repowise.cli.commands.init_cmd._options import InitOptions, InitRun

    fields: dict[str, Any] = {name: None for name in InitOptions.__dataclass_fields__}
    fields.update(
        exclude=(),
        coverage_report=(),
        run_mode="standard",
        concurrency=1,
        onboarding=True,
        editor_setup=False,
        save_key=True,
        yes=True,
        index_only=False,
        follow_renames=False,
        skip_tests=False,
        skip_infra=False,
        dry_run=False,
        resume=False,
        force=False,
        no_cost_tracking=False,
    )
    fields.update(overrides)
    run = InitRun(opts=InitOptions(**fields), repo_path=repo, start=0.0, is_interactive=False)
    run.provider = _Provider()
    return run


def _cost_rows(repo: Path) -> list[tuple[str, int]]:
    db = repo / ".repowise" / "wiki.db"
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "select operation, count(*) from llm_costs group by operation"
        ).fetchall()


def _index(monkeypatch, run: Any) -> None:
    import repowise.core.pipeline as pipeline
    from repowise.cli.commands.init_cmd._ingest import index_repository

    monkeypatch.delenv("REPOWISE_NO_COST_TRACKING", raising=False)
    monkeypatch.setattr(pipeline, "run_pipeline", _mining_pipeline)
    (run.repo_path / ".repowise").mkdir(parents=True, exist_ok=True)
    index_repository(run)


def test_init_records_the_decision_mining_it_pays_for(tmp_path, monkeypatch):
    run = _run(tmp_path)
    _index(monkeypatch, run)

    assert _cost_rows(tmp_path) == [("decision_extraction", 1)]
    assert run.cost_tracker.session_cost > 0


def test_no_cost_tracking_keeps_decision_spend_in_memory(tmp_path, monkeypatch):
    run = _run(tmp_path, no_cost_tracking=True)
    _index(monkeypatch, run)

    assert _cost_rows(tmp_path) == []
    # Still counted, so the run can report it; only the ledger is skipped.
    assert run.cost_tracker.session_cost > 0


def test_a_run_that_calls_no_model_builds_no_tracker(tmp_path, monkeypatch):
    run = _run(tmp_path, index_only=True)
    _index(monkeypatch, run)

    assert run.cost_tracker is None
    assert _cost_rows(tmp_path) == []


def test_generation_bills_the_same_tracker_so_the_cost_line_totals_both(tmp_path, monkeypatch):
    from repowise.cli.commands.init_cmd import generation
    from repowise.core.generation.cost_tracker import CostTracker

    def _no_second_tracker(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("generation must reuse the run's tracker")

    monkeypatch.setattr(generation.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(generation, "run_async", lambda coro: coro.close() or [])
    monkeypatch.setattr(generation, "_enrich_knowledge_graph", lambda **kw: None)
    monkeypatch.setattr(generation, "build_cost_tracker", _no_second_tracker)

    tracker = CostTracker()
    provider = _Provider()
    asyncio.run(tracker.record("claude-sonnet-4-6", 1000, 500, "decision_extraction"))
    mined = tracker.session_cost
    result = SimpleNamespace(
        repo_name="repo",
        parsed_files=[],
        source_map={},
        graph_builder=MagicMock(),
        repo_structure=MagicMock(),
        git_meta_map={},
        vector_store=object(),
    )
    generation.run_repo_generation(
        repo_path=tmp_path,
        result=result,
        provider=provider,
        gen_config=SimpleNamespace(max_concurrency=1, deterministic=False),
        concurrency=1,
        embedder_name_resolved="mock",
        resume=False,
        verbose=False,
        cost_tracker=tracker,
    )

    assert provider._cost_tracker is tracker
    assert result.llm_cost_usd == mined > 0


def test_workspace_init_records_each_repos_decision_mining(tmp_path, monkeypatch):
    import repowise.core.pipeline as pipeline
    from repowise.cli.commands.init_cmd import workspace

    monkeypatch.delenv("REPOWISE_NO_COST_TRACKING", raising=False)
    monkeypatch.setattr(pipeline, "run_pipeline", _mining_pipeline)
    (tmp_path / ".repowise").mkdir()
    ctx = SimpleNamespace(
        force=False,
        resolved_commit_limit=0,
        follow_renames=False,
        exclude_patterns=[],
        include_submodules=False,
        run_mode="standard",
        index_only=False,
    )
    index = workspace._index_repo(SimpleNamespace(path=tmp_path, alias="r"), ctx, _Provider())

    assert _cost_rows(tmp_path) == [("decision_extraction", 1)]
    # Generation for this repo bills the same tracker.
    assert index.cost_tracker.session_cost > 0
