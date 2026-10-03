"""Contract tests for direct_risks.temporal_hotspot — issue #1970.

``temporal_hotspot_score`` is an unbounded exponentially-decayed churn sum
(each commit contributes up to 3.0; 40% of files exceed 1.0 on this repo).
The scale contract must accurately describe that, not claim a 0-1 ratio.

These tests are pure-unit: ``structural_impact_contract`` is a dict-builder
with no I/O, so no fixtures, DB, or async are needed.
"""

from __future__ import annotations

from repowise.core.analysis.risk_semantics import structural_impact_contract


def _component_fields() -> dict:
    """Return the component_fields sub-dict from the full-scale contract.

    component_fields is a reference-tier key that compact_scale() intentionally
    strips; always read it from full_scale=True.
    """
    contract = structural_impact_contract(5.0, full_scale=True)
    scale = contract["structural_impact_scale"]
    return scale["component_fields"]


class TestTemporalHotspotContractKind:
    """direct_risks.temporal_hotspot must declare itself as raw decayed churn."""

    def test_kind_is_raw_decayed_churn(self) -> None:
        fields = _component_fields()
        assert fields["direct_risks.temporal_hotspot"]["kind"] == "raw_decayed_churn", (
            "temporal_hotspot is an unbounded decayed sum — it must not claim "
            "'normalized_component_score'"
        )

    def test_unit_is_decayed_line_hundreds(self) -> None:
        fields = _component_fields()
        assert fields["direct_risks.temporal_hotspot"]["unit"] == "decayed_line_hundreds"

    def test_maximum_is_none(self) -> None:
        """Range maximum must be None — the value is unbounded (max observed: 23.2)."""
        fields = _component_fields()
        rng = fields["direct_risks.temporal_hotspot"]["range"]
        assert rng["maximum"] is None, (
            f"temporal_hotspot maximum must be None (unbounded), got {rng['maximum']!r}"
        )

    def test_minimum_is_zero(self) -> None:
        fields = _component_fields()
        assert fields["direct_risks.temporal_hotspot"]["range"]["minimum"] == 0.0

    def test_note_is_present_and_mentions_churn_percentile(self) -> None:
        """A consumer note must exist and point to churn_percentile for normalisation."""
        fields = _component_fields()
        note = fields["direct_risks.temporal_hotspot"].get("note", "")
        assert note, "A consumer-facing note is required on this field"
        assert "churn_percentile" in note, (
            "Note must reference churn_percentile so consumers know the normalised alternative"
        )


class TestStructuralScoreContractUnchanged:
    """Regression guard: direct_risks.structural_score must stay unbounded.

    This field was already declared correctly; the fix must not touch it.
    """

    def test_structural_score_maximum_is_none(self) -> None:
        fields = _component_fields()
        rng = fields["direct_risks.structural_score"]["range"]
        assert rng["maximum"] is None


class TestContractConsistencyAcrossScoreTiers:
    """component_fields is a reference-tier key — compact_scale strips it.

    Assertions:
    1. The full scale carries the corrected contract.
    2. compact_scale correctly omits component_fields (that is the design,
       not a regression — so we assert it explicitly).
    """

    def test_kind_in_full_scale(self) -> None:
        fields = _component_fields()
        assert fields["direct_risks.temporal_hotspot"]["kind"] == "raw_decayed_churn"

    def test_maximum_in_full_scale(self) -> None:
        fields = _component_fields()
        assert fields["direct_risks.temporal_hotspot"]["range"]["maximum"] is None

    def test_compact_scale_omits_component_fields(self) -> None:
        """compact_scale() strips reference-tier keys — component_fields must not appear."""
        contract = structural_impact_contract(5.0, full_scale=False)
        scale = contract["structural_impact_scale"]
        assert "component_fields" not in scale, (
            "compact_scale must strip component_fields (it is a reference-tier key)"
        )


class TestPreviousContractWasWrong:
    """Explicitly assert that the old (broken) values are gone.

    These assertions will catch any future regression that re-introduces
    the false ratio claim.
    """

    def test_kind_is_not_normalized_component_score(self) -> None:
        fields = _component_fields()
        assert fields["direct_risks.temporal_hotspot"]["kind"] != "normalized_component_score", (
            "Regression: temporal_hotspot must not be declared as 'normalized_component_score' "
            "(issue #1970)"
        )

    def test_unit_is_not_ratio(self) -> None:
        fields = _component_fields()
        assert fields["direct_risks.temporal_hotspot"]["unit"] != "ratio"

    def test_maximum_is_not_1_0(self) -> None:
        fields = _component_fields()
        rng = fields["direct_risks.temporal_hotspot"]["range"]
        assert rng["maximum"] != 1.0, (
            "Regression: temporal_hotspot maximum must not be 1.0 — the value is unbounded "
            "(issue #1970)"
        )
