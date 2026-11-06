"""The decision lanes a keyless index skips, and the later run that fills them."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core import decision_lanes
from repowise.core.analysis.decision_extractor import DecisionExtractor
from repowise.core.decision_lanes import (
    PENDING_DECISION_LANES_KEY,
    DecisionLanePlan,
    lanes_label,
    lanes_needing_model,
    pending_decision_lanes,
    record_decision_lanes,
    run_decision_lanes,
)

MODEL_LANES = {"git_archaeology", "pr", "comment"}


def _repo(tmp_path: Path, config: str = "") -> Path:
    (tmp_path / ".repowise").mkdir()
    if config:
        (tmp_path / ".repowise" / "config.yaml").write_text(config, encoding="utf-8")
    return tmp_path


def test_default_policy_needs_a_model_for_prs_history_and_comments(tmp_path: Path) -> None:
    assert set(lanes_needing_model(_repo(tmp_path))) == MODEL_LANES


def test_a_keyless_run_records_the_lanes_and_a_model_run_clears_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state: dict = {}
    record_decision_lanes(state, repo, ran_with_model=False)
    assert set(state[PENDING_DECISION_LANES_KEY]) == MODEL_LANES

    record_decision_lanes(state, repo, ran_with_model=True)
    assert PENDING_DECISION_LANES_KEY not in state


def test_pending_lanes_follow_the_current_policy(tmp_path: Path) -> None:
    """A lane switched off since the keyless run is not run later."""
    repo = _repo(tmp_path, "decisions:\n  sources:\n    pr: false\n")
    state = {PENDING_DECISION_LANES_KEY: ["git_archaeology", "pr", "comment"]}
    assert pending_decision_lanes(state, repo) == ("git_archaeology", "comment")


def test_local_only_preset_leaves_nothing_pending(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "decisions:\n  preset: local_only\n")
    state: dict = {}
    record_decision_lanes(state, repo, ran_with_model=False)
    assert PENDING_DECISION_LANES_KEY not in state


def test_lanes_label_reads_as_a_list() -> None:
    assert lanes_label(("comment", "git_archaeology", "pr")) == "PRs, history and comments"
    assert lanes_label(("pr", "comment")) == "PRs and comments"
    assert lanes_label(("git_archaeology",)) == "history"


def test_planned_calls_batch_the_same_candidates_the_miners_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extractor = DecisionExtractor(repo_path=Path("."))
    monkeypatch.setattr(extractor, "_git_archaeology_candidates", lambda: ([{}] * 7, {}))
    monkeypatch.setattr(extractor, "_pr_body_candidates", lambda: ([{}] * 25, {}))
    monkeypatch.setattr(extractor, "_comment_snippets", lambda: [("a", "b")] * 5)
    # 7 commits in fives, 25 PR bodies in fives, 5 comments in fours.
    assert extractor.planned_model_calls(("git_archaeology", "pr", "comment")) == 2 + 5 + 2
    assert extractor.planned_model_calls(("adr", "inline_marker")) == 0


def _inputs() -> SimpleNamespace:
    return SimpleNamespace(
        graph_builder=SimpleNamespace(graph=lambda: None),
        git_meta_map={"a.py": {"path": "a.py"}},
        parsed_files=[],
        source_map={},
    )


async def test_only_sources_runs_just_those_lanes_and_no_session_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.pipeline.phases import analysis

    seen: dict = {}

    async def fake_extract_all(self, *, on_step=None, enabled_sources=None):
        seen["sources"] = tuple(enabled_sources)
        return SimpleNamespace(decisions=[], by_source={}, total_found=0, failures={})

    async def no_session(*_a, **_k):
        raise AssertionError("session passes must not run for a lane fill")

    monkeypatch.setattr(DecisionExtractor, "extract_all", fake_extract_all)
    monkeypatch.setattr(analysis, "_add_session_decisions", no_session)
    monkeypatch.setattr(analysis, "_run_session_discovery", no_session)

    report = await analysis._run_decision_extraction(
        _repo(tmp_path),
        llm_client=object(),
        graph_builder=_inputs().graph_builder,
        git_meta_map={},
        parsed_files=[],
        progress=None,
        only_sources=("pr", "comment"),
    )
    assert report is not None
    assert seen["sources"] == ("pr", "comment")


async def test_a_failed_lane_stays_pending_and_the_rest_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.pipeline import persist
    from repowise.core.pipeline.phases import analysis

    report = SimpleNamespace(decisions=[object(), object()], failures={"pr": "boom"})

    async def fake_extraction(*_a, **kwargs):
        assert kwargs["only_sources"] == ("git_archaeology", "pr")
        return report

    persisted: list = []

    async def fake_persist(result, _session, repo_id):
        persisted.append((result.decision_report, result.health_report, repo_id))

    class _Session:
        async def commit(self) -> None: ...
        async def rollback(self) -> None: ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None: ...

    monkeypatch.setattr(analysis, "_run_decision_extraction", fake_extraction)
    monkeypatch.setattr(persist, "persist_analysis", fake_persist)

    outcome = await run_decision_lanes(
        _Session,
        "repo-1",
        _repo(tmp_path),
        DecisionLanePlan(("git_archaeology", "pr"), calls=3),
        provider=object(),
        inputs=_inputs(),
    )
    assert outcome.found == 2
    assert outcome.still_pending == ("pr",)
    assert persisted == [(report, None, "repo-1")]


async def test_an_extraction_that_never_finished_keeps_every_lane_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.pipeline.phases import analysis

    async def fake_extraction(*_a, **_k):
        return None

    monkeypatch.setattr(analysis, "_run_decision_extraction", fake_extraction)
    plan = DecisionLanePlan(("git_archaeology", "comment"), calls=2)
    outcome = await run_decision_lanes(
        None, "repo-1", _repo(tmp_path), plan, provider=object(), inputs=_inputs()
    )
    assert outcome.still_pending == plan.lanes


def test_plan_is_none_when_nothing_is_pending(tmp_path: Path) -> None:
    assert decision_lanes.plan_decision_lanes({}, _repo(tmp_path), _inputs()) is None
