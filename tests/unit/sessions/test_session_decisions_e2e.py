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
    assert decision.status == "active"  # correction fast path, first promotion
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
    assert decision.status == "active"


def test_session_rank_sits_between_adr_and_commit():
    assert SOURCE_RANK["session"] == 7
    assert SOURCE_RANK["adr"] > SOURCE_RANK["session"] > SOURCE_RANK["commit"]
