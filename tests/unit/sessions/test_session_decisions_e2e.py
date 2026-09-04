"""End-to-end session decision mining: transcript -> gates -> LLM -> promotion."""

from __future__ import annotations

import json

from repowise.core.analysis.decisions.provenance import SOURCE_RANK
from repowise.core.sessions.adapters.claude_code import transcript_dir_for
from repowise.core.sessions.miners.decisions import mine_session_decisions
from repowise.core.sessions.staging import SessionStagingStore, default_store_path

CORRECTION = "No, always run the unit suite with the venv python, bare python is a stale install"


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeProvider:
    """Echoes a grounded structuring result for every candidate it is shown."""

    def __init__(self, items_per_candidate: dict | None = None) -> None:
        self.calls: list[str] = []
        self._item = items_per_candidate or {
            "title": "Run tests with the venv python",
            "decision": "always run the unit suite with the venv python",
            "rationale": "bare python is a stale install",
            "affected_files": [],
            "source_quote": CORRECTION,
        }

    async def generate(self, system: str, prompt: str, **kw) -> FakeResponse:
        self.calls.append(prompt)
        count = prompt.count("--- Candidate ")
        items = [{"candidate": i, **self._item} for i in range(count)]
        return FakeResponse(json.dumps(items))


def _write_transcript(repo_root, projects_root, name: str, session_id: str, text: str) -> None:
    directory = transcript_dir_for(repo_root, projects_root)
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "type": "user",
        "cwd": str(repo_root),
        "timestamp": "2026-07-11T10:00:00.000Z",
        "sessionId": session_id,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    (directory / name).write_text(json.dumps(entry) + "\n", encoding="utf-8")


async def test_correction_mines_structures_and_promotes(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    projects_root = tmp_path / "projects"
    provider = FakeProvider()
    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)

    decisions = await mine_session_decisions(
        repo_root, provider=provider, projects_root=projects_root, now=100.0
    )

    (decision,) = decisions
    assert decision.source == "session"
    # A user correction promotes on one observation, but promotion means
    # "worth reviewing", not "accepted": only `decision confirm` sets active.
    assert decision.status == "proposed"
    assert decision.verification == "exact"
    assert decision.evidence_commits == ["sess-1"]
    assert decision.source_quote == CORRECTION
    assert 0 < decision.confidence < 1
    assert len(provider.calls) == 1

    # Second run: no new transcript lines -> no LLM call, nothing re-promoted.
    again = await mine_session_decisions(
        repo_root, provider=provider, projects_root=projects_root, now=200.0
    )
    assert again == []
    assert len(provider.calls) == 1


async def test_second_session_reemits_as_proposed_evidence(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    projects_root = tmp_path / "projects"
    provider = FakeProvider()
    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)
    await mine_session_decisions(
        repo_root, provider=provider, projects_root=projects_root, now=100.0
    )

    _write_transcript(
        repo_root, projects_root, "two.jsonl", "sess-2", CORRECTION + " and stays that way"
    )
    decisions = await mine_session_decisions(
        repo_root, provider=provider, projects_root=projects_root, now=200.0
    )
    assert decisions  # one member per observing session
    assert {d.status for d in decisions} == {"proposed"}
    assert {d.evidence_commits[0] for d in decisions} == {"sess-1", "sess-2"}


async def test_ungrounded_llm_output_is_rejected_not_retried(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    projects_root = tmp_path / "projects"
    provider = FakeProvider(
        items_per_candidate={
            "title": "Adopt Redis for caching",
            "decision": "migrate all caching to Redis",  # nowhere in the quotes
            "rationale": "",
            "affected_files": [],
            "source_quote": "",
        }
    )
    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)

    decisions = await mine_session_decisions(
        repo_root, provider=provider, projects_root=projects_root, now=100.0
    )
    assert decisions == []

    with SessionStagingStore(default_store_path(repo_root)) as store:
        assert store.pending_raws(10) == []  # rejected, not left for retry
        assert store.promotable() == []


async def test_llm_failure_leaves_candidates_staged(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    projects_root = tmp_path / "projects"

    class FailingProvider:
        async def generate(self, *a, **kw):
            raise RuntimeError("provider down")

    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)
    decisions = await mine_session_decisions(
        repo_root, provider=FailingProvider(), projects_root=projects_root, now=100.0
    )
    assert decisions == []

    with SessionStagingStore(default_store_path(repo_root)) as store:
        assert len(store.pending_raws(10)) == 1  # staged, retried next update

    # Retry with a working provider structures the backlog without re-reading
    # the transcript (the cursor already advanced past it).
    provider = FakeProvider()
    decisions = await mine_session_decisions(
        repo_root, provider=provider, projects_root=projects_root, now=200.0
    )
    (decision,) = decisions
    assert decision.status == "proposed"


async def test_init_pipeline_appends_session_decisions(tmp_path, monkeypatch):
    """The full-index decision phase folds mined session decisions in."""
    from types import SimpleNamespace

    from repowise.core.analysis.decisions.extractor import ExtractedDecision
    from repowise.core.pipeline.phases.analysis import _run_decision_extraction

    calls = []

    async def fake_mine(repo_path, *, provider, **kw):
        calls.append(repo_path)
        return [ExtractedDecision(title="Use X", source="session", status="active")]

    monkeypatch.setattr("repowise.core.sessions.miners.decisions.mine_session_decisions", fake_mine)

    class Graph:
        def graph(self):
            return None

    class Provider:
        async def generate(self, *a, **kw):
            return SimpleNamespace(content="[]")

    report = await _run_decision_extraction(
        tmp_path,
        llm_client=Provider(),
        graph_builder=Graph(),
        git_meta_map={},
        parsed_files=[],
        progress=None,
    )
    assert calls == [tmp_path]
    assert [d.title for d in report.decisions if d.source == "session"] == ["Use X"]
    assert report.by_source["session"] == 1
    assert report.total_found == len(report.decisions)


async def test_init_pipeline_respects_session_mining_gate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from repowise.core.pipeline.phases.analysis import _run_decision_extraction

    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "decisions:\n  session_mining: false\n", encoding="utf-8"
    )

    async def fake_mine(repo_path, *, provider, **kw):
        raise AssertionError("must not mine when the config gate is off")

    monkeypatch.setattr("repowise.core.sessions.miners.decisions.mine_session_decisions", fake_mine)

    class Graph:
        def graph(self):
            return None

    class Provider:
        async def generate(self, *a, **kw):
            return SimpleNamespace(content="[]")

    report = await _run_decision_extraction(
        tmp_path,
        llm_client=Provider(),
        graph_builder=Graph(),
        git_meta_map={},
        parsed_files=[],
        progress=None,
    )
    assert report is not None
    assert "session" not in report.by_source


def test_session_rank_sits_above_adr_and_below_cli():
    """A transcript is the user speaking; an ADR is a write-up of it.

    This used to assert ``adr > session``, which meant the ``>=`` promotion
    branch in ``crud.decisions`` never fired for a transcript and a mined
    document overwrote the user's own words.
    """
    assert SOURCE_RANK["session"] == 8
    assert SOURCE_RANK["cli"] > SOURCE_RANK["session"] > SOURCE_RANK["adr"]


# ---------------------------------------------------------------------------
# One read, two consumers
# ---------------------------------------------------------------------------


async def test_the_transcript_read_serves_episodes_without_a_provider(tmp_path):
    """Keyless is the primary path, and this pass is where transcripts are read.

    Gating the read on a provider would leave a user with no API key no
    transcript supply at all, while the episode half needs no model. The
    structuring pass is what skips.
    """
    from repowise.core.precedent.store import TIER_TRANSCRIPT, EpisodeStore
    from repowise.core.precedent.store import default_store_path as episode_store_path

    repo_root = tmp_path / "repo"
    (repo_root / ".repowise").mkdir(parents=True)
    projects_root = tmp_path / "projects"
    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)

    decisions = await mine_session_decisions(
        repo_root, provider=None, projects_root=projects_root, now=100.0
    )

    assert decisions == []  # nothing can be structured without a model
    with EpisodeStore(episode_store_path(repo_root)) as store:
        (row,) = store.list_episodes(tier=TIER_TRANSCRIPT)
    assert CORRECTION in row["body"]
    assert row["subject"].endswith("one.jsonl")


async def test_the_episode_write_leaves_decision_mining_unchanged(tmp_path):
    """The tee must not perturb the stream it rides.

    Measured on the real corpus as well: 426 transcripts produced 547 candidates
    with and without the recorder attached.
    """
    repo_root = tmp_path / "repo"
    (repo_root / ".repowise").mkdir(parents=True)
    projects_root = tmp_path / "projects"
    provider = FakeProvider()
    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)

    decisions = await mine_session_decisions(
        repo_root, provider=provider, projects_root=projects_root, now=100.0
    )

    (decision,) = decisions
    assert decision.source == "session"
    assert decision.source_quote == CORRECTION


async def test_a_repo_with_no_transcripts_derives_nothing_and_deletes_nothing(tmp_path):
    """Rule 3a's test: the CI case must be degraded, never broken."""
    from repowise.core.precedent.store import default_store_path as episode_store_path

    repo_root = tmp_path / "repo"
    (repo_root / ".repowise").mkdir(parents=True)
    projects_root = tmp_path / "projects"

    decisions = await mine_session_decisions(
        repo_root, provider=None, projects_root=projects_root, now=100.0
    )
    assert decisions == []
    assert not episode_store_path(repo_root).exists()


# ---------------------------------------------------------------------------
# Broad discovery rides the same read (one cursor, one pass over the bytes)
# ---------------------------------------------------------------------------


async def test_discovery_spans_are_collected_only_when_asked(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    projects_root = tmp_path / "projects"
    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)

    await mine_session_decisions(
        repo_root, provider=None, projects_root=projects_root, now=100.0
    )
    with SessionStagingStore(default_store_path(repo_root)) as store:
        assert store.pending_discovery_count() == 0


async def test_discovery_spans_ride_the_miners_single_transcript_read(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    projects_root = tmp_path / "projects"
    _write_transcript(repo_root, projects_root, "one.jsonl", "sess-1", CORRECTION)

    await mine_session_decisions(
        repo_root,
        provider=None,
        projects_root=projects_root,
        collect_discovery_spans=True,
        now=100.0,
    )
    with SessionStagingStore(default_store_path(repo_root)) as store:
        spans = store.pending_discovery_spans(10)
    assert [span["session_id"] for span in spans] == ["sess-1"]
    assert CORRECTION in spans[0]["text"]

    # The cursor already consumed those bytes, so a second pass adds nothing.
    await mine_session_decisions(
        repo_root,
        provider=None,
        projects_root=projects_root,
        collect_discovery_spans=True,
        now=200.0,
    )
    with SessionStagingStore(default_store_path(repo_root)) as store:
        assert store.pending_discovery_count() == 1


async def test_a_span_from_another_checkout_is_not_collected(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    projects_root = tmp_path / "projects"
    directory = transcript_dir_for(repo_root, projects_root)
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "type": "user",
        "cwd": str(other),
        "timestamp": "2026-07-11T10:00:00.000Z",
        "sessionId": "sess-elsewhere",
        "message": {"role": "user", "content": [{"type": "text", "text": CORRECTION}]},
    }
    (directory / "one.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

    await mine_session_decisions(
        repo_root,
        provider=None,
        projects_root=projects_root,
        collect_discovery_spans=True,
        now=100.0,
    )
    with SessionStagingStore(default_store_path(repo_root)) as store:
        assert store.pending_discovery_count() == 0
