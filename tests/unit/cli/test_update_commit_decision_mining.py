"""`repowise update` mines the PR bodies and commits it brought in, gated like init."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from repowise.cli.commands.update_cmd import decision_passes
from repowise.core.analysis.decisions import evolution
from repowise.core.analysis.decisions import extractor as extractor_mod
from repowise.core.analysis.decisions.commit_mining import restrict_to_commits
from repowise.core.analysis.decisions.policy import preset_policy
from repowise.core.analysis.decisions.records import DecisionExtractionReport, ExtractedDecision


def _meta(*shas: str) -> dict:
    return {"significant_commits_json": json.dumps([{"sha": sha} for sha in shas])}


def test_restrict_keeps_only_the_named_commits_and_drops_empty_files():
    meta_map = {"a.py": _meta("aaaa1111", "bbbb2222"), "b.py": _meta("cccc3333")}

    narrowed = restrict_to_commits(meta_map, {"bbbb2222"})

    assert list(narrowed) == ["a.py"]
    assert narrowed["a.py"]["significant_commits_json"] == [{"sha": "bbbb2222"}]
    # The caller's map is not edited.
    assert json.loads(meta_map["a.py"]["significant_commits_json"])[0]["sha"] == "aaaa1111"


def test_restrict_matches_a_full_sha_by_its_prefix():
    narrowed = restrict_to_commits({"a.py": _meta("bbbb2222" + "f" * 32)}, {"bbbb2222"})

    assert list(narrowed) == ["a.py"]


class _StubExtractor:
    calls: ClassVar[list[dict]] = []

    def __init__(self, **kwargs) -> None:
        self.git_meta_map = kwargs["git_meta_map"]

    async def extract_all(self, *, enabled_sources):
        _StubExtractor.calls.append(
            {"sources": set(enabled_sources), "files": sorted(self.git_meta_map)}
        )
        found = ExtractedDecision(title="Mined", decision="d", source="pr")
        return DecisionExtractionReport(total_found=1, decisions=[found], by_source={"pr": 1})


@pytest.fixture
def stubbed(monkeypatch):
    _StubExtractor.calls = []
    monkeypatch.setattr(extractor_mod, "DecisionExtractor", _StubExtractor)
    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: {"bbbb2222"})
    return _StubExtractor.calls


def _run_and_docs(*, git_changed: bool = False, provider: object | None = object()):
    run = SimpleNamespace(
        repo_path=Path("."),
        base_ref="base",
        head="head",
        config=SimpleNamespace(git_changed=git_changed),
        git_meta_map={"old.py": _meta("aaaa1111"), "new.py": _meta("bbbb2222")},
        graph_builder=SimpleNamespace(graph=lambda: None),
        degraded=[],
        state={},
    )
    docs = SimpleNamespace(
        provider=provider,
        cost_tracker=SimpleNamespace(record_as=lambda _op: nullcontext()),
        new_decision_markers=[],
    )
    return run, docs


def _mine(run, docs, policy=None) -> None:
    opts = SimpleNamespace(verbose=False)
    decision_passes._mine_new_history(opts, run, docs, policy or preset_policy("default"))


def test_update_mines_pr_and_commits_in_its_own_range(stubbed):
    run, docs = _run_and_docs()

    _mine(run, docs)

    assert stubbed == [{"sources": {"pr", "git_archaeology"}, "files": ["new.py"]}]
    assert [d.title for d in docs.new_decision_markers] == ["Mined"]


def test_a_history_window_change_leaves_git_archaeology_to_the_full_rerun(stubbed):
    run, docs = _run_and_docs(git_changed=True)

    _mine(run, docs)

    assert stubbed[0]["sources"] == {"pr"}


def test_no_provider_mines_nothing(stubbed):
    run, docs = _run_and_docs(provider=None)

    _mine(run, docs)

    assert stubbed == []
    assert docs.new_decision_markers == []


def test_a_policy_with_the_model_off_mines_nothing(stubbed):
    run, docs = _run_and_docs()

    _mine(run, docs, preset_policy("default").with_llm(False))

    assert stubbed == []


def test_no_new_commit_mines_nothing(stubbed, monkeypatch):
    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: set())
    run, docs = _run_and_docs()

    _mine(run, docs)

    assert stubbed == []


def test_a_source_failure_is_reported_on_the_run(stubbed, monkeypatch):
    async def _failing(self, *, enabled_sources):
        return DecisionExtractionReport(
            total_found=0, decisions=[], by_source={}, failures={"pr": "RuntimeError: outage"}
        )

    monkeypatch.setattr(_StubExtractor, "extract_all", _failing)
    run, docs = _run_and_docs()

    _mine(run, docs)

    assert run.degraded == ["Decision mining (pr): RuntimeError: outage"]


def test_a_failed_source_keeps_its_range_and_a_succeeding_one_advances(stubbed, monkeypatch):
    bases: list[str] = []

    def _commits(_repo_path, base, _head):
        bases.append(base)
        return {"bbbb2222"}

    monkeypatch.setattr(evolution, "commits_in_range", _commits)

    async def _partial(self, *, enabled_sources):
        if "pr" in enabled_sources:
            return DecisionExtractionReport(
                total_found=0,
                decisions=[],
                by_source={},
                failures={"pr": "RuntimeError: outage"},
            )
        return DecisionExtractionReport(total_found=0, decisions=[], by_source={})

    monkeypatch.setattr(_StubExtractor, "extract_all", _partial)
    run, docs = _run_and_docs()
    run.state = {
        "decision_mining": {
            "pr": {"last_success_commit": "base"},
            "git_archaeology": {"last_success_commit": "base"},
        }
    }

    _mine(run, docs)

    assert bases == ["base"]
    ledger = run.state["decision_mining"]
    assert ledger["pr"]["last_success_commit"] == "base"
    assert ledger["pr"]["last_status"] == "failed"
    assert ledger["pr"]["last_failure"] == "RuntimeError: outage"
    assert ledger["git_archaeology"]["last_success_commit"] == "head"
    assert ledger["git_archaeology"]["last_status"] == "ok"

    # The next run starts pr at the commit it last succeeded on, and the
    # source that already advanced at the new base.
    bases.clear()
    run.base_ref = "head"
    run.head = "next"
    _mine(run, docs)

    assert bases == ["base", "head"]


def test_an_empty_range_does_not_advance_a_source(stubbed, monkeypatch):
    """An empty answer can be a git failure; advancing would lose the retry."""
    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: set())
    run, docs = _run_and_docs()
    run.state = {
        "decision_mining": {
            "pr": {"last_success_commit": "base"},
            "git_archaeology": {"last_success_commit": "base"},
        }
    }

    _mine(run, docs)

    assert stubbed == []
    assert run.state["decision_mining"]["pr"]["last_success_commit"] == "base"
    assert run.state["decision_mining"]["git_archaeology"]["last_success_commit"] == "base"

