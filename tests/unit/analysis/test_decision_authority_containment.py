"""Machine inference may propose. Only a person accepts.

The two paths that let an inferred record reach governance without anyone
agreeing to it: the session miner's first-promotion write, and the alignment
read that counted proposals as things governing a file.
"""

from __future__ import annotations

import json
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
    """Authority is the acceptance join, not the status column.

    Every case here passes a currency map instead of a status string, because
    that map is the whole test: a record absent from it has no acceptance row
    and is a candidate regardless of what ``decision_records.status`` says.
    """

    def _decision(self, did: str = "d1", title: str = "d") -> dict:
        return {"id": did, "title": title}

    def test_candidates_alone_govern_nothing(self):
        result = _compute_alignment(
            "src/a.py",
            [self._decision("d1"), self._decision("d2", "d2")],
            [],
            {},
        )

        assert result["score"] == "none"
        assert result["active_count"] == 0
        assert result["candidate_count"] == 2
        # governing_count keeps its old meaning: records naming this file.
        assert result["governing_count"] == 2

    def test_a_record_absent_from_the_currency_map_is_a_candidate(self):
        """Absence from the map is the whole test, whatever the column says.

        The end-to-end half of this, with a record actually stored active
        and carrying no acceptance row, is
        test_decisions_lanes.test_an_active_status_without_an_acceptance_is_a_candidate.
        This function never sees a status, which is the point: it cannot be
        fooled by one.
        """
        result = _compute_alignment("src/a.py", [self._decision()], [], {})

        assert result["score"] == "none"
        assert result["active_count"] == 0
        assert result["candidate_count"] == 1

    def test_candidates_are_named_as_awaiting_review(self):
        result = _compute_alignment("src/a.py", [self._decision()], [], {})

        assert "awaiting review" in result["explanation"]
        assert "No accepted decision" in result["explanation"]

    def test_accepted_decisions_govern(self):
        result = _compute_alignment(
            "src/a.py", [self._decision()], [], {"d1": "active"}
        )

        assert result["score"] == "high"
        assert result["active_count"] == 1
        assert result["candidate_count"] == 0

    def test_a_decision_whose_code_moved_still_governs(self):
        """``needs_review`` binds: it is a decision to re-read, not to ignore."""
        result = _compute_alignment(
            "src/a.py", [self._decision()], [], {"d1": "needs_review"}
        )

        assert result["active_count"] == 1
        assert result["stale_count"] == 1
        assert result["score"] == "low"

    def test_candidates_do_not_inflate_the_accepted_count(self):
        governing = [
            self._decision("d1"),
            self._decision("d2", "d2"),
            self._decision("d3", "d3"),
        ]

        result = _compute_alignment("src/a.py", governing, [], {"d1": "active"})

        assert result["active_count"] == 1
        assert result["candidate_count"] == 2
        assert result["governing_count"] == 3

    def test_candidates_cannot_rescue_a_withdrawn_file(self):
        governing = [self._decision("d1"), self._decision("d2", "d2")]

        result = _compute_alignment(
            "src/a.py", governing, [], {"d1": "superseded"}
        )

        assert result["score"] == "low"
        assert result["deprecated_count"] == 1
        assert result["candidate_count"] == 1

    def test_staleness_is_measured_over_accepted_records_only(self):
        governing = [self._decision("d1"), self._decision("d2", "d2")]

        result = _compute_alignment("src/a.py", governing, [], {"d1": "active"})

        assert result["stale_count"] == 0
        assert result["score"] == "high"

    def test_a_majority_stale_accepted_set_still_scores_low(self):
        governing = [self._decision("d1"), self._decision("d2", "d2")]

        result = _compute_alignment(
            "src/a.py", governing, [], {"d1": "needs_review", "d2": "active"}
        )

        assert result["score"] == "low"

    def test_the_four_lane_counts_add_up_to_the_records_naming_the_file(self):
        """No record naming the file may fall out of every count."""
        governing = [self._decision(f"d{i}", f"d{i}") for i in range(4)]

        result = _compute_alignment(
            "src/a.py",
            governing,
            [],
            {"d0": "active", "d1": "superseded", "d2": "uncheckable"},
        )

        assert result["active_count"] == 1
        assert result["deprecated_count"] == 1
        assert result["uncheckable_count"] == 1
        assert result["candidate_count"] == 1
        assert (
            result["active_count"]
            + result["deprecated_count"]
            + result["uncheckable_count"]
            + result["candidate_count"]
            == result["governing_count"]
        )

    def test_an_uncheckable_decision_governs_nothing(self):
        """Accepted, but it names nothing, so nothing can check it."""
        result = _compute_alignment(
            "src/a.py", [self._decision()], [], {"d1": "uncheckable"}
        )

        assert result["score"] == "none"
        assert result["active_count"] == 0
        assert result["uncheckable_count"] == 1

    def test_ungoverned_shape_carries_the_candidate_count(self):
        result = _compute_alignment("src/a.py", [], [], {})

        assert result["candidate_count"] == 0
        assert result["active_count"] == 0
        assert result["governing_count"] == 0


class TestSiblingCoverageCountsAcceptedRecordsOnly:
    """A pattern established only by candidates is not an established pattern."""

    class _Row:
        def __init__(self, did: str, title: str, files: list[str]):
            self.id = did
            self.title = title
            self.affected_files_json = json.dumps(files)

    def test_a_candidate_sibling_is_not_a_pattern(self):
        siblings = [self._Row("s1", "shared rule", ["src/b.py"])]

        result = _compute_alignment(
            "src/a.py",
            [{"id": "d1", "title": "shared rule"}],
            siblings,
            {"d1": "active"},
        )

        # No accepted sibling, so there is nothing to compare against and the
        # score falls back to "governed by one accepted decision".
        assert result["sibling_coverage"] is None
        assert result["score"] == "high"

    def test_an_accepted_sibling_sets_the_denominator(self):
        siblings = [
            self._Row("s1", "shared rule", ["src/b.py"]),
            self._Row("s2", "other rule", ["src/c.py"]),
        ]

        result = _compute_alignment(
            "src/a.py",
            [{"id": "d1", "title": "shared rule"}],
            siblings,
            {"d1": "active", "s1": "active", "s2": "active"},
        )

        assert result["sibling_coverage"] == 0.5
        assert result["score"] == "high"


class TestPromotionCarriesTheReviewLane:
    """``source`` is ``session`` for both lanes, so ``lane`` is what tells them apart."""

    def test_the_broad_lane_names_itself(self):
        out = promotion_decisions(_row(kind="session_discovery"), Path("."))

        assert {d.lane for d in out} == {"session_discovery"}

    def test_the_deterministic_lane_is_plain_session(self):
        out = promotion_decisions(_row(kind="user_correction"), Path("."))

        assert {d.lane for d in out} == {"session"}

    def test_a_bundled_candidate_carries_its_split_flag(self):
        row = _row(kind="session_discovery")
        row["structured"]["needs_split"] = True

        assert all(d.needs_split for d in promotion_decisions(row, Path(".")))
