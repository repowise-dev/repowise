"""Phase 3C — two-pass evolution on ``repowise update``.

Pass 1 (cheap) cross-references the diff/commit evidence for evolution signals
against existing decisions governing the changed files; Pass 2 (gated LLM) asks
whether each survivor is amended / superseded / reaffirmed. Governed pages of an
evolved decision are returned for regeneration so they re-render in the same run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.core.analysis.decision_evolution import (
    contradicts,
    is_reversal,
    pass1_evolution_candidates,
    run_update_evolution,
    scan_evolution_signals,
    supersession_confidence,
)
from repowise.core.persistence.crud import bulk_upsert_decisions, get_decision
from tests.unit.persistence.helpers import accept, insert_repo

# ---------------------------------------------------------------------------
# Pure heuristics
# ---------------------------------------------------------------------------


def test_scan_evolution_signals():
    assert scan_evolution_signals("We migrate to Postgres because MySQL is slow") == [
        "because",
        "migrate",
    ]
    assert scan_evolution_signals("just a routine refactor") == []


def test_is_reversal():
    assert is_reversal("Replace sessions with JWT")[0] is True
    assert is_reversal("Adopt Redis for caching")[0] is False


def test_contradicts_requires_shared_topic():
    # Opposing verbs but unrelated topics → not a contradiction.
    assert contradicts("Drop the Redis cache", "Adopt gRPC transport")[0] is False
    # Opposing verbs on a shared topic → contradiction, on the default path.
    assert contradicts("Drop the Redis cache layer", "Adopt the Redis cache layer")[0] is True


def test_a_lone_reversal_signal_needs_a_caller_that_vouches_for_the_topic():
    """A real reversal the default declines, and the reason it declines it.

    "Replace sessions" against "Use sessions" is a true contradiction that no
    opposing verb pair straddles, so requiring two-sided evidence gives it up.
    That is a deliberate loss, taken because the one-sided branch is not
    selective enough to keep: over this repository's whole injection corpus it
    fired once, wrongly, and that firing is the layer's only published outcome
    number. Supersession keeps the branch by opting in, having already
    established the shared topic with a vector score.
    """
    pair = ("Replace sessions with JWT for auth", "Use sessions for auth")
    assert contradicts(*pair)[0] is False
    assert contradicts(*pair, lone_reversal_counts=True)[0] is True


def test_supersession_confidence_bounds_and_bumps():
    base = supersession_confidence(0.7)
    bumped = supersession_confidence(0.7, verified=True, has_reversal_signal=True)
    assert bumped > base
    assert supersession_confidence(2.0) <= 0.99  # clamped


# ---------------------------------------------------------------------------
# Pass 1 — pattern scan + governed-file cross-reference
# ---------------------------------------------------------------------------


def test_pass1_only_returns_decisions_with_signal_on_changed_governed_file():
    dec_hit = SimpleNamespace(affected_files_json=json.dumps(["src/db.py"]))
    dec_miss_file = SimpleNamespace(affected_files_json=json.dumps(["src/other.py"]))
    dec_no_signal = SimpleNamespace(affected_files_json=json.dumps(["src/api.py"]))

    candidates = pass1_evolution_candidates(
        changed_files={"src/db.py", "src/api.py"},
        evidence_by_file={
            "src/db.py": "feat: migrate from MySQL to Postgres because of JSONB",
            "src/api.py": "chore: tidy imports",  # no evolution signal
        },
        existing_decisions=[dec_hit, dec_miss_file, dec_no_signal],
    )
    assert len(candidates) == 1
    dec, evidence, signals = candidates[0]
    assert dec is dec_hit
    assert "migrate" in signals and "because" in signals
    assert "migrate from MySQL" in evidence


# ---------------------------------------------------------------------------
# Pass 2 — gated LLM judge + regen coupling
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, content: str) -> None:
        self._content = content

    async def generate(self, system: str, prompt: str, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(content=self._content)


async def _seed_decision(session, repo_id: str) -> str:
    ids = await bulk_upsert_decisions(
        session,
        repo_id,
        [
            {
                "title": "Use MySQL for storage",
                "decision": "use MySQL as the primary datastore",
                "rationale": "the team already ran MySQL in production",
                "source": "inline_marker",
                "status": "proposed",
                "affected_files": ["src/db.py"],
                "evidence_file": "src/db.py",
                "confidence": 0.6,
                "verification": "exact",
                "source_quote": "use MySQL",
            }
        ],
    )
    await accept(session, ids[0])
    return ids[0]


async def test_run_update_evolution_supersedes_and_queues_regen(async_session):
    repo = await insert_repo(async_session)
    did = await _seed_decision(async_session, repo.id)

    evidence = "feat: migrate from MySQL to Postgres because of JSONB. We replace MySQL."
    provider = _FakeProvider(
        json.dumps(
            {
                "verdict": "superseded",
                "rationale": "the change says we replace MySQL",
                "source_quote": "replace MySQL",  # verbatim substring of evidence
            }
        )
    )

    result = await run_update_evolution(
        async_session,
        repo.id,
        changed_files={"src/db.py"},
        evidence_by_file={"src/db.py": evidence},
        provider=provider,
    )

    assert result["superseded"] == 1
    assert "src/db.py" in result["regen_files"]
    assert (await get_decision(async_session, did)).status == "superseded"


async def test_run_update_evolution_ungrounded_verdict_rejected(async_session):
    repo = await insert_repo(async_session)
    did = await _seed_decision(async_session, repo.id)

    # The quote is NOT in the evidence → the gate drops it → verdict discarded.
    provider = _FakeProvider(
        json.dumps(
            {
                "verdict": "superseded",
                "rationale": "we switched to Cassandra",
                "source_quote": "adopt Cassandra cluster",
            }
        )
    )
    result = await run_update_evolution(
        async_session,
        repo.id,
        changed_files={"src/db.py"},
        evidence_by_file={"src/db.py": "feat: migrate from MySQL to Postgres"},
        provider=provider,
    )
    assert result["superseded"] == 0
    assert (await get_decision(async_session, did)).status == "active"


async def test_run_update_evolution_without_provider_queues_regen_only(async_session):
    repo = await insert_repo(async_session)
    did = await _seed_decision(async_session, repo.id)

    result = await run_update_evolution(
        async_session,
        repo.id,
        changed_files={"src/db.py"},
        evidence_by_file={"src/db.py": "feat: migrate from MySQL to Postgres"},
        provider=None,
    )
    # Pass-1 signal queues the governed page for regen, but no status change
    # without the LLM judge.
    assert "src/db.py" in result["regen_files"]
    assert result["superseded"] == 0
    assert (await get_decision(async_session, did)).status == "active"
