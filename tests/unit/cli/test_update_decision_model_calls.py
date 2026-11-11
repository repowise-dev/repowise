"""`repowise update` makes no decision model call unless update mining is on."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import decision_passes
from repowise.core.analysis import decision_extractor as extractor_shim
from repowise.core.analysis.decisions import evolution
from repowise.core.analysis.decisions import extractor as extractor_mod
from repowise.core.analysis.decisions.records import DecisionExtractionReport
from repowise.core.pipeline.phase_timing import PhaseTimings


class _Tripwire:
    """A provider that records every use, so a test can prove it went unused."""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name: str):
        self.touched.append(name)
        raise AssertionError(f"decision pass used the provider: {name}")


@pytest.fixture
def seen(monkeypatch):
    """The provider each model-capable pass was handed, by pass."""
    calls: list[tuple[str, object]] = []

    class _Extractor:
        def __init__(self, **kwargs) -> None:
            calls.append(("extractor", kwargs["provider"]))

        async def extract_all(self, *, enabled_sources):
            return DecisionExtractionReport(total_found=0, decisions=[], by_source={})

        async def scan_inline_markers(self, *, restrict_to_files):
            return []

    monkeypatch.setattr(extractor_mod, "DecisionExtractor", _Extractor)
    monkeypatch.setattr(extractor_shim, "DecisionExtractor", _Extractor)
    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: {"bbbb2222"})

    def _evolution(*_a, provider, **_k):
        calls.append(("evolution", provider))
        return None

    monkeypatch.setattr(decision_passes, "_run_decision_evolution", _evolution)
    monkeypatch.setattr(decision_passes, "_build_update_vector_store", lambda *_a, **_k: None)
    from repowise.core import git_refs

    monkeypatch.setattr(git_refs, "is_ancestor", lambda *_a: True)
    return calls


def _run(tmp_path: Path, cfg: dict):
    run = SimpleNamespace(
        repo_path=tmp_path,
        cfg=cfg,
        base_ref="base",
        head="head",
        # A history-window change too, so the git archaeology re-run is covered.
        config=SimpleNamespace(git_changed=True, traversal_changed=False, generation_changed=False),
        git_meta_map={"new.py": {"significant_commits_json": '[{"sha": "bbbb2222"}]'}},
        graph_builder=SimpleNamespace(graph=lambda: None),
        file_diffs=[SimpleNamespace(path="new.py", status="modified")],
        parsed_files=[],
        source_map={"new.py": ""},
        degraded=[],
        state={},
        timings=PhaseTimings(),
    )
    provider = _Tripwire()
    docs = SimpleNamespace(
        provider=provider,
        cost_tracker=SimpleNamespace(record_as=lambda _op: nullcontext()),
        new_decision_markers=[],
        session_decisions=[],
        decision_vector_store=None,
        config=SimpleNamespace(max_concurrency=1),
        git_history_remined=False,
    )
    decision_passes.run_decision_passes(SimpleNamespace(verbose=False), run, docs)
    run.docs = docs
    return run, provider


def test_a_default_update_hands_no_decision_pass_a_provider(tmp_path, seen):
    run, provider = _run(tmp_path, {})

    # The marker rescan and evolution still run, without a model.
    assert {name for name, _ in seen} == {"extractor", "evolution"}
    assert all(p is None for _, p in seen)
    assert provider.touched == []
    assert run.degraded == []
    # Nothing was re-mined, so the history-window change must not replace the
    # mined records already stored.
    assert run.docs.git_history_remined is False


def test_update_mining_on_hands_every_pass_the_provider(tmp_path, seen):

    run, _ = _run(tmp_path, {"decisions": {"update_mining": True}})
    assert run.docs.git_history_remined is True
    names = [name for name, p in seen if p is not None]
    # Re-run, new-history mining, marker rescan, and evolution.
    assert names.count("extractor") == 3
    assert "evolution" in names


def test_a_configured_preset_keeps_update_mining(tmp_path, seen):
    _run(tmp_path, {"decisions": {"preset": "default"}})

    assert any(p is not None for _, p in seen)


def test_the_model_switch_still_wins_with_update_mining_on(tmp_path, seen):
    _run(tmp_path, {"decisions": {"update_mining": True, "llm": False}})

    assert all(p is None for _, p in seen)


def test_hook_firings_are_classified_without_the_session_lane(tmp_path, monkeypatch) -> None:
    """Classification reads the hooks' own output, no model: it runs for a default
    repository, but only when the ledger holds a firing still waiting for it."""
    from repowise.core.sessions import efficacy
    from repowise.core.sessions.staging import SessionStagingStore

    calls: list[Path] = []
    monkeypatch.setattr(
        efficacy, "ingest_transcript_efficacy", lambda repo, since: calls.append(repo) or {}
    )
    opts = SimpleNamespace(verbose=False)
    run = SimpleNamespace(repo_path=tmp_path, degraded=[])

    decision_passes._classify_hook_efficacy(opts, run)
    assert calls == []

    store = SessionStagingStore.open_default(tmp_path)
    store._conn.execute(
        "INSERT INTO injections (session_id, decision_id, shown_at, surface, category) "
        "VALUES ('s1', 'k', 1.0, 'upkeep', 'commit_notice')"
    )
    store.commit()
    store.close()

    decision_passes._classify_hook_efficacy(opts, run)
    assert calls == [tmp_path] and run.degraded == []
