"""Machine inference may propose. Only a person accepts.

The two paths that let an inferred record reach governance without anyone
agreeing to it: the session miner's first-promotion write, and the alignment
read that counted proposals as things governing a file.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.sessions.miners.decisions import promotion_decisions
from repowise.server.mcp_server._helpers import _compute_alignment


def _row(**overrides):
    row = {
        "structured": {
            "decision": "Use one transaction for the full-text write",
            "rationale": "because per-row commits dominated the tail",
            "source_quote": "one transaction",
            "verification": "exact",
            "affected_files": [],
        },
        "title": "One transaction for the FTS write",
        "files": [],
        "observations": 4,
        "sessions": ["s1", "s2"],
        "first_promotion": True,
    }
    row.update(overrides)
    return row


class TestRecurrenceIsNotAcceptance:
    def test_first_promotion_lands_proposed(self):
        """Recurrence across sessions is evidence, not an acceptance event."""
        out = promotion_decisions(_row(first_promotion=True), Path("."))

        assert out
        assert {d.status for d in out} == {"proposed"}

    def test_re_emission_also_lands_proposed(self):
        out = promotion_decisions(_row(first_promotion=False), Path("."))

        assert {d.status for d in out} == {"proposed"}

    def test_no_observation_count_produces_an_active_record(self):
        for observations in (1, 2, 5, 50):
            out = promotion_decisions(_row(observations=observations), Path("."))
            assert all(d.status != "active" for d in out)


class TestOnlyAcceptedDecisionsGovern:
    def _decision(self, status: str, title: str = "d", staleness: float = 0.0) -> dict:
        return {"status": status, "title": title, "staleness_score": staleness}

    def test_proposals_alone_govern_nothing(self):
        result = _compute_alignment(
            "src/a.py", [self._decision("proposed"), self._decision("proposed", "d2")], []
        )

        assert result["score"] == "none"
        assert result["active_count"] == 0
        assert result["candidate_count"] == 2
        # governing_count keeps its old meaning: records naming this file.
        assert result["governing_count"] == 2

    def test_proposals_are_named_as_awaiting_review(self):
        result = _compute_alignment("src/a.py", [self._decision("proposed")], [])

        assert "awaiting review" in result["explanation"]
        assert "No accepted decision" in result["explanation"]

    def test_accepted_decisions_still_govern(self):
        result = _compute_alignment("src/a.py", [self._decision("active")], [])

        assert result["score"] == "high"
        assert result["active_count"] == 1
        assert result["candidate_count"] == 0

    def test_proposals_do_not_inflate_the_accepted_count(self):
        governing = [
            self._decision("active"),
            self._decision("proposed", "d2"),
            self._decision("proposed", "d3"),
        ]

        result = _compute_alignment("src/a.py", governing, [])

        assert result["active_count"] == 1
        assert result["candidate_count"] == 2
        assert result["governing_count"] == 3

    def test_proposals_cannot_rescue_an_all_deprecated_file(self):
        governing = [self._decision("deprecated"), self._decision("proposed", "d2")]

        result = _compute_alignment("src/a.py", governing, [])

        assert result["score"] == "low"
        assert "deprecated" in result["explanation"]

    def test_staleness_is_measured_over_accepted_records_only(self):
        governing = [
            self._decision("active", "d1", staleness=0.0),
            self._decision("proposed", "d2", staleness=1.0),
        ]

        result = _compute_alignment("src/a.py", governing, [])

        assert result["stale_count"] == 0
        assert result["score"] == "high"

    def test_a_majority_stale_accepted_set_still_scores_low(self):
        governing = [
            self._decision("active", "d1", staleness=0.9),
            self._decision("active", "d2", staleness=0.0),
        ]

        result = _compute_alignment("src/a.py", governing, [])

        assert result["score"] == "low"

    def test_ungoverned_shape_carries_the_candidate_count(self):
        result = _compute_alignment("src/a.py", [], [])

        assert result["candidate_count"] == 0
        assert result["active_count"] == 0
        assert result["governing_count"] == 0
