from types import SimpleNamespace

import pytest

from repowise.core.index_scope import file_page_scope, resolve_index_scope, stamp_index_scope


def test_legacy_scope_does_not_invent_completeness() -> None:
    scope = resolve_index_scope({"docs_enabled": True, "total_pages": 12}, {"commit_limit": 500})
    assert scope["run_mode"] == "unknown"
    assert scope["content_provenance"] == "unknown"
    assert scope["git_tier"] == "unknown"
    assert scope["git_commit_cap"] == 500
    assert scope["git_history_coverage"] is None
    assert scope["file_pages"]["generated"] is None
    assert scope["search"]["semantic"] == "unknown"


def test_scope_keeps_configured_caps_separate_from_achieved_counts() -> None:
    state = {"run_mode": "fast", "git_tier": "essential", "docs_mode": "none"}
    stamp_index_scope(
        state,
        {"commit_limit": 500, "max_file_pages": 2},
        file_pages=file_page_scope(
            configured_cap=2,
            eligible=5,
            generated_pages=[SimpleNamespace(page_type="file_page") for _ in range(2)],
        ),
        git_history_coverage={"files_eligible": 5, "files_measured": 3},
    )
    scope = resolve_index_scope(state, {"commit_limit": 999})
    assert scope["git_commit_cap"] == 500
    assert scope["git_history_coverage"]["files_measured"] == 3
    assert scope["file_pages"] == {
        "configured_cap": 2,
        "effective_cap": 2,
        "eligible": 5,
        "generated": 2,
        "omitted": 3,
    }


def test_scope_surfaces_degraded_features_as_unavailable() -> None:
    scope = resolve_index_scope(
        {
            "index_scope": {"analysis": {"unavailable": ["health"], "skipped": []}},
            "degraded": ["Execution flow: parser failed"],
        }
    )
    assert scope["analysis"]["unavailable"] == [
        "Execution flow: parser failed",
        "health",
    ]


def test_mixed_provenance_survives_resolution() -> None:
    # A wiki where only some pages carry model prose is "mixed", not "model".
    state = {"run_mode": "standard", "docs_mode": "llm"}
    stamp_index_scope(state, {}, content_provenance="mixed")
    assert resolve_index_scope(state, {})["content_provenance"] == "mixed"


def test_dropped_generated_files_are_named_in_the_scope() -> None:
    from repowise.core.index_scope import dropped_files_scope

    stats = SimpleNamespace(generated_files=[f"gen/f{i:02}.go" for i in range(25, -1, -1)])
    state: dict = {}
    stamp_index_scope(state, dropped_files=dropped_files_scope(stats))
    dropped = resolve_index_scope(state)["dropped_files"]["generated"]
    assert dropped["count"] == 26
    assert dropped["paths"][:2] == ["gen/f00.go", "gen/f01.go"]
    assert len(dropped["paths"]) == 20
    assert dropped["truncated"] is True


def test_scope_without_traversal_does_not_claim_nothing_dropped() -> None:
    from repowise.core.index_scope import dropped_files_scope

    assert dropped_files_scope(None) is None
    assert resolve_index_scope({})["dropped_files"] is None


def test_scope_drops_legacy_init_tips_and_size_skips() -> None:
    scope = resolve_index_scope(
        {
            "index_scope": {"analysis": {"unavailable": [], "skipped": []}},
            "degraded": [
                "Graph build can take several minutes on a first run. Safe to Ctrl-C — "
                "re-run 'repowise init --resume' to continue where it stopped.",
                "  Not indexed: src/TestData.g.cs (9,745 KB, over the 2,048 KB limit)",
                "  ...and more source files skipped on size",
                "Git indexing skipped: boom",
            ],
        }
    )
    assert scope["analysis"]["unavailable"] == ["Git indexing skipped: boom"]


def test_scope_with_only_legacy_tips_reports_complete() -> None:
    from repowise.core.index_scope import compact_index_scope

    state = {
        "run_mode": "standard",
        "git_tier": "full",
        "docs_mode": "llm",
        "index_scope": {
            "git_commit_cap": 500,
            "file_pages": {"eligible": 10, "generated": 10, "omitted": 0},
            "provider": {"name": "openai", "model": "a-model", "embedder": "openai"},
            "search": {"full_text": "available", "semantic": "available"},
        },
        "git_history_coverage": {"eligible_files": 10, "retained_commits": 50},
        "full_upgrade": {"status": "complete", "completed_stages": ["generation"]},
        "degraded": [
            "Graph build can take several minutes on a first run. Safe to Ctrl-C — "
            "re-run 'repowise init --resume' to continue where it stopped.",
            "  Not indexed: big.cs (9,745 KB, over the 2,048 KB limit)",
            "  ...and more source files skipped on size",
        ],
    }
    scope = resolve_index_scope(state)
    compact = compact_index_scope(scope)
    assert compact is not None
    assert compact["status"] == "complete"
    assert "degraded_analyses" not in compact



@pytest.mark.parametrize(
    ("reused", "persisted_health", "health_report", "unavailable"),
    [
        (True, True, None, []),
        (True, False, None, ["health"]),
        (False, False, object(), []),
        (False, True, None, ["health"]),
    ],
)
def test_init_scope_distinguishes_reused_health_from_fresh_failure(
    reused, persisted_health, health_report, unavailable
) -> None:
    from repowise.cli.commands.init_cmd._finish import _stamp_scope

    result = SimpleNamespace(
        analysis_reused=reused,
        reused_health_available=persisted_health,
        health_report=health_report,
        parsed_files=[],
        generated_pages=None,
    )
    run = SimpleNamespace(
        result=result,
        opts=SimpleNamespace(run_mode="standard", test_run=False),
        provider=None,
        scope_embedder=None,
        resolved_commit_limit=500,
        max_file_pages=None,
    )
    # No previous state is required: generation can have been interrupted
    # after the analysis checkpoint but before state.json was ever written.
    state = {}
    _stamp_scope(run, state, "deterministic")
    assert state["index_scope"]["analysis"]["unavailable"] == unavailable


def test_pipeline_result_carries_checkpoint_health_availability(tmp_path) -> None:
    from dataclasses import MISSING, fields

    from repowise.core.pipeline._run.state import PipelineRun
    from repowise.core.pipeline.orchestrator import _build_result

    required = {
        f.name: None
        for f in fields(PipelineRun)
        if f.default is MISSING and f.default_factory is MISSING
    }
    required.update(repo_path=tmp_path, start=0.0)
    run = PipelineRun(**required)
    run.skip_analysis = True
    run.reused_health_available = True
    result = _build_result(run)
    assert result.analysis_reused is True
    assert result.reused_health_available is True
    assert result.health_report is None


@pytest.mark.parametrize("available", [True, False])
async def test_reused_analysis_reads_health_status_without_rehydrating_report(available) -> None:
    from unittest.mock import AsyncMock

    from repowise.core.pipeline._run.analysis import _rehydrate_analysis

    controller = SimpleNamespace(
        can_skip=AsyncMock(return_value=True),
        rehydrate_analysis=AsyncMock(return_value=(object(), object())),
        has_persisted_health=AsyncMock(return_value=available),
        has_completed_decision_extraction=AsyncMock(return_value=True),
    )
    run = SimpleNamespace(resume_controller=controller, progress=None, health_report=None)
    assert await _rehydrate_analysis(run) is True
    assert run.reused_health_available is available
    assert run.health_report is None


async def test_health_checkpoint_read_failure_falls_back_to_recomputation() -> None:
    from unittest.mock import AsyncMock

    from repowise.core.pipeline._run.analysis import _rehydrate_analysis

    controller = SimpleNamespace(
        can_skip=AsyncMock(return_value=True),
        rehydrate_analysis=AsyncMock(return_value=(object(), object())),
        has_persisted_health=AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    run = SimpleNamespace(resume_controller=controller, progress=None, health_report=None)
    assert await _rehydrate_analysis(run) is False


async def test_legacy_health_evidence_is_recomputed_instead_of_assumed_healthy() -> None:
    from unittest.mock import AsyncMock

    from repowise.core.pipeline._run.analysis import _rehydrate_analysis

    controller = SimpleNamespace(
        can_skip=AsyncMock(return_value=True),
        rehydrate_analysis=AsyncMock(return_value=(object(), object())),
        has_persisted_health=AsyncMock(return_value=None),
    )
    run = SimpleNamespace(resume_controller=controller, progress=None, health_report=None)
    assert await _rehydrate_analysis(run) is False


def test_checkpoint_fields_preserve_pipeline_result_positional_prefix() -> None:
    from dataclasses import fields

    from repowise.core.pipeline.orchestrator import PipelineResult

    names = [f.name for f in fields(PipelineResult)]
    assert names[names.index("health_report") + 1] == "doc_drift_report"
    assert names[-2:] == ["analysis_reused", "reused_health_available"]


@pytest.mark.parametrize(
    ("reused", "persisted_health", "report", "expected"),
    [(True, True, None, True), (True, False, None, False),
     (False, True, None, False), (False, False, object(), True)],
)
async def test_final_persistence_preserves_analysis_availability(
    reused, persisted_health, report, expected
) -> None:
    from unittest.mock import AsyncMock

    from repowise.cli.commands.init_cmd.persistence import _stamp_analysis_checkpoint
    from repowise.core.pipeline.resume import ResumePhase

    previous = {"decision_extraction_completed": True, "health_analysis_completed": True}
    ledger = SimpleNamespace(
        get_completed_job=AsyncMock(return_value=SimpleNamespace(metadata=previous)),
        mark_completed=AsyncMock(),
    )
    result = SimpleNamespace(
        analysis_reused=reused, reused_health_available=persisted_health, health_report=report
    )
    await _stamp_analysis_checkpoint(ledger, result)
    ledger.mark_completed.assert_awaited_once_with(
        ResumePhase.ANALYSIS,
        metadata={"decision_extraction_completed": True, "health_analysis_completed": expected},
    )
    assert previous["health_analysis_completed"] is True
